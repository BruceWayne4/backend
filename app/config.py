from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    DATABASE_URL: str = "postgresql+asyncpg://user:password@localhost:5432/ajvc"
    GOOGLE_CREDENTIALS_FILE: str = "./credentials.json"
    GEMINI_API_KEY: str = ""
    GRANOLA_API_KEY: str = ""
    GRANOLA_API_BASE_URL: str = "https://public-api.granola.ai"
    JWT_SECRET: str = "change-me-in-production"
    ADMIN_PASSWORD: str = "ajvc2026"
    CORS_ORIGINS: str = "http://localhost:5173"

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]


settings = Settings()
