"""
Google Drive uploader.

First run: opens browser to authorize access → saves token.json.
Subsequent runs: loads saved token automatically.

Setup (one-time):
  1. Go to https://console.cloud.google.com
  2. Create a project → Enable "Google Drive API"
  3. Go to APIs & Services → Credentials → Create OAuth 2.0 Client ID
  4. Application type: Desktop app
  5. Download JSON → save as credentials/google_credentials.json
  6. Add GOOGLE_DRIVE_ENABLED=true to .env
"""

import os
import json
from pathlib import Path

# Lazy import so the rest of the app works even if google libs aren't installed
try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    _GOOGLE_AVAILABLE = True
except ImportError:
    _GOOGLE_AVAILABLE = False

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
CREDS_DIR = Path("credentials")
CREDS_FILE = CREDS_DIR / "google_credentials.json"
TOKEN_FILE = CREDS_DIR / "token.json"


def is_enabled() -> bool:
    return os.getenv("GOOGLE_DRIVE_ENABLED", "").lower() == "true"


def _authenticate():
    """Returns an authenticated Google Drive service, or None on failure."""
    if not _GOOGLE_AVAILABLE:
        print("[Drive] google-api-python-client not installed. Run: pip install google-api-python-client google-auth-oauthlib")
        return None

    if not CREDS_FILE.exists():
        print(
            "\n" + "="*60 +
            "\n[Drive] google_credentials.json not found." +
            f"\n  Expected: {CREDS_FILE.absolute()}" +
            "\n  → Go to https://console.cloud.google.com" +
            "\n  → Create project → Enable Drive API" +
            "\n  → Credentials → Create OAuth 2.0 Client ID (Desktop)" +
            "\n  → Download JSON → save it as credentials/google_credentials.json" +
            "\n" + "="*60
        )
        return None

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None

        if not creds:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDS_FILE), SCOPES,
                redirect_uri="urn:ietf:wg:oauth:2.0:oob",
            )
            auth_url, _ = flow.authorization_url(prompt="consent")
            print("\n" + "="*60)
            print("[Drive] Open this URL in your browser:")
            print(f"\n  {auth_url}\n")
            print("After clicking 'Continue', Google will show you a code.")
            print("Copy that code and paste it here.")
            print("="*60)
            code = input("  Paste code: ").strip()
            flow.fetch_token(code=code)
            creds = flow.credentials

        CREDS_DIR.mkdir(exist_ok=True)
        TOKEN_FILE.write_text(creds.to_json())
        print("[Drive] Token saved. You won't need to log in again.")

    return build("drive", "v3", credentials=creds)


def _get_or_create_folder(service, name: str, parent_id: str = None) -> str:
    """Returns the folder ID, creating it if it doesn't exist."""
    query = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        query += f" and '{parent_id}' in parents"

    results = service.files().list(q=query, fields="files(id, name)").execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]

    meta = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        meta["parents"] = [parent_id]

    folder = service.files().create(body=meta, fields="id").execute()
    return folder["id"]


def upload_file(local_path: str, subfolder: str = None) -> str | None:
    """
    Uploads a file to Google Drive.

    Folder structure in Drive:
      Job Search Automation/
        └── <subfolder>/        (optional, e.g. "Applications", "Reports")
              └── <filename>

    Returns the Drive file URL, or None if upload failed / Drive disabled.
    """
    if not is_enabled():
        return None

    local_path = Path(local_path)
    if not local_path.exists():
        return None

    try:
        service = _authenticate()
        if not service:
            return None

        root_folder_name = os.getenv("GOOGLE_DRIVE_FOLDER", "Job Search Automation")
        root_id = _get_or_create_folder(service, root_folder_name)

        parent_id = root_id
        if subfolder:
            parent_id = _get_or_create_folder(service, subfolder, parent_id=root_id)

        # Guess MIME type
        suffix = local_path.suffix.lower()
        mime_map = {
            ".pdf":  "application/pdf",
            ".txt":  "text/plain",
            ".html": "text/html",
            ".json": "application/json",
        }
        mime = mime_map.get(suffix, "application/octet-stream")

        file_meta = {
            "name": local_path.name,
            "parents": [parent_id],
        }
        media = MediaFileUpload(str(local_path), mimetype=mime, resumable=False)
        result = service.files().create(
            body=file_meta, media_body=media, fields="id, webViewLink"
        ).execute()

        link = result.get("webViewLink", "")
        print(f"[Drive] Uploaded: {local_path.name} → {link}")
        return link

    except Exception as e:
        print(f"[Drive] Upload failed for {local_path.name}: {e}")
        return None


def upload_files_for_job(pdf_path: str, cl_path: str) -> tuple[str | None, str | None]:
    """Uploads resume PDF and cover letter. Returns (pdf_link, cl_link)."""
    pdf_link = upload_file(pdf_path, subfolder="Applications")
    cl_link  = upload_file(cl_path,  subfolder="Applications")
    return pdf_link, cl_link


def upload_report(report_path: str) -> str | None:
    """Uploads an HTML report. Returns the Drive link."""
    return upload_file(report_path, subfolder="Reports")
