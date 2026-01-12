from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime
import asyncio
import random
import logging

from playwright.async_api import async_playwright, Browser, Page, Playwright
from playwright_stealth import Stealth

from ..proxy_manager import ProxyManager

logger = logging.getLogger(__name__)


@dataclass
class CarListing:
    """Parsed car listing data."""
    source: str
    listing_id: str
    url: str
    make: str
    
    model: Optional[str] = None
    year: Optional[int] = None
    mileage: Optional[int] = None  # km
    
    price_usd: float = 0.0
    price_original: Optional[float] = None
    currency_original: Optional[str] = None
    
    title: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None
    image_url: Optional[str] = None
    
    is_urgent: bool = False
    customs_cleared: Optional[bool] = None
    
    raw_data: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        """Convert to dictionary for database."""
        return {
            "source": self.source,
            "listing_id": self.listing_id,
            "url": self.url,
            "make": self.make,
            "model": self.model,
            "year": self.year,
            "mileage": self.mileage,
            "price_usd": self.price_usd,
            "price_original": self.price_original,
            "currency_original": self.currency_original,
            "title": self.title,
            "description": self.description,
            "location": self.location,
            "image_url": self.image_url,
            "is_urgent": self.is_urgent,
            "customs_cleared": self.customs_cleared,
        }


class BaseScraper(ABC):
    """
    Base class for all car listing scrapers.
    Uses Playwright with stealth for anti-detection.
    """
    
    SOURCE_NAME: str = "base"
    BASE_URL: str = ""
    
    # User agents for rotation
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    ]
    
    def __init__(
        self,
        proxy_manager: Optional[ProxyManager] = None,
        delay_min: int = 5,
        delay_max: int = 15,
        max_retries: int = 3
    ):
        self.proxy_manager = proxy_manager
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.max_retries = max_retries
        
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
    
    async def __aenter__(self):
        await self.start()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()
    
    async def start(self):
        """Start the browser."""
        self._playwright = await async_playwright().start()
        await self._launch_browser()
    
    async def stop(self):
        """Stop the browser and cleanup."""
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
    
    async def _launch_browser(self, proxy_url: Optional[str] = None):
        """Launch browser with optional proxy."""
        launch_options = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ]
        }
        
        if proxy_url:
            launch_options["proxy"] = {"server": proxy_url}
        
        self._browser = await self._playwright.chromium.launch(**launch_options)
    
    async def _create_page(self) -> Page:
        """Create a new page with stealth settings."""
        # Apply stealth to browser context
        stealth = Stealth()
        context = await self._browser.new_context(
            user_agent=random.choice(self.USER_AGENTS),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )
        
        page = await context.new_page()
        await stealth.apply_stealth_async(page)
        
        return page
    
    async def _random_delay(self):
        """Add random delay between requests."""
        delay = random.uniform(self.delay_min, self.delay_max)
        logger.debug(f"Waiting {delay:.1f} seconds...")
        await asyncio.sleep(delay)
    
    async def _fetch_page(
        self, 
        url: str, 
        wait_selector: Optional[str] = None,
        timeout: int = 30000
    ) -> Optional[Page]:
        """
        Fetch a page with retry logic.
        Returns Page object or None on failure.
        """
        last_error = None
        current_proxy = None
        
        for attempt in range(self.max_retries):
            try:
                # Get proxy if available
                if self.proxy_manager and attempt > 0:
                    # Try with proxy on retry
                    current_proxy = await self.proxy_manager.get_proxy()
                    if current_proxy:
                        logger.info(f"Retrying with proxy: {current_proxy}")
                        await self._browser.close()
                        await self._launch_browser(current_proxy)
                
                page = await self._create_page()
                
                logger.info(f"Fetching: {url} (attempt {attempt + 1}/{self.max_retries})")
                
                response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                
                if response and response.status == 200:
                    # Wait for specific selector if provided
                    if wait_selector:
                        await page.wait_for_selector(wait_selector, timeout=timeout)
                    
                    # Mark proxy as successful
                    if current_proxy and self.proxy_manager:
                        self.proxy_manager.mark_proxy_success(current_proxy)
                    
                    return page
                
                logger.warning(f"Got status {response.status if response else 'None'} for {url}")
                await page.close()
                
            except Exception as e:
                last_error = e
                logger.warning(f"Attempt {attempt + 1} failed: {e}")
                
                # Mark proxy as failed
                if current_proxy and self.proxy_manager:
                    self.proxy_manager.mark_proxy_failed(current_proxy)
            
            # Wait before retry
            if attempt < self.max_retries - 1:
                await asyncio.sleep(2 ** attempt)  # Exponential backoff
        
        logger.error(f"All attempts failed for {url}: {last_error}")
        return None
    
    @abstractmethod
    async def build_search_url(
        self,
        brands: List[str],
        min_year: int,
        max_price_usd: int
    ) -> str:
        """Build search URL with filters."""
        pass
    
    @abstractmethod
    async def scrape_listings(
        self,
        brands: List[str],
        min_year: int,
        max_price_usd: int,
        max_pages: int = 5
    ) -> List[CarListing]:
        """Scrape listings from the site."""
        pass
    
    @abstractmethod
    async def parse_listing_element(self, element: Any) -> Optional[CarListing]:
        """Parse a single listing element."""
        pass
    
    def _normalize_make(self, make: str) -> str:
        """Normalize car make name."""
        make = make.strip().lower()
        
        mappings = {
            "mercedes-benz": "Mercedes",
            "mercedes benz": "Mercedes",
            "mercedes": "Mercedes",
            "bmw": "BMW",
            "audi": "Audi",
            "lexus": "Lexus",
        }
        
        return mappings.get(make, make.title())
    
    def _parse_price(self, price_str: str) -> tuple[float, str]:
        """
        Parse price string to (amount, currency).
        Returns (0.0, 'USD') if parsing fails.
        """
        import re
        
        price_str = price_str.strip()
        
        # Detect currency
        currency = "USD"
        if "֏" in price_str or "AMD" in price_str.upper():
            currency = "AMD"
        elif "₾" in price_str or "GEL" in price_str.upper():
            currency = "GEL"
        elif "$" in price_str or "USD" in price_str.upper():
            currency = "USD"
        
        # Extract number
        numbers = re.findall(r'[\d,.\s]+', price_str)
        if numbers:
            num_str = numbers[0].replace(",", "").replace(" ", "").strip()
            try:
                amount = float(num_str)
                return amount, currency
            except ValueError:
                pass
        
        return 0.0, "USD"
    
    def _convert_to_usd(self, amount: float, currency: str) -> float:
        """Convert price to USD using approximate rates."""
        # Approximate exchange rates (should use API in production)
        rates = {
            "USD": 1.0,
            "AMD": 0.0025,   # 1 AMD ≈ 0.0025 USD
            "GEL": 0.37,     # 1 GEL ≈ 0.37 USD
        }
        
        rate = rates.get(currency.upper(), 1.0)
        return amount * rate
    
    def _parse_mileage(self, mileage_str: str) -> Optional[int]:
        """Parse mileage string to km."""
        import re
        
        mileage_str = mileage_str.lower().strip()
        
        numbers = re.findall(r'[\d,.\s]+', mileage_str)
        if not numbers:
            return None
        
        num_str = numbers[0].replace(",", "").replace(" ", "").strip()
        try:
            value = int(float(num_str))
            
            # Convert miles to km if needed
            if "mi" in mileage_str and "km" not in mileage_str:
                value = int(value * 1.60934)
            
            return value
        except ValueError:
            return None
    
    def _parse_year(self, year_str: str) -> Optional[int]:
        """Parse year from string."""
        import re
        
        match = re.search(r'(19|20)\d{2}', year_str)
        if match:
            return int(match.group())
        return None
