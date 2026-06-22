from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from config import settings
import io

SCOPES = settings.SCOPES
_service = None

FOLDER_STRUCTURE = {
    "Fleet Documents": {
        "Vehicles": ["RC", "Insurance", "Permit", "Fitness", "PUC"],
        "Drivers": ["License", "Aadhaar", "Passbook", "Photos"],
    }
}


def get_drive_service():
    global _service
    if _service is None:
        creds = Credentials.from_service_account_file(settings.SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        _service = build("drive", "v3", credentials=creds)
    return _service


def find_folder(name: str, parent_id: str) -> str | None:
    svc = get_drive_service()
    q = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and '{parent_id}' in parents and trashed=false"
    results = svc.files().list(q=q, fields="files(id,name)", spaces="drive").execute()
    files = results.get("files", [])
    return files[0]["id"] if files else None


def create_folder(name: str, parent_id: str) -> str:
    svc = get_drive_service()
    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    folder = svc.files().create(body=metadata, fields="id").execute()
    return folder["id"]


def ensure_folder(name: str, parent_id: str) -> str:
    fid = find_folder(name, parent_id)
    if fid:
        return fid
    return create_folder(name, parent_id)


def initialize_drive_folders():
    root_id = settings.GOOGLE_DRIVE_FOLDER_ID
    if not root_id:
        return {}
    folder_ids = {}
    fleet_id = ensure_folder("Fleet Documents", root_id)
    folder_ids["Fleet Documents"] = fleet_id
    for category, subs in FOLDER_STRUCTURE["Fleet Documents"].items():
        cat_id = ensure_folder(category, fleet_id)
        folder_ids[category] = cat_id
        for sub in subs:
            sub_id = ensure_folder(sub, cat_id)
            folder_ids[f"{category}/{sub}"] = sub_id
    return folder_ids


_folder_cache = {}


def get_folder_ids() -> dict:
    global _folder_cache
    if not _folder_cache:
        _folder_cache = initialize_drive_folders()
    return _folder_cache


def upload_file(file_content: bytes, filename: str, mime_type: str, entity_type: str, doc_type: str) -> dict:
    svc = get_drive_service()
    folder_ids = get_folder_ids()
    folder_key = f"{entity_type}/{doc_type}"
    parent_id = folder_ids.get(folder_key, folder_ids.get(entity_type, settings.GOOGLE_DRIVE_FOLDER_ID))
    metadata = {"name": filename, "parents": [parent_id]}
    media = MediaIoBaseUpload(io.BytesIO(file_content), mimetype=mime_type, resumable=True)
    uploaded = svc.files().create(body=metadata, media_body=media, fields="id,webViewLink,webContentLink").execute()
    svc.permissions().create(fileId=uploaded["id"], body={"type": "anyone", "role": "reader"}).execute()
    return {
        "file_id": uploaded["id"],
        "view_url": uploaded.get("webViewLink", ""),
        "download_url": uploaded.get("webContentLink", ""),
        "preview_url": f"https://drive.google.com/file/d/{uploaded['id']}/preview",
    }


def delete_file(file_id: str):
    svc = get_drive_service()
    svc.files().delete(fileId=file_id).execute()
