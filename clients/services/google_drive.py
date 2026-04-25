"""Google Drive integration for per-client document folders.

Uses Application Default Credentials (ADC) — `google.auth.default()` picks up
whichever credential is present:

- Local dev (Mac): `gcloud auth application-default login` — user's own Google
  account. Folders are created in that user's Drive under the root folder below.
- GCP (Cloud Run/GCE/GKE): the attached service account / Workload Identity.
  No key files, no env changes.

Env vars (in .env):
    GOOGLE_DRIVE_ROOT_FOLDER_ID — ID of the "All Clients" root folder

Folder URL pattern: https://drive.google.com/drive/folders/<folder_id>
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

_SCOPES = ["https://www.googleapis.com/auth/drive"]
_FOLDER_MIME = "application/vnd.google-apps.folder"


class DriveNotConfigured(RuntimeError):
    """Raised when Drive isn't set up correctly (missing root folder or no ADC)."""


def _root_folder_id() -> str:
    root_id = os.environ.get("GOOGLE_DRIVE_ROOT_FOLDER_ID", "").strip()
    if not root_id:
        raise DriveNotConfigured(
            "Set GOOGLE_DRIVE_ROOT_FOLDER_ID in .env to the 'All Clients' folder ID."
        )
    return root_id


@lru_cache(maxsize=1)
def _service():
    import google.auth
    from google.auth import impersonated_credentials
    from google.auth.exceptions import DefaultCredentialsError
    from googleapiclient.discovery import build

    try:
        base_creds, _project = google.auth.default()
    except DefaultCredentialsError as e:
        raise DriveNotConfigured(
            "No Google credentials found. Run: gcloud auth application-default login"
        ) from e

    sa_email = os.environ.get("GOOGLE_DRIVE_IMPERSONATE_SA", "").strip()
    if sa_email:
        creds = impersonated_credentials.Credentials(
            source_credentials=base_creds,
            target_principal=sa_email,
            target_scopes=_SCOPES,
            lifetime=3600,
        )
    else:
        creds = base_creds.with_scopes(_SCOPES) if hasattr(base_creds, "with_scopes") else base_creds

    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _folder_url(folder_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{folder_id}"


def _find_subfolder(service, parent_id: str, name: str) -> Optional[str]:
    # Escape single quotes in the folder name for the q parameter.
    safe_name = name.replace("'", "\\'")
    q = (
        f"'{parent_id}' in parents and "
        f"mimeType = '{_FOLDER_MIME}' and "
        f"name = '{safe_name}' and trashed = false"
    )
    resp = service.files().list(
        q=q,
        fields="files(id, name)",
        pageSize=1,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    files = resp.get("files", [])
    return files[0]["id"] if files else None


def get_or_create_client_folder(client_name: str, client_id: int) -> tuple[str, str]:
    """Return (folder_id, folder_url) for this client's doc folder under the root.

    Folder name pattern: "<client_name> (#<client_id>)" — the id disambiguates clients
    with identical names and makes the folder easy to match back.
    """
    service = _service()
    root_id = _root_folder_id()

    folder_name = f"{client_name} (#{client_id})"

    existing = _find_subfolder(service, root_id, folder_name)
    if existing:
        return existing, _folder_url(existing)

    metadata = {
        "name": folder_name,
        "mimeType": _FOLDER_MIME,
        "parents": [root_id],
    }
    created = service.files().create(
        body=metadata,
        fields="id",
        supportsAllDrives=True,
    ).execute()
    folder_id = created["id"]
    return folder_id, _folder_url(folder_id)
