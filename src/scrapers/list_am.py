import re
import logging
from typing import List, Optional, Any
from urllib.parse import urlencode
from bs4 import BeautifulSoup

from .base import BaseScraper, CarListing
from ..proxy_manager import ProxyManager
from ..config import URGENCY_KEYWORDS, TARGET_CARS

logger = logging.getLogger(__name__)


class ListAmScraper(BaseScraper):
    """
    Scraper for list.am - Armenian classifieds.
    Handles static HTML pages with some Cloudflare protection.
    """
    
    SOURCE_NAME = "list.am"
    BASE_URL = "https://www.list.am"
    CARS_CATEGORY = "/category/23"  # Transport category (cars)
    
    # CSS selectors
    LISTING_SELECTOR = ".gl"  # Grid listing item
    TITLE_SELECTOR = ".l"
    PRICE_SELECTOR = ".p"
    INFO_SELECTOR = ".at"
    IMAGE_SELECTOR = "img"
    
    def __init__(
        self,
        proxy_manager: Optional[ProxyManager] = None,
        delay_min: int = 5,
        delay_max: int = 15,
        max_retries: int = 3
    ):
        super().__init__(proxy_manager, delay_min, delay_max, max_retries)
    
    async def build_search_url(
        self,
        brand_id: int,
        model_id: int,
        min_year: int,
        max_price_usd: int
    ) -> str:
        """Build search URL for list.am."""
        params = {
            "bid": brand_id,
            "price2": max_price_usd,
            "_a2_1": min_year,
            "crc": 1,
        }
        
        if model_id > 0:
            params["mid"] = model_id
        
        url = f"{self.BASE_URL}{self.CARS_CATEGORY}?{urlencode(params)}"
        return url
    
    async def scrape_listings(
        self,
        min_year: int,
        max_price_usd: int,
        max_pages: int = 5
    ) -> List[CarListing]:
        """Scrape car listings from list.am."""
        all_listings = []
        
        for car_name, (brand_id, model_id) in TARGET_CARS.items():
            base_url = await self.build_search_url(brand_id, model_id, min_year, max_price_usd)
            logger.info(f"Scraping {car_name} from list.am")
            
            for page_num in range(1, max_pages + 1):
                # Construct page URL
                if page_num == 1:
                    url = base_url
                else:
                    url = f"{base_url}&pg={page_num}"
                
                logger.info(f"Scraping list.am {car_name} page {page_num}: {url}")
                
                page = await self._fetch_page(url, wait_selector=".gl")
                
                if not page:
                    logger.warning(f"Failed to fetch page {page_num}")
                    break
                
                try:
                    # Get page content
                    content = await page.content()
                    soup = BeautifulSoup(content, "lxml")
                    
                    # Find all listing elements
                    listing_elements = soup.select(self.LISTING_SELECTOR)
                    
                    if not listing_elements:
                        logger.info(f"No more listings found on page {page_num}")
                        break
                    
                    logger.info(f"Found {len(listing_elements)} listings on page {page_num}")
                    
                    # Parse each listing
                    for element in listing_elements:
                        try:
                            listing = await self.parse_listing_element(element, brand)
                            
                            if listing:
                                all_listings.append(listing)
                                logger.debug(f"Added: {listing.make} {listing.model} ${listing.price_usd}")
                        except Exception as e:
                            logger.warning(f"Failed to parse listing: {e}")
                    
                finally:
                    await page.close()
                
                # Random delay between pages
                if page_num < max_pages:
                    await self._random_delay()
            
            # Delay between car models
            await self._random_delay()
        
        logger.info(f"Total listings scraped from list.am: {len(all_listings)}")
        return all_listings
    
    async def parse_listing_element(self, element: Any, brand: str = None) -> Optional[CarListing]:
        """Parse a single listing element from BeautifulSoup."""
        try:
            # Get listing URL and ID
            link = element.select_one("a")
            if not link:
                return None
            
            href = link.get("href", "")
            if not href.startswith("/item/"):
                return None
            
            listing_id = href.split("/")[-1]
            url = f"{self.BASE_URL}{href}"
            
            # Get title
            title_elem = element.select_one(self.TITLE_SELECTOR)
            title = title_elem.get_text(strip=True) if title_elem else ""
            
            # Get price
            price_elem = element.select_one(self.PRICE_SELECTOR)
            price_text = price_elem.get_text(strip=True) if price_elem else "0"
            price_original, currency = self._parse_price(price_text)
            price_usd = self._convert_to_usd(price_original, currency)
            
            # Get info (year, mileage, etc.)
            info_elem = element.select_one(self.INFO_SELECTOR)
            info_text = info_elem.get_text(strip=True) if info_elem else ""
            
            # Parse make and model from title
            make, model = self._parse_make_model(title)
            
            # Parse year from info or title
            year = self._parse_year(info_text) or self._parse_year(title)
            
            # Parse mileage from info
            mileage = self._parse_mileage(info_text)
            
            # Get image URL
            img_elem = element.select_one(self.IMAGE_SELECTOR)
            image_url = None
            if img_elem:
                image_url = img_elem.get("src") or img_elem.get("data-src")
                if image_url and not image_url.startswith("http"):
                    image_url = f"{self.BASE_URL}{image_url}"
            
            # Check for urgency indicators
            full_text = f"{title} {info_text}".lower()
            is_urgent = self._detect_urgency(full_text)
            
            # Get location
            location = self._extract_location(info_text)
            
            return CarListing(
                source=self.SOURCE_NAME,
                listing_id=listing_id,
                url=url,
                make=make,
                model=model,
                year=year,
                mileage=mileage,
                price_usd=price_usd,
                price_original=price_original,
                currency_original=currency,
                title=title,
                location=location,
                image_url=image_url,
                is_urgent=is_urgent,
                raw_data={"info_text": info_text}
            )
            
        except Exception as e:
            logger.warning(f"Error parsing listing element: {e}")
            return None
    
    def _parse_make_model(self, title: str) -> tuple[str, Optional[str]]:
        """Extract make and model from title."""
        title_lower = title.lower()
        
        # Known makes to search for
        makes = {
            "bmw": "BMW",
            "mercedes": "Mercedes",
            "mercedes-benz": "Mercedes",
            "audi": "Audi",
            "lexus": "Lexus",
            "toyota": "Toyota",
            "honda": "Honda",
            "nissan": "Nissan",
            "hyundai": "Hyundai",
            "kia": "Kia",
        }
        
        detected_make = "Unknown"
        for key, value in makes.items():
            if key in title_lower:
                detected_make = value
                break
        
        # Try to extract model (usually follows make)
        model = None
        parts = title.split()
        for i, part in enumerate(parts):
            if part.lower() in makes and i + 1 < len(parts):
                # Next part might be model
                model_candidate = parts[i + 1]
                # Clean up model name
                model = re.sub(r'[^\w\s-]', '', model_candidate)
                break
        
        return detected_make, model
    
    def _matches_brand(self, listing: CarListing, brands_lower: List[str]) -> bool:
        """Check if listing matches any of the target brands."""
        listing_make = listing.make.lower()
        listing_title = (listing.title or "").lower()
        
        for brand in brands_lower:
            if brand in listing_make or brand in listing_title:
                return True
        
        return False
    
    def _detect_urgency(self, text: str) -> bool:
        """Detect urgency indicators in text."""
        text_lower = text.lower()
        
        # Check all language keywords
        for lang_keywords in URGENCY_KEYWORDS.values():
            for keyword in lang_keywords:
                if keyword.lower() in text_lower:
                    return True
        
        # Check for multiple exclamation marks
        if "!!!" in text or text.count("!") > 2:
            return True
        
        return False
    
    def _extract_location(self, info_text: str) -> Optional[str]:
        """Extract location from info text."""
        # Common Armenian locations
        locations = [
            "Yerevan", "Երdelays", "Gyumri", "Vanadzor",
            "Ejmiatsin", "Abovyan", "Kapan", "Armavir"
        ]
        
        for loc in locations:
            if loc.lower() in info_text.lower():
                return loc
        
        return None
