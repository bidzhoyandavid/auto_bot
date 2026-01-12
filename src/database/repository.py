from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select, func, and_
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from typing import Optional, List
from datetime import datetime, timedelta
import logging

from .models import Base, Listing, PriceHistory, SentNotification

logger = logging.getLogger(__name__)


class Repository:
    """Database repository for all operations."""
    
    def __init__(self, database_url: str = "sqlite+aiosqlite:///auto_bot.db"):
        self.engine = create_async_engine(database_url, echo=False)
        self.async_session = async_sessionmaker(
            self.engine, 
            class_=AsyncSession, 
            expire_on_commit=False
        )
    
    async def init_db(self):
        """Create all tables."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database initialized")
    
    async def close(self):
        """Close database connection."""
        await self.engine.dispose()
    
    # ==================== Listing Operations ====================
    
    async def upsert_listing(self, listing_data: dict) -> tuple[Listing, bool, Optional[float]]:
        """
        Insert or update a listing.
        Returns: (listing, is_new, previous_price)
        """
        async with self.async_session() as session:
            # Check if listing exists
            result = await session.execute(
                select(Listing).where(
                    and_(
                        Listing.source == listing_data["source"],
                        Listing.listing_id == listing_data["listing_id"]
                    )
                )
            )
            existing = result.scalar_one_or_none()
            
            if existing:
                # Update existing listing
                previous_price = existing.price_usd
                for key, value in listing_data.items():
                    if hasattr(existing, key) and value is not None:
                        setattr(existing, key, value)
                existing.last_seen = datetime.utcnow()
                
                # Record price if changed
                if previous_price != listing_data.get("price_usd"):
                    price_record = PriceHistory(
                        listing_id=existing.id,
                        price_usd=listing_data["price_usd"],
                        price_original=listing_data.get("price_original"),
                        currency_original=listing_data.get("currency_original")
                    )
                    session.add(price_record)
                
                await session.commit()
                return existing, False, previous_price
            else:
                # Create new listing
                listing = Listing(**listing_data)
                session.add(listing)
                await session.commit()
                await session.refresh(listing)
                
                # Record initial price
                price_record = PriceHistory(
                    listing_id=listing.id,
                    price_usd=listing.price_usd,
                    price_original=listing.price_original,
                    currency_original=listing.currency_original
                )
                session.add(price_record)
                await session.commit()
                
                return listing, True, None
    
    async def get_listing(self, source: str, listing_id: str) -> Optional[Listing]:
        """Get a specific listing."""
        async with self.async_session() as session:
            result = await session.execute(
                select(Listing).where(
                    and_(
                        Listing.source == source,
                        Listing.listing_id == listing_id
                    )
                )
            )
            return result.scalar_one_or_none()
    
    async def get_listings_by_make(
        self, 
        make: str, 
        min_year: Optional[int] = None,
        max_price: Optional[float] = None
    ) -> List[Listing]:
        """Get listings filtered by make and optionally year/price."""
        async with self.async_session() as session:
            query = select(Listing).where(Listing.make.ilike(f"%{make}%"))
            
            if min_year:
                query = query.where(Listing.year >= min_year)
            if max_price:
                query = query.where(Listing.price_usd <= max_price)
            
            result = await session.execute(query)
            return list(result.scalars().all())
    
    # ==================== Price Analysis ====================
    
    async def get_price_percentile(
        self, 
        make: str, 
        year: Optional[int] = None,
        percentile: int = 20
    ) -> Optional[float]:
        """
        Get price at given percentile for similar cars.
        Used to determine if a price is 'good'.
        """
        async with self.async_session() as session:
            query = select(Listing.price_usd).where(
                Listing.make.ilike(f"%{make}%")
            )
            
            if year:
                # Include cars within 1 year range
                query = query.where(
                    and_(
                        Listing.year >= year - 1,
                        Listing.year <= year + 1
                    )
                )
            
            result = await session.execute(query)
            prices = [row[0] for row in result.fetchall()]
            
            if not prices or len(prices) < 3:
                return None
            
            prices.sort()
            idx = int(len(prices) * percentile / 100)
            return prices[idx]
    
    async def get_average_price(
        self, 
        make: str, 
        model: Optional[str] = None,
        year: Optional[int] = None
    ) -> Optional[float]:
        """Get average price for similar cars."""
        async with self.async_session() as session:
            query = select(func.avg(Listing.price_usd)).where(
                Listing.make.ilike(f"%{make}%")
            )
            
            if model:
                query = query.where(Listing.model.ilike(f"%{model}%"))
            if year:
                query = query.where(
                    and_(
                        Listing.year >= year - 1,
                        Listing.year <= year + 1
                    )
                )
            
            result = await session.execute(query)
            return result.scalar_one_or_none()
    
    # ==================== Price History ====================
    
    async def get_price_history(self, listing_db_id: int) -> List[PriceHistory]:
        """Get price history for a listing."""
        async with self.async_session() as session:
            result = await session.execute(
                select(PriceHistory)
                .where(PriceHistory.listing_id == listing_db_id)
                .order_by(PriceHistory.recorded_at.desc())
            )
            return list(result.scalars().all())
    
    async def get_price_drop(self, listing_db_id: int) -> Optional[float]:
        """
        Get price drop percentage if any.
        Returns negative percentage if price dropped.
        """
        history = await self.get_price_history(listing_db_id)
        if len(history) < 2:
            return None
        
        current = history[0].price_usd
        previous = history[1].price_usd
        
        if previous == 0:
            return None
        
        return ((current - previous) / previous) * 100
    
    # ==================== Notifications ====================
    
    async def was_notification_sent(
        self, 
        listing_db_id: int, 
        hours: int = 24
    ) -> bool:
        """Check if notification was sent for this listing recently."""
        async with self.async_session() as session:
            cutoff = datetime.utcnow() - timedelta(hours=hours)
            result = await session.execute(
                select(SentNotification).where(
                    and_(
                        SentNotification.listing_id == listing_db_id,
                        SentNotification.sent_at >= cutoff
                    )
                )
            )
            return result.scalar_one_or_none() is not None
    
    async def record_notification(
        self, 
        listing_db_id: int, 
        reason: str,
        message_id: Optional[int] = None
    ):
        """Record that a notification was sent."""
        async with self.async_session() as session:
            notification = SentNotification(
                listing_id=listing_db_id,
                reason=reason,
                message_id=message_id
            )
            session.add(notification)
            await session.commit()
    
    # ==================== Statistics ====================
    
    async def get_stats(self) -> dict:
        """Get database statistics."""
        async with self.async_session() as session:
            listings_count = await session.execute(select(func.count(Listing.id)))
            notifications_count = await session.execute(select(func.count(SentNotification.id)))
            
            # Listings by source
            by_source = await session.execute(
                select(Listing.source, func.count(Listing.id))
                .group_by(Listing.source)
            )
            
            # Listings by make
            by_make = await session.execute(
                select(Listing.make, func.count(Listing.id))
                .group_by(Listing.make)
            )
            
            return {
                "total_listings": listings_count.scalar_one(),
                "total_notifications": notifications_count.scalar_one(),
                "by_source": dict(by_source.fetchall()),
                "by_make": dict(by_make.fetchall())
            }
