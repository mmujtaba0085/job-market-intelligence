"""
src/auth/oauth_google.py
─────────────────────────
Registers Google as an Authlib OAuth client. No-op when
GOOGLE_OAUTH_CLIENT_ID/SECRET aren't set — callers should check
config.settings.GOOGLE_OAUTH_ENABLED before using `oauth.google`.
"""

from authlib.integrations.flask_client import OAuth

from config.settings import GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, GOOGLE_OAUTH_ENABLED

oauth = OAuth()


def init_oauth(app):
    oauth.init_app(app)
    if GOOGLE_OAUTH_ENABLED:
        oauth.register(
            name="google",
            client_id=GOOGLE_OAUTH_CLIENT_ID,
            client_secret=GOOGLE_OAUTH_CLIENT_SECRET,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
