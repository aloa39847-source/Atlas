import os
import sys
from pathlib import Path

# Ensure backend package is importable when running `pytest` from backend/
BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# Use isolated test DB
os.environ.setdefault("TITAN_DATABASE_URL", "sqlite:///./backend/data/test_titan.db")
os.environ.setdefault("TITAN_LIVE_TRADING_ENABLED", "false")
os.environ.setdefault("TITAN_ALLOW_DEMO_TO_LIVE_AUTO", "false")
