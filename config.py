import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Config:
    BOT_TOKEN: str
    GROQ_API_KEY: str
    GOOGLE_SHEETS_ID: str | None = None
    GOOGLE_CREDENTIALS_JSON: str | None = None
    TIMEZONE: str = "Europe/Moscow"
    ACCESS_PASSWORD: str | None = None
    REPORT_USER_IDS: list = field(default_factory=list)
    DEEPSEEK_API_KEY: str | None = None
    DEEPSEEK_API_URL: str | None = None
    GOOGLE_OAUTH_CLIENT_ID: str | None = None
    GOOGLE_OAUTH_CLIENT_SECRET: str | None = None
    GOOGLE_REDIRECT_URI: str | None = None
    HEALTHCONNECT_INGEST_TOKEN: str | None = None
    WEB_UI_TOKEN: str | None = None
    MINIAPP_URL: str | None = None
    MEALBOT_STORAGE: str = "sheets"
    MEALBOT_SQLITE_PATH: str = "meal_bot.sqlite3"
    GARMIN_CONNECT_EMAIL: str | None = None
    GARMIN_CONNECT_PASSWORD: str | None = None
    GARMIN_CONNECT_TOKENSTORE: str = "garmin_tokens.json"
    GARMIN_CONNECT_DEBUG_DUMP: bool = False
    STRICT_EXPENDITURE_SOURCE: bool = True
    WEB_PORT: int = 8080


def load_config() -> Config:
    # Загружаем .env файл, если он есть рядом
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=True)

    return Config(
        BOT_TOKEN=os.environ["BOT_TOKEN"],
        GROQ_API_KEY=os.environ["GROQ_API_KEY"],
        GOOGLE_SHEETS_ID=os.getenv("GOOGLE_SHEETS_ID"),
        GOOGLE_CREDENTIALS_JSON=os.getenv("GOOGLE_CREDENTIALS_JSON"),
        TIMEZONE=os.getenv("TIMEZONE", "Europe/Moscow"),
        ACCESS_PASSWORD=os.getenv("ACCESS_PASSWORD"),
        REPORT_USER_IDS=[
            int(x) for x in os.getenv("REPORT_USER_IDS", "").split(",") if x.strip()
        ],
        DEEPSEEK_API_KEY=os.getenv("DEEPSEEK_API_KEY"),
        DEEPSEEK_API_URL=os.getenv("DEEPSEEK_API_URL"),
        GOOGLE_OAUTH_CLIENT_ID=os.getenv("GOOGLE_OAUTH_CLIENT_ID"),
        GOOGLE_OAUTH_CLIENT_SECRET=os.getenv("GOOGLE_OAUTH_CLIENT_SECRET"),
        GOOGLE_REDIRECT_URI=os.getenv("GOOGLE_REDIRECT_URI"),
        HEALTHCONNECT_INGEST_TOKEN=os.getenv("HEALTHCONNECT_INGEST_TOKEN"),
        WEB_UI_TOKEN=os.getenv("WEB_UI_TOKEN"),
        MINIAPP_URL=os.getenv("MINIAPP_URL"),
        MEALBOT_STORAGE=os.getenv("MEALBOT_STORAGE", "sheets"),
        MEALBOT_SQLITE_PATH=os.getenv("MEALBOT_SQLITE_PATH", "meal_bot.sqlite3"),
        GARMIN_CONNECT_EMAIL=os.getenv("GARMIN_CONNECT_EMAIL"),
        GARMIN_CONNECT_PASSWORD=os.getenv("GARMIN_CONNECT_PASSWORD"),
        GARMIN_CONNECT_TOKENSTORE=os.getenv("GARMIN_CONNECT_TOKENSTORE", "garmin_tokens.json"),
        GARMIN_CONNECT_DEBUG_DUMP=os.getenv("GARMIN_CONNECT_DEBUG_DUMP", "").strip().lower() in {"1", "true", "yes", "on"},
        STRICT_EXPENDITURE_SOURCE=os.getenv("STRICT_EXPENDITURE_SOURCE", "true").strip().lower() not in {"0", "false", "no", "off"},
        WEB_PORT=int(os.getenv("WEB_PORT", "8080")),
    )
