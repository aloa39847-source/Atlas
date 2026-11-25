"""Set an initial local password for an allowlisted TITAN user.

Usage (run from backend/):
  TITAN_ALLOWED_EMAILS=abdoavm02@gmail.com python3 -m app.scripts.set_initial_password --email abdoavm02@gmail.com

Password is read from TITAN_INITIAL_PASSWORD env var (local shell only) or prompted interactively.
Never commit passwords or pass them in chat logs.
"""
from __future__ import annotations

import argparse
import getpass
import os

from app.models.db import SessionLocal, init_db
from app.services.auth_service import set_password


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    password = os.getenv("TITAN_INITIAL_PASSWORD") or getpass.getpass("New password (input hidden): ")
    if not password or len(password) < 8:
        raise SystemExit("Password must be at least 8 characters.")
    init_db()
    db = SessionLocal()
    try:
        user = set_password(db, args.email, password)
        db.commit()
        print(f"Password hash stored for {user.email}. (Password not displayed.)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
