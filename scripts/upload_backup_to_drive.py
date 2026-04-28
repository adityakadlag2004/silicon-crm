"""Upload a backup file to Google Drive using the existing service account.

Called by scripts/backup_db.sh — only runs if GOOGLE_DRIVE_BACKUP_FOLDER_ID
and GOOGLE_APPLICATION_CREDENTIALS are set.
"""
import os
import sys
import mimetypes

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


def main():
    if len(sys.argv) != 2:
        print("usage: upload_backup_to_drive.py <file>", file=sys.stderr)
        sys.exit(2)

    path = sys.argv[1]
    folder_id = os.environ["GOOGLE_DRIVE_BACKUP_FOLDER_ID"]
    key_file = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]

    creds = service_account.Credentials.from_service_account_file(
        key_file, scopes=["https://www.googleapis.com/auth/drive.file"]
    )
    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    name = os.path.basename(path)
    mime, _ = mimetypes.guess_type(path)
    media = MediaFileUpload(path, mimetype=mime or "application/octet-stream", resumable=True)
    metadata = {"name": name, "parents": [folder_id]}

    file = service.files().create(body=metadata, media_body=media, fields="id,webViewLink").execute()
    print(f"Uploaded {name} → {file.get('webViewLink', file['id'])}")


if __name__ == "__main__":
    main()
