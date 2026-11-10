# api/settings.py — Configuration de l'API, lue depuis .env
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_key: str = ""
    sitemap_url: str = "https://explorawatersports.com/sitemap.xml"
    max_pages: int = 10


@lru_cache
def get_settings() -> Settings:
    return Settings()