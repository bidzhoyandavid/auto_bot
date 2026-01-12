from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator
from typing import List
import os


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    # Telegram
    telegram_bot_token: str = Field(..., validation_alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(..., validation_alias="TELEGRAM_CHAT_ID")
    
    # Scraping intervals
    scrape_interval_minutes: int = Field(default=25, validation_alias="SCRAPE_INTERVAL_MINUTES")
    request_delay_min: int = Field(default=5, validation_alias="REQUEST_DELAY_MIN")
    request_delay_max: int = Field(default=15, validation_alias="REQUEST_DELAY_MAX")
    
    # Proxy
    proxy_refresh_minutes: int = Field(default=15, validation_alias="PROXY_REFRESH_MINUTES")
    min_proxy_pool_size: int = Field(default=10, validation_alias="MIN_PROXY_POOL_SIZE")
    
    # Car filters - stored as comma-separated string in .env
    target_brands_str: str = Field(
        default="BMW,Mercedes,Audi,Lexus",
        validation_alias="TARGET_BRANDS"
    )
    min_year: int = Field(default=2020, validation_alias="MIN_YEAR")
    max_price_usd: int = Field(default=20000, validation_alias="MAX_PRICE_USD")
    
    # Database
    database_url: str = Field(
        default="sqlite+aiosqlite:///auto_bot.db",
        validation_alias="DATABASE_URL"
    )
    
    @property
    def target_brands(self) -> List[str]:
        """Parse target brands from comma-separated string."""
        return [b.strip() for b in self.target_brands_str.split(",")]


def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Brand mappings for different sites
BRAND_MAPPINGS = {
    "list.am": {
        "BMW": "BMW",
        "Mercedes": "Mercedes",
        "Audi": "Audi",
        "Lexus": "Lexus"
    },
    "myauto.ge": {
        # Manufacturer IDs on myauto.ge
        "BMW": "9",
        "Mercedes": "47",
        "Audi": "11",
        "Lexus": "37"
    }
}

# Urgency keywords for detection
URGENCY_KEYWORDS = {
    "ru": [
        "срочно", "срочная продажа", "торг", "торг уместен",
        "нужны деньги", "переезд", "в связи с отъездом",
        "быстрая продажа", "сегодня", "завтра"
    ],
    "en": [
        "urgent", "urgently", "quick sale", "must sell",
        "negotiable", "moving", "relocating", "asap"
    ],
    "am": [
        "շdelays", "անdelays"
    ],
    "ge": [
        "სასწრაფოდ"
    ]
}
