from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_env: str = "development"
    app_secret: str
    database_url: str
    redis_url: str
    node_bootstrap_token: str
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str | None = None
    telegram_bot_token: str | None = None
    public_base_url: str = "https://xu.chanelchat.ir"
    model_config = SettingsConfigDict(case_sensitive=False)

@lru_cache
def get_settings() -> Settings:
    return Settings()
