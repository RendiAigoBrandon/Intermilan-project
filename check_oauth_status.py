"""
Check OAuth status in production environment.
Run: python manage.py shell < check_oauth_status.py
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env if exists
dotenv_path = Path(__file__).parent / '.env'
if dotenv_path.exists():
    load_dotenv(dotenv_path, override=True)

from apps.documents.services.google_drive import oauth_enabled
from apps.documents.services.google_oauth import is_oauth_configured, has_central_token

print("=" * 60)
print("OAUTH STATUS CHECK")
print("=" * 60)
print()
print(f"oauth_enabled():        {oauth_enabled()}")
print(f"is_oauth_configured():  {is_oauth_configured()}")
print(f"has_central_token():    {has_central_token()}")
print()

# Also show env vars (without secrets)
print("Environment Variables:")
print(f"  GOOGLE_DRIVE_ENABLED:        {os.environ.get('GOOGLE_DRIVE_ENABLED', 'NOT SET')}")
print(f"  GOOGLE_DRIVE_UPLOAD_MODE:     {os.environ.get('GOOGLE_DRIVE_UPLOAD_MODE', 'NOT SET')}")
print(f"  GOOGLE_DRIVE_OAUTH_CLIENT_ID: {'SET' if os.environ.get('GOOGLE_DRIVE_OAUTH_CLIENT_ID') else 'NOT SET'}")
print(f"  GOOGLE_DRIVE_OAUTH_CLIENT_SECRET: {'SET' if os.environ.get('GOOGLE_DRIVE_OAUTH_CLIENT_SECRET') else 'NOT SET'}")
print()
