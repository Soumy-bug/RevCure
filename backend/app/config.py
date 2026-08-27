import os
from pathlib import Path
from dotenv import load_dotenv

# Base directories
BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent

# Load backend/.env first, then root .env if present
load_dotenv(BACKEND_DIR / ".env")
load_dotenv(REPO_ROOT / ".env")

class Settings:
    PROJECT_NAME: str = "RevCure API"
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    DEBUG: bool = os.getenv("DEBUG", "true").lower() in ("true", "1", "t")
    ENABLE_DB_TEST: bool = os.getenv("ENABLE_DB_TEST", "false").lower() in ("true", "1", "t")

    # PostgreSQL / Database connection configuration
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        f"postgresql://{os.getenv('POSTGRES_USER', 'revcure_user')}:{os.getenv('POSTGRES_PASSWORD', 'postgres')}@{os.getenv('POSTGRES_HOST', 'localhost')}:{os.getenv('POSTGRES_PORT', '5433')}/{os.getenv('POSTGRES_DB', 'revcure_db')}",
    )

    # Razorpay credentials — MUST be set via environment variables.
    # Never hardcode. Use RAZORPAY_KEY_ID=rzp_test_xxx for test mode.
    RAZORPAY_KEY_ID: str = os.getenv("RAZORPAY_KEY_ID", "")
    RAZORPAY_KEY_SECRET: str = os.getenv("RAZORPAY_KEY_SECRET", "")
    RAZORPAY_WEBHOOK_SECRET: str = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")

    @property
    def razorpay_configured(self) -> bool:
        """Return True only when both Razorpay key ID and secret are non-empty
        AND look like real credentials (not obvious placeholders)."""
        key = self.RAZORPAY_KEY_ID.strip()
        secret = self.RAZORPAY_KEY_SECRET.strip()
        if not key or not secret:
            return False
        # Reject placeholder values that look like test defaults
        _placeholders = {"", "placeholder", "placeholder_key", "placeholder_secret",
                         "rzp_test_placeholder_key", "placeholder_secret_key"}
        if key.lower() in _placeholders or secret.lower() in _placeholders:
            return False
        return True

settings = Settings()
