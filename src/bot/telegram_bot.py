import logging
from typing import Optional
from dataclasses import dataclass

from aiogram import Bot, Dispatcher, Router
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from ..database.models import Listing
from ..analyzers.price_analyzer import PriceAnalysis
from ..analyzers.urgency_detector import UrgencyAnalysis

logger = logging.getLogger(__name__)


@dataclass
class NotificationData:
    """Data for a notification message."""
    listing: Listing
    price_analysis: Optional[PriceAnalysis] = None
    urgency_analysis: Optional[UrgencyAnalysis] = None
    is_new: bool = True
    previous_price: Optional[float] = None


class TelegramBot:
    """
    Telegram bot for sending car deal notifications.
    Single recipient mode - sends to configured CHAT_ID.
    """
    
    def __init__(self, bot_token: str, chat_id: str):
        self.chat_id = chat_id
        self.bot = Bot(
            token=bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        self.dp = Dispatcher()
        self.router = Router()
        
        # Register handlers
        self._setup_handlers()
        self.dp.include_router(self.router)
    
    def _setup_handlers(self):
        """Setup command handlers."""
        
        @self.router.message(Command("start"))
        async def cmd_start(message: Message):
            await message.answer(
                "🚗 <b>Auto Deal Bot</b>\n\n"
                "I monitor car listings on list.am and myauto.ge "
                "for great deals on BMW, Mercedes, Audi, and Lexus.\n\n"
                "Commands:\n"
                "/status - Check bot status\n"
                "/stats - Show statistics"
            )
        
        @self.router.message(Command("status"))
        async def cmd_status(message: Message):
            await message.answer(
                "✅ Bot is running\n"
                "📡 Monitoring: list.am, myauto.ge\n"
                "🚗 Brands: BMW, Mercedes, Audi, Lexus\n"
                "📅 Year: 2020+\n"
                "💰 Max price: $20,000"
            )
        
        @self.router.message(Command("stats"))
        async def cmd_stats(message: Message):
            # This will be populated by main.py with actual stats
            await message.answer(
                "📊 <b>Statistics</b>\n\n"
                "Use /status to check bot status"
            )
    
    async def start_polling(self):
        """Start the bot polling."""
        logger.info("Starting Telegram bot polling...")
        await self.dp.start_polling(self.bot)
    
    async def stop(self):
        """Stop the bot."""
        await self.bot.session.close()
    
    async def send_notification(self, data: NotificationData) -> Optional[int]:
        """
        Send a notification about a car deal.
        Returns message_id if successful, None otherwise.
        """
        try:
            message = self._format_notification(data)
            
            # Send with photo if available
            if data.listing.image_url:
                try:
                    result = await self.bot.send_photo(
                        chat_id=self.chat_id,
                        photo=data.listing.image_url,
                        caption=message,
                    )
                    return result.message_id
                except Exception as e:
                    logger.warning(f"Failed to send photo, sending text only: {e}")
            
            # Send text only
            result = await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                disable_web_page_preview=False
            )
            return result.message_id
            
        except Exception as e:
            logger.error(f"Failed to send notification: {e}")
            return None
    
    def _format_notification(self, data: NotificationData) -> str:
        """Format notification message."""
        listing = data.listing
        
        # Header with emoji based on reason
        if data.urgency_analysis and data.urgency_analysis.is_urgent:
            header = "🔥 <b>URGENT DEAL!</b>"
        elif data.price_analysis and data.price_analysis.is_good_deal:
            header = "💰 <b>GOOD PRICE!</b>"
        else:
            header = "🚗 <b>New Listing</b>"
        
        # Car info
        car_info = f"<b>{listing.make}</b>"
        if listing.model:
            car_info += f" {listing.model}"
        if listing.year:
            car_info += f" ({listing.year})"
        
        # Price line
        price_line = f"💵 <b>${listing.price_usd:,.0f}</b>"
        if listing.currency_original and listing.currency_original != "USD":
            price_line += f" ({listing.price_original:,.0f} {listing.currency_original})"
        
        # Price change indicator
        if data.previous_price and data.previous_price != listing.price_usd:
            diff = listing.price_usd - data.previous_price
            if diff < 0:
                price_line += f" 📉 <i>-${abs(diff):,.0f}</i>"
            else:
                price_line += f" 📈 <i>+${diff:,.0f}</i>"
        
        # Details
        details = []
        if listing.mileage:
            details.append(f"📏 {listing.mileage:,} km")
        if listing.location:
            details.append(f"📍 {listing.location}")
        if listing.customs_cleared is not None:
            details.append("✅ Customs cleared" if listing.customs_cleared else "⚠️ Not cleared")
        
        details_line = " | ".join(details) if details else ""
        
        # Reason/analysis
        reasons = []
        if data.price_analysis and data.price_analysis.reason:
            reasons.append(data.price_analysis.reason)
        if data.urgency_analysis and data.urgency_analysis.reason:
            reasons.append(data.urgency_analysis.reason)
        
        reason_line = ""
        if reasons:
            reason_line = f"\n💡 <i>{'; '.join(reasons)}</i>"
        
        # Source
        source = f"🌐 {listing.source}"
        
        # Link
        link = f"\n🔗 <a href='{listing.url}'>View listing</a>"
        
        # Compose message
        lines = [
            header,
            "",
            car_info,
            price_line,
        ]
        
        if details_line:
            lines.append(details_line)
        
        if reason_line:
            lines.append(reason_line)
        
        lines.extend([
            "",
            source,
            link
        ])
        
        return "\n".join(lines)
    
    async def send_startup_message(self):
        """Send a message when bot starts."""
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=(
                    "🚀 <b>Auto Deal Bot Started!</b>\n\n"
                    "Monitoring:\n"
                    "• list.am (Armenia)\n"
                    "• myauto.ge (Georgia)\n\n"
                    "Searching for: BMW, Mercedes, Audi, Lexus\n"
                    "Year: 2020+\n"
                    "Max price: $20,000"
                )
            )
        except Exception as e:
            logger.error(f"Failed to send startup message: {e}")
    
    async def send_error_message(self, error: str):
        """Send an error notification."""
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=f"⚠️ <b>Bot Error</b>\n\n{error}"
            )
        except Exception as e:
            logger.error(f"Failed to send error message: {e}")
