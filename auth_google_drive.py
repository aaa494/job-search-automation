"""
One-time Google Drive authorization.
Run this ONCE in your terminal, then everything else is automatic.

Usage:
  source .venv/bin/activate
  python auth_google_drive.py
"""

import json
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
CREDS_FILE = Path("credentials/google_credentials.json")
TOKEN_FILE = Path("credentials/token.json")


def main():
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Missing library. Run: pip install google-auth-oauthlib")
        return

    if not CREDS_FILE.exists():
        print(f"ERROR: {CREDS_FILE} not found.")
        print("Download it from Google Cloud Console → Credentials → OAuth 2.0 Client")
        return

    if TOKEN_FILE.exists():
        print(f"Token already exists at {TOKEN_FILE}")
        print("Delete it and re-run if you want to re-authorize.")
        return

    print("\n" + "="*60)
    print("Google Drive Authorization")
    print("="*60)

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CREDS_FILE), SCOPES,
        redirect_uri="urn:ietf:wg:oauth:2.0:oob",
    )
    auth_url, _ = flow.authorization_url(prompt="consent")

    print("\n1. Open this URL in your browser:\n")
    print(f"   {auth_url}\n")
    print("2. Sign in with your Google account")
    print("3. Click 'Continue' on the warning screen")
    print("4. Copy the authorization code Google shows you")
    print("="*60)

    code = input("\nPaste the code here: ").strip()

    try:
        flow.fetch_token(code=code)
        creds = flow.credentials
        TOKEN_FILE.write_text(creds.to_json())
        print(f"\n✓ Authorization successful! Token saved to {TOKEN_FILE}")
        print("You won't need to do this again.\n")
    except Exception as e:
        print(f"\nERROR: {e}")
        print("Make sure you copied the full code from the browser.")


if __name__ == "__main__":
    main()
