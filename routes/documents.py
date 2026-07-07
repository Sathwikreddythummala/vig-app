from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse, StreamingResponse, RedirectResponse
from services.sheets_service import get_all_records, append_row, gen_id, now_str, add_audit_log, find_row_by_id, delete_row
from services.drive_service import upload_file, get_drive_service, delete_file
import re
import io

router = APIRouter(prefix="/documents", tags=["documents"])


def get_user(request: Request):
    return request.session.get("user")


def _file_id_from_url(url: str) -> str:
    m = re.search(r"/file/d/([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)
    m = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)
    return ""


def _build_filename(doc: dict) -> str:
    entity_type = doc.get("EntityType", "")
    entity_id = doc.get("EntityID", "")
    doc_type = doc.get("DocumentType", "Unknown")
    original = doc.get("FileName", "file")
    ext = original.rsplit(".", 1)[-1] if "." in original else "pdf"

    if entity_type == "Vehicle":
        vehicles = get_all_records("Vehicles")
        v = next((v for v in vehicles if v.get("VehicleID") == entity_id), None)
        prefix = v.get("VehicleNumber", entity_id).replace(" ", "_") if v else entity_id
    elif entity_type == "Driver":
        drivers = get_all_records("Drivers")
        d = next((d for d in drivers if d.get("DriverID") == entity_id), None)
        prefix = d.get("DriverName", entity_id).replace(" ", "_") if d else entity_id
    elif entity_type == "GST":
        prefix = entity_id  # entity_id is the month e.g. 2026-07
    else:
        prefix = entity_id

    return f"{prefix}_{doc_type}.{ext}"


@router.get("/api/list")
async def list_docs(request: Request, entity_type: str = "", entity_id: str = ""):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    docs = get_all_records("Documents")
    if entity_type:
        docs = [d for d in docs if d.get("EntityType") == entity_type]
    if entity_id:
        docs = [d for d in docs if d.get("EntityID") == entity_id]
    docs.sort(key=lambda d: str(d.get("UploadedDate", "")), reverse=True)
    return docs


@router.get("/api/view/{doc_id}")
async def view_doc(request: Request, doc_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    docs = get_all_records("Documents")
    doc = next((d for d in docs if d.get("DocumentID") == doc_id), None)
    if not doc:
        return JSONResponse({"error": "Not found"}, 404)
    file_id = _file_id_from_url(doc.get("DriveURL", ""))
    preview_url = f"https://drive.google.com/file/d/{file_id}/preview"
    return RedirectResponse(preview_url)


@router.get("/api/download/{doc_id}")
async def download_doc(request: Request, doc_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    docs = get_all_records("Documents")
    doc = next((d for d in docs if d.get("DocumentID") == doc_id), None)
    if not doc:
        return JSONResponse({"error": "Not found"}, 404)

    file_id = _file_id_from_url(doc.get("DriveURL", ""))
    if not file_id:
        return JSONResponse({"error": "Invalid file URL"}, 400)

    svc = get_drive_service()
    file_meta = svc.files().get(fileId=file_id, fields="mimeType,name").execute()
    mime = file_meta.get("mimeType", "application/octet-stream")

    request_obj = svc.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    from googleapiclient.http import MediaIoBaseDownload
    downloader = MediaIoBaseDownload(buf, request_obj)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    buf.seek(0)

    filename = _build_filename(doc)
    return StreamingResponse(
        buf,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/upload")
async def upload_doc(
    request: Request,
    file: UploadFile = File(...),
    entity_type: str = Form(...),
    entity_id: str = Form(...),
    doc_type: str = Form(...),
):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    content = await file.read()
    result = upload_file(content, file.filename, file.content_type or "application/octet-stream", entity_type, doc_type)
    doc_id = gen_id("DOC")
    append_row("Documents", [doc_id, entity_type, entity_id, doc_type, file.filename, result["view_url"], now_str()])
    add_audit_log("UPLOAD", "Documents", doc_id, f"Uploaded {doc_type} for {entity_type} {entity_id}", user["email"])
    return {"success": True, "doc_id": doc_id, "view_url": result["view_url"]}


@router.delete("/api/{doc_id}")
async def delete_doc(request: Request, doc_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    docs = get_all_records("Documents")
    doc = next((d for d in docs if d.get("DocumentID") == doc_id), None)
    if not doc:
        return JSONResponse({"error": "Not found"}, 404)
    file_id = _file_id_from_url(doc.get("DriveURL", ""))
    try:
        if file_id:
            delete_file(file_id)
    except Exception:
        pass
    result = find_row_by_id("Documents", doc_id)
    if result:
        delete_row("Documents", result[0])
    add_audit_log("DELETE", "Documents", doc_id, f"Deleted {doc.get('DocumentType','')} for {doc.get('EntityType','')} {doc.get('EntityID','')}", user["email"])
    return {"success": True}
