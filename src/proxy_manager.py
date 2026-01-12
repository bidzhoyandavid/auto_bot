import aiohttp
import asyncio
import logging
import random
import re
from typing import Optional, List, Set
from dataclasses import dataclass
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass
class Proxy:
    """Proxy server information."""
    host: str
    port: int
    protocol: str = "http"
    country: Optional[str] = None
    anonymity: Optional[str] = None
    last_checked: Optional[datetime] = None
    success_count: int = 0
    fail_count: int = 0
    
    @property
    def url(self) -> str:
        return f"{self.protocol}://{self.host}:{self.port}"
    
    @property
    def success_rate(self) -> float:
        total = self.success_count + self.fail_count
        if total == 0:
            return 0.5  # Unknown, assume 50%
        return self.success_count / total


class ProxyManager:
    """
    Manager for free proxy rotation.
    Fetches proxies from multiple sources, validates them, and provides rotation.
    """
    
    PROXY_SOURCES = [
        "https://www.sslproxies.org/",
        "https://free-proxy-list.net/",
        "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
    ]
    
    # Test URLs for validation
    TEST_URLS = {
        "list.am": "https://www.list.am/",
        "myauto.ge": "https://www.myauto.ge/",
    }
    
    def __init__(
        self, 
        min_pool_size: int = 10,
        refresh_interval_minutes: int = 15,
        validation_timeout: int = 10
    ):
        self.min_pool_size = min_pool_size
        self.refresh_interval = timedelta(minutes=refresh_interval_minutes)
        self.validation_timeout = validation_timeout
        
        self._proxies: List[Proxy] = []
        self._working_proxies: Set[str] = set()
        self._last_refresh: Optional[datetime] = None
        self._lock = asyncio.Lock()
    
    async def initialize(self):
        """Initialize proxy pool."""
        await self.refresh_proxies()
    
    async def refresh_proxies(self):
        """Fetch and validate proxies from all sources."""
        async with self._lock:
            logger.info("Refreshing proxy pool...")
            
            # Fetch from all sources
            all_proxies = []
            for source in self.PROXY_SOURCES:
                try:
                    proxies = await self._fetch_from_source(source)
                    all_proxies.extend(proxies)
                    logger.info(f"Fetched {len(proxies)} proxies from {source}")
                except Exception as e:
                    logger.warning(f"Failed to fetch from {source}: {e}")
            
            # Remove duplicates
            seen = set()
            unique_proxies = []
            for proxy in all_proxies:
                key = f"{proxy.host}:{proxy.port}"
                if key not in seen:
                    seen.add(key)
                    unique_proxies.append(proxy)
            
            logger.info(f"Total unique proxies: {len(unique_proxies)}")
            
            # Validate proxies (in parallel, but limited)
            validated = await self._validate_proxies(unique_proxies)
            
            self._proxies = validated
            self._working_proxies = {p.url for p in validated}
            self._last_refresh = datetime.utcnow()
            
            logger.info(f"Validated proxies: {len(validated)}")
    
    async def _fetch_from_source(self, source_url: str) -> List[Proxy]:
        """Fetch proxies from a single source."""
        proxies = []
        
        async with aiohttp.ClientSession() as session:
            async with session.get(source_url, timeout=15) as response:
                text = await response.text()
                
                if "proxyscrape" in source_url:
                    # Plain text format: ip:port
                    for line in text.strip().split("\n"):
                        line = line.strip()
                        if ":" in line:
                            parts = line.split(":")
                            if len(parts) == 2:
                                proxies.append(Proxy(
                                    host=parts[0],
                                    port=int(parts[1])
                                ))
                else:
                    # HTML table format
                    proxies.extend(self._parse_html_table(text))
        
        return proxies
    
    def _parse_html_table(self, html: str) -> List[Proxy]:
        """Parse proxy table from HTML."""
        proxies = []
        
        # Simple regex to find IP:PORT patterns in table
        # This works for free-proxy-list.net and sslproxies.org
        ip_pattern = r'<td>(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})</td>\s*<td>(\d+)</td>'
        matches = re.findall(ip_pattern, html)
        
        for ip, port in matches:
            try:
                proxies.append(Proxy(
                    host=ip,
                    port=int(port)
                ))
            except ValueError:
                continue
        
        return proxies
    
    async def _validate_proxies(
        self, 
        proxies: List[Proxy], 
        max_concurrent: int = 20
    ) -> List[Proxy]:
        """Validate proxies against target sites."""
        semaphore = asyncio.Semaphore(max_concurrent)
        validated = []
        
        async def validate_one(proxy: Proxy) -> Optional[Proxy]:
            async with semaphore:
                if await self._test_proxy(proxy):
                    return proxy
                return None
        
        tasks = [validate_one(p) for p in proxies]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, Proxy):
                validated.append(result)
        
        return validated
    
    async def _test_proxy(self, proxy: Proxy) -> bool:
        """Test if proxy works against a target site."""
        test_url = random.choice(list(self.TEST_URLS.values()))
        
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            timeout = aiohttp.ClientTimeout(total=self.validation_timeout)
            
            async with aiohttp.ClientSession(
                connector=connector,
                timeout=timeout
            ) as session:
                async with session.get(
                    test_url,
                    proxy=proxy.url,
                    headers={"User-Agent": self._get_random_ua()}
                ) as response:
                    if response.status == 200:
                        proxy.last_checked = datetime.utcnow()
                        proxy.success_count += 1
                        return True
        except Exception:
            proxy.fail_count += 1
        
        return False
    
    async def get_proxy(self) -> Optional[str]:
        """Get a working proxy URL."""
        # Refresh if needed
        if self._should_refresh():
            await self.refresh_proxies()
        
        if not self._proxies:
            logger.warning("No proxies available, using direct connection")
            return None
        
        # Sort by success rate and pick randomly from top half
        sorted_proxies = sorted(
            self._proxies, 
            key=lambda p: p.success_rate, 
            reverse=True
        )
        
        top_half = sorted_proxies[:max(1, len(sorted_proxies) // 2)]
        selected = random.choice(top_half)
        
        return selected.url
    
    def _should_refresh(self) -> bool:
        """Check if proxy pool needs refresh."""
        if self._last_refresh is None:
            return True
        
        if datetime.utcnow() - self._last_refresh > self.refresh_interval:
            return True
        
        if len(self._proxies) < self.min_pool_size:
            return True
        
        return False
    
    def mark_proxy_failed(self, proxy_url: str):
        """Mark a proxy as failed."""
        for proxy in self._proxies:
            if proxy.url == proxy_url:
                proxy.fail_count += 1
                # Remove if too many failures
                if proxy.success_rate < 0.2:
                    self._proxies.remove(proxy)
                    self._working_proxies.discard(proxy_url)
                break
    
    def mark_proxy_success(self, proxy_url: str):
        """Mark a proxy as successful."""
        for proxy in self._proxies:
            if proxy.url == proxy_url:
                proxy.success_count += 1
                break
    
    def _get_random_ua(self) -> str:
        """Get a random User-Agent string."""
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
        ]
        return random.choice(user_agents)
    
    @property
    def pool_size(self) -> int:
        """Current number of proxies in pool."""
        return len(self._proxies)
    
    @property
    def stats(self) -> dict:
        """Get proxy pool statistics."""
        return {
            "total": len(self._proxies),
            "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None,
            "avg_success_rate": sum(p.success_rate for p in self._proxies) / max(1, len(self._proxies))
        }
