from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # silently ignore unknown env vars (e.g. BACKEND_PORT from docker-compose)
    )

    # Database
    DATABASE_URL: str

    # Google / Gemini
    GOOGLE_CREDENTIALS_FILE: str
    GEMINI_API_KEY: str = ""

    # Central Gantt spreadsheet (Gantt_Overall)
    # Set to the spreadsheet ID or full URL. When set, Gantt pulls use
    # per-company tab names instead of per-company sheets_url fields.
    GANTT_SPREADSHEET_ID: str = ""

    # Granola
    GRANOLA_API_KEY: str = ""
    GRANOLA_API_BASE_URL: str = "https://public-api.granola.ai"

    # Claude / Anthropic (Phase 3 - GTM)
    CLAUDE_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-3-5-sonnet-20241022"

    # Auth
    JWT_SECRET: str
    ADMIN_PASSWORD: str

    # CORS
    CORS_ORIGINS: str = "http://localhost:5173"

    # Server tunables (used by docker-entrypoint.sh / uvicorn)
    PORT: int = 8000
    LOG_LEVEL: str = "info"

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]


settings = Settings()
