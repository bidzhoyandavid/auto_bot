import logging
from typing import Optional, List, Tuple
from dataclasses import dataclass

from ..database.repository import Repository
from ..database.models import Listing

logger = logging.getLogger(__name__)


@dataclass
class PriceAnalysis:
    """Result of price analysis."""
    listing_id: int
    current_price: float
    
    # Market comparison
    market_average: Optional[float] = None
    percentile_20: Optional[float] = None
    deviation_percent: Optional[float] = None
    
    # Verdict
    is_good_deal: bool = False
    is_below_market: bool = False
    confidence: float = 0.0  # 0-1, based on sample size
    
    # Reasoning
    reason: Optional[str] = None


class PriceAnalyzer:
    """
    Analyzes car prices to determine if they're good deals.
    Uses percentile comparison and market averages.
    """
    
    # Thresholds
    GOOD_DEAL_PERCENTILE = 20  # Below 20th percentile is a good deal
    SIGNIFICANT_DISCOUNT_PERCENT = 15  # 15% below market avg is significant
    MIN_SAMPLES_FOR_CONFIDENCE = 5  # Minimum samples for high confidence
    
    def __init__(self, repository: Repository):
        self.repository = repository
    
    async def analyze(
        self, 
        listing: Listing,
        force_recalculate: bool = False
    ) -> PriceAnalysis:
        """
        Analyze if a listing's price is a good deal.
        
        Returns PriceAnalysis with verdict and reasoning.
        """
        analysis = PriceAnalysis(
            listing_id=listing.id,
            current_price=listing.price_usd
        )
        
        # Get market data
        percentile_20 = await self.repository.get_price_percentile(
            make=listing.make,
            year=listing.year,
            percentile=self.GOOD_DEAL_PERCENTILE
        )
        
        market_avg = await self.repository.get_average_price(
            make=listing.make,
            model=listing.model,
            year=listing.year
        )
        
        # Get sample size for confidence
        similar_listings = await self.repository.get_listings_by_make(
            make=listing.make,
            min_year=listing.year - 1 if listing.year else None
        )
        sample_size = len(similar_listings)
        
        # Calculate confidence based on sample size
        if sample_size >= self.MIN_SAMPLES_FOR_CONFIDENCE * 2:
            analysis.confidence = 1.0
        elif sample_size >= self.MIN_SAMPLES_FOR_CONFIDENCE:
            analysis.confidence = 0.7
        elif sample_size >= 3:
            analysis.confidence = 0.4
        else:
            analysis.confidence = 0.2
        
        analysis.market_average = market_avg
        analysis.percentile_20 = percentile_20
        
        # Analyze price
        reasons = []
        
        # Check against 20th percentile
        if percentile_20 and listing.price_usd < percentile_20:
            analysis.is_good_deal = True
            diff = percentile_20 - listing.price_usd
            reasons.append(f"${diff:,.0f} below P20 (${percentile_20:,.0f})")
        
        # Check against market average
        if market_avg and market_avg > 0:
            deviation = ((market_avg - listing.price_usd) / market_avg) * 100
            analysis.deviation_percent = deviation
            
            if deviation > self.SIGNIFICANT_DISCOUNT_PERCENT:
                analysis.is_below_market = True
                analysis.is_good_deal = True
                reasons.append(f"{deviation:.0f}% below market avg (${market_avg:,.0f})")
        
        # Set reason
        if reasons:
            analysis.reason = "; ".join(reasons)
        else:
            analysis.reason = "Price within normal market range"
        
        logger.debug(
            f"Price analysis for {listing.make} {listing.model}: "
            f"good_deal={analysis.is_good_deal}, reason={analysis.reason}"
        )
        
        return analysis
    
    async def get_market_stats(
        self, 
        make: str,
        model: Optional[str] = None,
        year: Optional[int] = None
    ) -> dict:
        """Get market statistics for a car type."""
        listings = await self.repository.get_listings_by_make(
            make=make,
            min_year=year - 1 if year else None
        )
        
        if model:
            listings = [l for l in listings if l.model and model.lower() in l.model.lower()]
        
        if not listings:
            return {
                "count": 0,
                "min_price": None,
                "max_price": None,
                "avg_price": None,
                "median_price": None
            }
        
        prices = sorted([l.price_usd for l in listings])
        
        return {
            "count": len(prices),
            "min_price": min(prices),
            "max_price": max(prices),
            "avg_price": sum(prices) / len(prices),
            "median_price": prices[len(prices) // 2],
            "percentile_20": prices[int(len(prices) * 0.2)] if len(prices) >= 5 else None,
        }
