"""Load the shared environment and require credentials at startup."""

from os import environ
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def load_env():
    load_dotenv(ROOT / ".env", override=True)
    for name in ("FAL_KEY", "HF_TOKEN", "MODEL_API_KEY"):
        assert environ[name].strip(), f"{name} must be set in .env"
