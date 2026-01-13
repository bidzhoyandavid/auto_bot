from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import List, Dict
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
    
    # Car filters
    min_year: int = Field(default=2020, validation_alias="MIN_YEAR")
    max_price_usd: int = Field(default=20000, validation_alias="MAX_PRICE_USD")
    
    # Database
    database_url: str = Field(
        default="sqlite+aiosqlite:///auto_bot.db",
        validation_alias="DATABASE_URL"
    )


def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# =============================================================================
# ЦЕЛЕВЫЕ МОДЕЛИ ДЛЯ ПОИСКА (list.am)
# Формат: "Марка Модель": (brand_id, model_id)
# Если model_id = 0, то ищет все модели этой марки
# =============================================================================

TARGET_CARS = {
    "Mercedes E-Class": (49, 963),
    "Mercedes S-Class": (49, 986),
    "Mercedes GLC-Class": (49, 1984),
    "Mercedes GLE-Class": (49, 1983),
    "BMW 3 Series": (7, 187),
    "BMW 4 Series": (7, 109),
    "BMW 5 Series": (7, 110),
    "BMW 7 Series": (7, 113),
    "BMW X3": (7, 120),
    "BMW X5": (7, 121),
    "Audi A4": (5, 62),
    "Audi A5": (5, 63),
    "Audi A6": (5, 64),
    "Audi A5": (5, 65),
    "Audi A8": (5, 66),
    "Audi Q5": (5, 71),
    "Lexus RX": (42, 833),
    "Lexus GS": (42, 825),
    "Lexus ES": (42, 824),
    "Lexus IS": (42, 828),
    "Toyota Land Cruiser Prado": (76, 1597),
    "Toyota Camry": (76, 1560),
    "Toyota Highlander": (76, 1588),
    "Mitsubishi Outlander": (53, 1069),
    "Mazda CX-5": (48, 914),
    
    # Примеры как добавлять другие:
    # "Mercedes C-Class": (49, 1317),
    # "Mercedes GLE": (49, 5563),
    # "BMW 3 Series": (7, 1133),
    # "BMW X5": (7, 1143),
    # "Audi A6": (5, 1098),
    # "Lexus RX": (42, 1240),
    #
    # Все модели марки (model_id = 0):
    # "BMW All": (7, 0),
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
