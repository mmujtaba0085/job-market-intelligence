"""
Run this once to reset the admin password to a freshly-generated random value.
Usage:  python reset_admin.py
"""
import os, secrets, sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

os.environ.setdefault("AUTH_DB_PATH", "data/auth.sqlite")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.auth.models import init_auth_db, list_users, change_password, get_auth_db

NEW_PASSWORD = secrets.token_urlsafe(16)

init_auth_db()

users = list_users()
print("Current users:", [(u["id"], u["username"], u["role"]) for u in users])

admin = next((u for u in users if u["username"] == "admin"), None)
if admin:
    change_password(admin["id"], NEW_PASSWORD)
    print(f"\n✅  Admin password reset to: {NEW_PASSWORD}")
else:
    print("No admin user found — creating one.")
    from src.auth.models import create_user
    create_user("admin", "admin@localhost", NEW_PASSWORD, role="admin")
    print(f"\n✅  Admin user created. Password: {NEW_PASSWORD}")

print("\nYou can now log in at http://localhost:5000")
print("  Username: admin")
print(f"  Password: {NEW_PASSWORD}")
