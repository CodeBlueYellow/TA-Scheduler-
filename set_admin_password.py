# Sets the admin password. Run with: python set_admin_password.py
# Only a hash is stored in .env, so the real password is never written down

from getpass import getpass
from pathlib import Path

from dotenv import set_key
from werkzeug.security import generate_password_hash

# .env sits next to this file, wherever the command is run from
ENV_FILE = Path(__file__).parent / ".env"

if not ENV_FILE.exists():
    raise SystemExit("No .env file found. Copy .env.example to .env first.")

# getpass hides what's typed, like the MySQL password prompt
password = getpass("New admin password: ")

if len(password) < 8:
    raise SystemExit("Password must be at least 8 characters. Nothing was changed.")

if password != getpass("Type it again: "):
    raise SystemExit("Those didn't match. Nothing was changed.")

set_key(ENV_FILE, "ADMIN_PASSWORD_HASH", generate_password_hash(password))

print("Admin password saved. Restart the app to use it.")
