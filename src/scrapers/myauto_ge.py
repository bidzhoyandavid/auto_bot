import re
import json
import logging
from typing import List, Optional, Any
from urllib.parse import urlencode

from .base import BaseScraper, CarListing
from ..proxy_manager import ProxyManager
from ..config import URGENCY_KEYWORDS, BRAND_MAPPINGS

logger = logging.getLogger(__name__)


class MyAutoGeScraper(BaseScraper):
    """
    Scraper for myauto.ge - Georgian car marketplace.
    Handles SPA (Single Page Application) with dynamic content loading.
    """
    
    SOURCE_NAME = "myauto.ge"
    BASE_URL = "https://www.myauto.ge"
    SEARCH_PATH = "/en/s/car"
    
    # CSS selectors for the SPA
    LISTING_SELECTOR = "[data-testid='search-card']"
    LISTING_LINK_SELECTOR = "a[href*='/pr/']"
    PRICE_SELECTOR = "[data-testid='price']"
    
    # Brand IDs on myauto.ge
    BRAND_IDS = {
        "BMW": "9",
        "Mercedes": "47",
        "Audi": "11",
        "Lexus": "37",
    }
    
    def __init__(
        self,
        proxy_manager: Optional[ProxyManager] = None,
        delay_min: int = 8,
        delay_max: int = 20,
        max_retries: int = 3
    ):
        # Longer delays for myauto.ge (more aggressive protection)
        super().__init__(proxy_manager, delay_min, delay_max, max_retries)
    
    async def build_search_url(
        self,
        brands: List[str],
        min_year: int,
        max_price_usd: int
    ) -> str:
        """Build search URL with filters for myauto.ge."""
        
        # Build manufacturer filter
        # Format: mansNModels=9.0,47.0,11.0,37.0 (brand IDs with .0)
        brand_ids = []
        for brand in brands:
            if brand in self.BRAND_IDS:
                brand_ids.append(f"{self.BRAND_IDS[brand]}.0")
        
        params = {
            "bargainType": "0",      # For sale
            "vehicleType": "0",       # Cars
            "currencyId": "1",        # USD
            "priceTo": max_price_usd,
            "yearFrom": min_year,
            "mansNModels": ",".join(brand_ids),
            "sortId": "1",            # Sort by date (newest first)
        }
        
        url = f"{self.BASE_URL}{self.SEARCH_PATH}?{urlencode(params)}"
        return url
    
    async def scrape_listings(
        self,
        brands: List[str],
        min_year: int,
        max_price_usd: int,
        max_pages: int = 5
    ) -> List[CarListing]:
        """Scrape car listings from myauto.ge."""
        all_listings = []
        
        base_url = await self.build_search_url(brands, min_year, max_price_usd)
        
        for page_num in range(1, max_pages + 1):
            # Construct page URL
            if page_num == 1:
                url = base_url
            else:
                url = f"{base_url}&page={page_num}"
            
            logger.info(f"Scraping myauto.ge page {page_num}: {url}")
            
            # myauto.ge is SPA, need to wait for content to load
            page = await self._fetch_page(
                url, 
                wait_selector="[data-testid='search-results']",
                timeout=45000
            )
            
            if not page:
                logger.warning(f"Failed to fetch page {page_num}")
                break
            
            try:
                # Wait for listings to load
                await page.wait_for_timeout(2000)  # Extra wait for dynamic content
                
                # Try to find listings using various selectors
                listings_found = await self._extract_listings_from_page(page)
                
                if not listings_found:
                    logger.info(f"No more listings found on page {page_num}")
                    break
                
                logger.info(f"Found {len(listings_found)} listings on page {page_num}")
                all_listings.extend(listings_found)
                
            finally:
                await page.close()
            
            # Random delay between pages
            if page_num < max_pages:
                await self._random_delay()
        
        logger.info(f"Total listings scraped from myauto.ge: {len(all_listings)}")
        return all_listings
    
    async def _extract_listings_from_page(self, page) -> List[CarListing]:
        """Extract listings from the page using JavaScript evaluation."""
        listings = []
        
        try:
            # Get all listing cards
            cards = await page.query_selector_all("[class*='card']")
            
            if not cards:
                # Try alternative selector
                cards = await page.query_selector_all("a[href*='/pr/']")
            
            for card in cards:
                try:
                    listing = await self._parse_card(page, card)
                    if listing:
                        listings.append(listing)
                except Exception as e:
                    logger.debug(f"Failed to parse card: {e}")
            
            # If no cards found, try to extract from page data
            if not listings:
                listings = await self._extract_from_json(page)
            
        except Exception as e:
            logger.warning(f"Error extracting listings: {e}")
        
        return listings
    
    async def _parse_card(self, page, card) -> Optional[CarListing]:
        """Parse a single listing card element."""
        try:
            # Get the href for listing URL
            href = await card.get_attribute("href")
            
            if not href or "/pr/" not in href:
                # Try to find link inside card
                link = await card.query_selector("a[href*='/pr/']")
                if link:
                    href = await link.get_attribute("href")
            
            if not href or "/pr/" not in href:
                return None
            
            # Extract listing ID from URL
            # Format: /en/pr/123456/some-slug
            match = re.search(r'/pr/(\d+)', href)
            if not match:
                return None
            
            listing_id = match.group(1)
            url = f"{self.BASE_URL}{href}" if href.startswith("/") else href
            
            # Get text content
            text_content = await card.inner_text()
            
            # Parse details from text
            make, model = self._parse_make_model_from_text(text_content)
            year = self._parse_year(text_content)
            mileage = self._parse_mileage(text_content)
            price_usd, price_original, currency = self._parse_price_from_text(text_content)
            
            # Check for urgency stickers
            is_urgent = await self._check_urgency_sticker(card, text_content)
            
            # Check customs status
            customs_cleared = "customs cleared" in text_content.lower() or "განბაჟებული" in text_content
            
            # Get image
            image_url = await self._get_image_url(card)
            
            # Extract location
            location = self._extract_location(text_content)
            
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
                title=f"{make} {model or ''} {year or ''}".strip(),
                location=location,
                image_url=image_url,
                is_urgent=is_urgent,
                customs_cleared=customs_cleared,
            )
            
        except Exception as e:
            logger.debug(f"Error parsing card: {e}")
            return None
    
    async def _extract_from_json(self, page) -> List[CarListing]:
        """Try to extract listings from embedded JSON data."""
        listings = []
        
        try:
            # myauto.ge often has __NEXT_DATA__ with listing info
            script_content = await page.evaluate("""
                () => {
                    const script = document.querySelector('#__NEXT_DATA__');
                    return script ? script.textContent : null;
                }
            """)
            
            if script_content:
                data = json.loads(script_content)
                
                # Navigate to listings in the JSON structure
                props = data.get("props", {})
                page_props = props.get("pageProps", {})
                items = page_props.get("items", []) or page_props.get("results", [])
                
                for item in items:
                    listing = self._parse_json_item(item)
                    if listing:
                        listings.append(listing)
                
        except Exception as e:
            logger.debug(f"Could not extract from JSON: {e}")
        
        return listings
    
    def _parse_json_item(self, item: dict) -> Optional[CarListing]:
        """Parse a listing from JSON data."""
        try:
            listing_id = str(item.get("car_id") or item.get("id", ""))
            if not listing_id:
                return None
            
            # Get make/model
            make = item.get("man_name", "") or item.get("manufacturer", "")
            model = item.get("model_name", "") or item.get("model", "")
            make = self._normalize_make(make)
            
            # Get price
            price = item.get("price", 0)
            currency = "USD" if item.get("currency_id") == 1 else "GEL"
            price_usd = price if currency == "USD" else self._convert_to_usd(price, currency)
            
            # Get other details
            year = item.get("prod_year") or item.get("year")
            mileage = item.get("car_run") or item.get("mileage")
            
            # Build URL
            url = f"{self.BASE_URL}/en/pr/{listing_id}"
            
            # Get image
            photo = item.get("photo") or item.get("pic_url", "")
            image_url = None
            if photo:
                if photo.startswith("http"):
                    image_url = photo
                else:
                    image_url = f"https://static.my.ge/myauto/photos/{photo}"
            
            # Check stickers for urgency
            stickers = item.get("stickers", []) or []
            is_urgent = any(
                s.get("name", "").lower() in ["urgently", "სასწრაფოდ"]
                for s in stickers
            )
            
            # Customs status
            customs_cleared = item.get("customs_passed", False)
            
            # Location
            location = item.get("location_name") or item.get("location", "")
            
            return CarListing(
                source=self.SOURCE_NAME,
                listing_id=listing_id,
                url=url,
                make=make,
                model=model,
                year=year,
                mileage=mileage,
                price_usd=price_usd,
                price_original=price,
                currency_original=currency,
                title=f"{make} {model} {year}".strip(),
                location=location,
                image_url=image_url,
                is_urgent=is_urgent,
                customs_cleared=customs_cleared,
            )
            
        except Exception as e:
            logger.debug(f"Error parsing JSON item: {e}")
            return None
    
    async def parse_listing_element(self, element: Any) -> Optional[CarListing]:
        """Parse a single listing element (for interface compatibility)."""
        # This is handled by _parse_card for Playwright elements
        return None
    
    def _parse_make_model_from_text(self, text: str) -> tuple[str, Optional[str]]:
        """Extract make and model from text content."""
        text_lower = text.lower()
        
        makes = {
            "bmw": "BMW",
            "mercedes": "Mercedes",
            "mercedes-benz": "Mercedes",
            "audi": "Audi",
            "lexus": "Lexus",
        }
        
        detected_make = "Unknown"
        for key, value in makes.items():
            if key in text_lower:
                detected_make = value
                break
        
        # Try to extract model
        model = None
        
        # Common model patterns
        model_patterns = [
            r"BMW\s+(\w+)",
            r"Mercedes\s+(\w+[-]?\w*)",
            r"Audi\s+(\w+)",
            r"Lexus\s+(\w+)",
        ]
        
        for pattern in model_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                model = match.group(1)
                break
        
        return detected_make, model
    
    def _parse_price_from_text(self, text: str) -> tuple[float, float, str]:
        """Parse price from text content."""
        # Look for price patterns
        # $15,000 or 15 000 $ or 40000 ₾
        
        price_patterns = [
            r'\$\s*([\d,\s]+)',          # $15,000
            r'([\d,\s]+)\s*\$',          # 15 000 $
            r'([\d,\s]+)\s*USD',         # 15000 USD
            r'₾\s*([\d,\s]+)',           # ₾ 40000
            r'([\d,\s]+)\s*₾',           # 40000 ₾
            r'([\d,\s]+)\s*GEL',         # 40000 GEL
        ]
        
        for pattern in price_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                price_str = match.group(1).replace(",", "").replace(" ", "")
                try:
                    price = float(price_str)
                    
                    # Determine currency
                    if "$" in text or "USD" in text.upper():
                        return price, price, "USD"
                    elif "₾" in text or "GEL" in text.upper():
                        price_usd = self._convert_to_usd(price, "GEL")
                        return price_usd, price, "GEL"
                    else:
                        return price, price, "USD"
                except ValueError:
                    continue
        
        return 0.0, 0.0, "USD"
    
    async def _check_urgency_sticker(self, card, text: str) -> bool:
        """Check for urgency indicators."""
        text_lower = text.lower()
        
        # Check for urgency stickers/badges
        urgency_indicators = [
            "urgently", "urgent",
            "სასწრაფოდ",  # Georgian for "urgently"
            "hot offer", "must sell"
        ]
        
        for indicator in urgency_indicators:
            if indicator in text_lower:
                return True
        
        # Check keywords from config
        for lang_keywords in URGENCY_KEYWORDS.values():
            for keyword in lang_keywords:
                if keyword.lower() in text_lower:
                    return True
        
        return False
    
    async def _get_image_url(self, card) -> Optional[str]:
        """Extract image URL from card."""
        try:
            img = await card.query_selector("img")
            if img:
                src = await img.get_attribute("src")
                if src and not src.startswith("data:"):
                    return src
                
                # Try lazy loading attributes
                for attr in ["data-src", "data-lazy-src"]:
                    src = await img.get_attribute(attr)
                    if src:
                        return src
        except:
            pass
        
        return None
    
    def _extract_location(self, text: str) -> Optional[str]:
        """Extract location from text."""
        locations = [
            "Tbilisi", "თბილისი",
            "Batumi", "ბათუმი",
            "Kutaisi", "ქუთაისი",
            "Rustavi", "რუსთავი",
            "Gori", "გორი",
            "Zugdidi", "ზუგდიდი",
        ]
        
        for loc in locations:
            if loc.lower() in text.lower():
                # Return English version
                return loc if loc[0].isascii() else None
        
        return None
