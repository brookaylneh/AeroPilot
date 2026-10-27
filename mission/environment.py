"""Shared credential loading for mission clients and deployment commands."""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env', override=True)
for name in ('FAL_KEY', 'HF_TOKEN', 'MODEL_API_KEY'):
    assert os.environ[name].strip(), f'{name} must be set in .env'

if __name__ == '__main__':
    os.execv(sys.argv[1], sys.argv[1:])
