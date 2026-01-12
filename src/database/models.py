from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text, Index
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime

Base = declarative_base()


class Listing(Base):
    """Car listing from any source."""
    __tablename__ = "listings"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(50), nullable=False)  # 'list.am' or 'myauto.ge'
    listing_id = Column(String(100), nullable=False)  # Original ID from source
    url = Column(String(500), nullable=False)
    
    # Car details
    make = Column(String(100), nullable=False)  # BMW, Mercedes, etc.
    model = Column(String(100), nullable=True)
    year = Column(Integer, nullable=True)
    mileage = Column(Integer, nullable=True)  # In km
    
    # Price
    price_usd = Column(Float, nullable=False)
    price_original = Column(Float, nullable=True)
    currency_original = Column(String(10), nullable=True)  # AMD, GEL, USD
    
    # Additional info
    title = Column(String(500), nullable=True)
    description = Column(Text, nullable=True)
    location = Column(String(200), nullable=True)
    image_url = Column(String(500), nullable=True)
    
    # Flags
    is_urgent = Column(Boolean, default=False)
    customs_cleared = Column(Boolean, nullable=True)  # For myauto.ge
    
    # Timestamps
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    price_history = relationship("PriceHistory", back_populates="listing", cascade="all, delete-orphan")
    notifications = relationship("SentNotification", back_populates="listing", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index('idx_source_listing_id', 'source', 'listing_id', unique=True),
        Index('idx_make_year', 'make', 'year'),
        Index('idx_price_usd', 'price_usd'),
    )
    
    def __repr__(self):
        return f"<Listing {self.source}:{self.listing_id} {self.make} {self.model} {self.year} ${self.price_usd}>"


class PriceHistory(Base):
    """Track price changes for listings."""
    __tablename__ = "price_history"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    listing_id = Column(Integer, ForeignKey("listings.id", ondelete="CASCADE"), nullable=False)
    price_usd = Column(Float, nullable=False)
    price_original = Column(Float, nullable=True)
    currency_original = Column(String(10), nullable=True)
    recorded_at = Column(DateTime, default=datetime.utcnow)
    
    listing = relationship("Listing", back_populates="price_history")
    
    __table_args__ = (
        Index('idx_listing_recorded', 'listing_id', 'recorded_at'),
    )
    
    def __repr__(self):
        return f"<PriceHistory listing={self.listing_id} ${self.price_usd} at {self.recorded_at}>"


class SentNotification(Base):
    """Track sent Telegram notifications to avoid duplicates."""
    __tablename__ = "sent_notifications"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    listing_id = Column(Integer, ForeignKey("listings.id", ondelete="CASCADE"), nullable=False)
    sent_at = Column(DateTime, default=datetime.utcnow)
    reason = Column(String(100), nullable=True)  # 'good_price', 'urgent', 'price_drop'
    message_id = Column(Integer, nullable=True)  # Telegram message ID
    
    listing = relationship("Listing", back_populates="notifications")
    
    __table_args__ = (
        Index('idx_listing_sent', 'listing_id', 'sent_at'),
    )
    
    def __repr__(self):
        return f"<SentNotification listing={self.listing_id} reason={self.reason}>"
