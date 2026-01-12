"""
Auto Deal Bot - Main Entry Point

Monitors car listings on list.am for good deals
on BMW, Mercedes, Audi, and Lexus (2020+, under $20K).
Sends notifications via Telegram.
"""

import os
import asyncio
import logging

from aiohttp import web
import sys
from datetime import datetime
from typing import List

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .config import get_settings, Settings
from .database.repository import Repository
from .proxy_manager import ProxyManager
from .scrapers.list_am import ListAmScraper
from .scrapers.base import CarListing
from .analyzers.price_analyzer import PriceAnalyzer
from .analyzers.urgency_detector import UrgencyDetector
from .bot.telegram_bot import TelegramBot, NotificationData

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("auto_bot.log", encoding="utf-8")
    ]
)

logger = logging.getLogger(__name__)

async def health_check(request):
    """Health check endpoint for Render."""
    return web.Response(text="Bot is running")

async def start_web_server():
    """Start simple web server for Render health checks."""
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.environ.get('PORT', 8080)))
    await site.start()
    logger.info("Web server started on port 8080")


class AutoDealBot:
    """Main bot orchestrator."""
    
    def __init__(self, settings: Settings):
        self.settings = settings
        
        # Components
        self.repository = Repository(settings.database_url)
        self.proxy_manager = ProxyManager(
            min_pool_size=settings.min_proxy_pool_size,
            refresh_interval_minutes=settings.proxy_refresh_minutes
        )
        self.price_analyzer = PriceAnalyzer(self.repository)
        self.urgency_detector = UrgencyDetector(self.repository)
        self.telegram_bot = TelegramBot(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id
        )
        
        # Scheduler
        self.scheduler = AsyncIOScheduler()
        
        # State
        self._is_running = False
        self._scrape_count = 0
        self._notifications_sent = 0
    
    async def start(self):
        """Initialize and start the bot."""
        logger.info("=" * 50)
        logger.info("Starting Auto Deal Bot")
        logger.info("=" * 50)
        
        # Initialize database
        await self.repository.init_db()
        # Start web server for Render
        asyncio.create_task(start_web_server())
        logger.info("Database initialized")
        
        # Initialize proxy manager
        logger.info("Initializing proxy manager...")
        await self.proxy_manager.initialize()
        logger.info(f"Proxy pool size: {self.proxy_manager.pool_size}")
        
        # Send startup message
        await self.telegram_bot.send_startup_message()
        
        # Schedule scraping job
        self.scheduler.add_job(
            self.scrape_and_notify,
            trigger=IntervalTrigger(minutes=self.settings.scrape_interval_minutes),
            id="scrape_job",
            name="Scrape car listings",
            next_run_time=datetime.now()  # Run immediately on start
        )
        
        # Schedule proxy refresh
        self.scheduler.add_job(
            self.proxy_manager.refresh_proxies,
            trigger=IntervalTrigger(minutes=self.settings.proxy_refresh_minutes),
            id="proxy_refresh",
            name="Refresh proxy pool"
        )
        
        self.scheduler.start()
        self._is_running = True
        
        logger.info(f"Scheduler started. Scraping every {self.settings.scrape_interval_minutes} minutes")
        
        # Keep running
        try:
            while self._is_running:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            await self.stop()
    
    async def stop(self):
        """Stop the bot gracefully."""
        logger.info("Stopping Auto Deal Bot...")
        self._is_running = False
        
        self.scheduler.shutdown(wait=False)
        await self.telegram_bot.stop()
        await self.repository.close()
        
        logger.info("Bot stopped")
    
    async def scrape_and_notify(self):
        """Main scraping and notification job."""
        self._scrape_count += 1
        logger.info(f"Starting scrape job #{self._scrape_count}")
        
        start_time = datetime.now()
        all_listings: List[CarListing] = []
        
        # Scrape list.am
        try:
            async with ListAmScraper(
                proxy_manager=self.proxy_manager,
                delay_min=self.settings.request_delay_min,
                delay_max=self.settings.request_delay_max
            ) as scraper:
                listings = await scraper.scrape_listings(
                    brands=self.settings.target_brands,
                    min_year=self.settings.min_year,
                    max_price_usd=self.settings.max_price_usd,
                    max_pages=3
                )
                all_listings.extend(listings)
                logger.info(f"list.am: Found {len(listings)} listings")
        except Exception as e:
            logger.error(f"Error scraping list.am: {e}")
        
        logger.info(f"Total listings found: {len(all_listings)}")
        
        # Process listings
        notifications_to_send = []
        
        for car_listing in all_listings:
            try:
                # Save to database
                listing, is_new, previous_price = await self.repository.upsert_listing(
                    car_listing.to_dict()
                )
                
                # Check if we already sent notification recently
                if await self.repository.was_notification_sent(listing.id, hours=24):
                    continue
                
                # Analyze price
                price_analysis = await self.price_analyzer.analyze(listing)
                
                # Analyze urgency
                urgency_analysis = await self.urgency_detector.analyze(listing)
                
                # Determine if we should notify
                should_notify = False
                reason = None
                
                # Send ALL new listings (important when database is empty)
                if is_new:
                    should_notify = True
                    reason = "new_listing"
                    
                    # Add special labels if applicable
                    if price_analysis.is_good_deal:
                        reason = "good_price"
                    elif urgency_analysis.is_urgent:
                        reason = "urgent"
                        
                elif not is_new and previous_price:
                    # Check for significant price drop
                    drop_percent = ((previous_price - listing.price_usd) / previous_price) * 100
                    if drop_percent >= 5:
                        should_notify = True
                        reason = "price_drop"
                
                if should_notify:
                    notifications_to_send.append(NotificationData(
                        listing=listing,
                        price_analysis=price_analysis,
                        urgency_analysis=urgency_analysis,
                        is_new=is_new,
                        previous_price=previous_price
                    ))
                
            except Exception as e:
                logger.error(f"Error processing listing {car_listing.listing_id}: {e}")
        
        # Send notifications
        for notification in notifications_to_send:
            try:
                message_id = await self.telegram_bot.send_notification(notification)
                
                if message_id:
                    # Record notification
                    reason = "new_listing"
                    if notification.price_analysis and notification.price_analysis.is_good_deal:
                        reason = "good_price"
                    if notification.urgency_analysis and notification.urgency_analysis.is_urgent:
                        reason = "urgent"
                    if notification.previous_price:
                        reason = "price_drop"
                    
                    await self.repository.record_notification(
                        listing_db_id=notification.listing.id,
                        reason=reason,
                        message_id=message_id
                    )
                    self._notifications_sent += 1
                    logger.info(f"Sent notification for {notification.listing.make} {notification.listing.model}")
                
                # Small delay between notifications
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"Error sending notification: {e}")
        
        # Log summary
        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(
            f"Scrape job #{self._scrape_count} completed in {elapsed:.1f}s. "
            f"Processed: {len(all_listings)}, Notifications: {len(notifications_to_send)}"
        )


async def main():
    """Main entry point."""
    try:
        settings = get_settings()
    except Exception as e:
        print(f"Error loading settings: {e}")
        print("Make sure you have a .env file with TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
        sys.exit(1)
    
    bot = AutoDealBot(settings)
    
    try:
        await bot.start()
    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
    finally:
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
