import re
import logging
from typing import Optional, List, Set
from dataclasses import dataclass

from ..database.repository import Repository
from ..database.models import Listing
from ..config import URGENCY_KEYWORDS

logger = logging.getLogger(__name__)


@dataclass
class UrgencyAnalysis:
    """Result of urgency detection."""
    listing_id: int
    is_urgent: bool = False
    
    # Detection sources
    has_urgent_keywords: bool = False
    has_price_drop: bool = False
    price_drop_percent: Optional[float] = None
    
    # Details
    detected_keywords: List[str] = None
    reason: Optional[str] = None
    urgency_score: float = 0.0  # 0-1
    
    def __post_init__(self):
        if self.detected_keywords is None:
            self.detected_keywords = []


class UrgencyDetector:
    """
    Detects urgency signals in car listings.
    Uses keyword matching and price drop tracking.
    """
    
    # Price drop thresholds
    SIGNIFICANT_DROP_PERCENT = 5  # 5% drop is significant
    MAJOR_DROP_PERCENT = 10  # 10% drop is major
    
    # Pattern indicators
    URGENCY_PATTERNS = [
        r"!!!+",  # Multiple exclamation marks
        r"СРОЧНО",  # Caps lock URGENT in Russian
        r"URGENT",  # Caps lock URGENT
        r"\bасап\b",  # ASAP
        r"\basap\b",
    ]
    
    def __init__(self, repository: Repository):
        self.repository = repository
        
        # Compile keyword patterns
        self._keyword_patterns = self._compile_keywords()
    
    def _compile_keywords(self) -> List[re.Pattern]:
        """Compile all urgency keywords into regex patterns."""
        patterns = []
        
        # Add keywords from config
        for lang, keywords in URGENCY_KEYWORDS.items():
            for keyword in keywords:
                # Create word-boundary pattern for multi-word phrases
                if " " in keyword:
                    pattern = re.compile(re.escape(keyword), re.IGNORECASE)
                else:
                    pattern = re.compile(rf"\b{re.escape(keyword)}\b", re.IGNORECASE)
                patterns.append(pattern)
        
        # Add additional patterns
        for pattern_str in self.URGENCY_PATTERNS:
            patterns.append(re.compile(pattern_str, re.IGNORECASE))
        
        return patterns
    
    async def analyze(
        self,
        listing: Listing,
        check_price_history: bool = True
    ) -> UrgencyAnalysis:
        """
        Analyze a listing for urgency signals.
        
        Checks:
        1. Keywords in title/description
        2. Price drops compared to history
        3. Patterns (!!!, CAPS, etc.)
        """
        analysis = UrgencyAnalysis(listing_id=listing.id)
        
        # Check keywords in text
        text_to_check = f"{listing.title or ''} {listing.description or ''}".strip()
        
        if text_to_check:
            detected = self._detect_keywords(text_to_check)
            if detected:
                analysis.has_urgent_keywords = True
                analysis.detected_keywords = list(detected)
                analysis.urgency_score += 0.5
        
        # Check if listing was already marked as urgent
        if listing.is_urgent:
            analysis.has_urgent_keywords = True
            analysis.urgency_score += 0.3
        
        # Check price history for drops
        if check_price_history:
            price_drop = await self.repository.get_price_drop(listing.id)
            
            if price_drop is not None and price_drop < -self.SIGNIFICANT_DROP_PERCENT:
                analysis.has_price_drop = True
                analysis.price_drop_percent = abs(price_drop)
                
                if price_drop < -self.MAJOR_DROP_PERCENT:
                    analysis.urgency_score += 0.4
                else:
                    analysis.urgency_score += 0.2
        
        # Determine final urgency
        analysis.is_urgent = analysis.urgency_score >= 0.3
        
        # Build reason string
        reasons = []
        if analysis.has_urgent_keywords:
            if analysis.detected_keywords:
                reasons.append(f"Keywords: {', '.join(analysis.detected_keywords[:3])}")
            else:
                reasons.append("Marked as urgent")
        
        if analysis.has_price_drop:
            reasons.append(f"Price dropped {analysis.price_drop_percent:.1f}%")
        
        analysis.reason = "; ".join(reasons) if reasons else None
        
        logger.debug(
            f"Urgency analysis for listing {listing.id}: "
            f"urgent={analysis.is_urgent}, score={analysis.urgency_score:.2f}, "
            f"reason={analysis.reason}"
        )
        
        return analysis
    
    def _detect_keywords(self, text: str) -> Set[str]:
        """Detect urgency keywords in text."""
        detected = set()
        
        for pattern in self._keyword_patterns:
            matches = pattern.findall(text)
            for match in matches:
                # Normalize the match
                detected.add(match.lower().strip())
        
        return detected
    
    def check_text_urgency(self, text: str) -> tuple[bool, List[str]]:
        """
        Quick check for urgency in text without database lookup.
        Returns (is_urgent, detected_keywords).
        """
        detected = self._detect_keywords(text)
        return bool(detected), list(detected)
