from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse, StreamingResponse, RedirectResponse
from services.sheets_service import get_all_records, gen_id, now_str, add_audit_log
from services.db import execute
from utils.templates import templates
import io

router = APIRouter(prefix="/documents", tags=["documents"])


def get_user(request: Request):
    return request.session.get("user")


def _ensure_table():
    execute("""
        CREATE TABLE IF NOT EXISTS document_files (
            doc_id TEXT PRIMARY KEY,
            entity_type TEXT,
            entity_id TEXT,
            doc_type TEXT,
            file_name TEXT,
            mime_type TEXT,
            file_data BYTEA,
            uploaded_by TEXT,
            uploaded_date TEXT
        )
    """)


def _build_filename(row: dict) -> str:
    import re
    entity_type = row.get("entity_type", "")
    entity_id = row.get("entity_id", "")
    doc_type = row.get("doc_type", "Document")
    original = row.get("file_name", "file")
    ext = original.rsplit(".", 1)[-1] if "." in original else "pdf"

    if entity_type == "Vehicle":
        vehicles = get_all_records("Vehicles")
        v = next((v for v in vehicles if v.get("VehicleID") == entity_id), None)
        prefix = v.get("VehicleNumber", entity_id).replace(" ", "_") if v else entity_id
    elif entity_type == "Driver":
        drivers = get_all_records("Drivers")
        d = next((d for d in drivers if d.get("DriverID") == entity_id), None)
        prefix = d.get("DriverName", entity_id).replace(" ", "_") if d else entity_id
    elif entity_type == "GSTPurchase":
        purchases = get_all_records("GSTpurchases")
        p = next((p for p in purchases if p.get("PurchaseID") == entity_id), None)
        if p:
            month = str(p.get("InvoiceDate", ""))[:7]
            credit = str(p.get("CreditTo", "") or "NA")
            inv = str(p.get("InvoiceNumber", ""))
            prefix = f"gstpurchase_{month}_{credit}_{inv}"
        else:
            prefix = entity_id
    elif entity_type == "GST":
        prefix = entity_id
    else:
        prefix = entity_id

    prefix = re.sub(r'[\\/:*?"<>|\s]+', "-", prefix)
    return f"{prefix}_{doc_type}.{ext}"



@router.get("")
async def documents_page(request: Request):
    user = get_user(request)
    if not user:
        return RedirectResponse("/auth/login-page")
    return templates.TemplateResponse(request=request, name="documents.html", context={"user": user})


@router.get("/api/list")
async def list_docs(request: Request, entity_type: str = "", entity_id: str = ""):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    _ensure_table()
    sql = "SELECT doc_id, entity_type, entity_id, doc_type, file_name, mime_type, uploaded_by, uploaded_date FROM document_files WHERE 1=1"
    params = []
    if entity_type:
        sql += " AND entity_type = %s"
        params.append(entity_type)
    if entity_id:
        sql += " AND entity_id = %s"
        params.append(entity_id)
    sql += " ORDER BY uploaded_date DESC"
    rows = execute(sql, params, fetch=True)
    return [dict(r) for r in (rows or [])]


@router.get("/api/view/{doc_id}")
async def view_doc(request: Request, doc_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    _ensure_table()
    rows = execute("SELECT file_data, mime_type, file_name FROM document_files WHERE doc_id = %s", [doc_id], fetch=True)
    if not rows:
        return JSONResponse({"error": "Not found"}, 404)
    row = rows[0]
    mime = row["mime_type"] or "application/octet-stream"
    data = bytes(row["file_data"])
    return StreamingResponse(
        io.BytesIO(data),
        media_type=mime,
        headers={"Content-Disposition": f'inline; filename="{row["file_name"]}"'},
    )


@router.get("/api/download/{doc_id}")
async def download_doc(request: Request, doc_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    _ensure_table()
    rows = execute("SELECT file_data, mime_type, entity_type, entity_id, doc_type, file_name FROM document_files WHERE doc_id = %s", [doc_id], fetch=True)
    if not rows:
        return JSONResponse({"error": "Not found"}, 404)
    row = dict(rows[0])
    mime = row["mime_type"] or "application/octet-stream"
    data = bytes(row["file_data"])
    filename = _build_filename(row)
    return StreamingResponse(
        io.BytesIO(data),
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
    _ensure_table()
    content = await file.read()
    doc_id = gen_id("DOC")
    mime = file.content_type or "application/octet-stream"
    execute(
        "INSERT INTO document_files (doc_id, entity_type, entity_id, doc_type, file_name, mime_type, file_data, uploaded_by, uploaded_date) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        [doc_id, entity_type, entity_id, doc_type, file.filename, mime, content, user["email"], now_str()],
    )
    add_audit_log("UPLOAD", "Documents", doc_id, f"Uploaded {doc_type} for {entity_type} {entity_id}", user["email"])
    return {"success": True, "doc_id": doc_id}


@router.delete("/api/{doc_id}")
async def delete_doc(request: Request, doc_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    _ensure_table()
    rows = execute("SELECT doc_type, entity_type, entity_id FROM document_files WHERE doc_id = %s", [doc_id], fetch=True)
    if not rows:
        return JSONResponse({"error": "Not found"}, 404)
    row = rows[0]
    execute("DELETE FROM document_files WHERE doc_id = %s", [doc_id])
    add_audit_log("DELETE", "Documents", doc_id, f"Deleted {row['doc_type']} for {row['entity_type']} {row['entity_id']}", user["email"])
    return {"success": True}
