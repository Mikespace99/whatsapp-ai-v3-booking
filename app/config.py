import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "").strip()
    VERIFY_TOKEN: str = os.getenv("VERIFY_TOKEN", "whatsapp_ai_verify_token_2026").strip()
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./app.db").strip()

    # Timeout inattività conversazione (10 minuti)
    SESSION_TIMEOUT_SECONDS: int = int(os.getenv("SESSION_TIMEOUT_SECONDS", "600"))

    # Durata blocco temporaneo slot (5 minuti)
    SLOT_LOCK_TIMEOUT_SECONDS: int = int(os.getenv("SLOT_LOCK_TIMEOUT_SECONDS", "300"))


settings = Settings()
