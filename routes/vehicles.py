from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse
from services.sheets_service import (
    get_all_records, find_row_by_id, append_row, update_row, delete_row,
    gen_id, now_str, add_audit_log,
)
from services.drive_service import upload_file
from utils.templates import templates

router = APIRouter(prefix="/vehicles", tags=["vehicles"])


def get_user(request: Request):
    user = request.session.get("user")
    if not user:
        return None
    return user


@router.get("")
async def vehicles_page(request: Request):
    user = get_user(request)
    if not user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/auth/login-page")
    return templates.TemplateResponse(request=request, name="vehicles.html", context={"user": user})


@router.get("/api/list")
async def list_vehicles(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    vehicles = get_all_records("Vehicles")
    vehicles.sort(key=lambda v: str(v.get("VehicleNumber", "")))
    return {"vehicles": vehicles}


@router.post("/api/sync-drivers")
async def sync_drivers_to_vehicles(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    from services.sheets_service import SHEET_HEADERS, invalidate_cache
    drivers = get_all_records("Drivers")
    vehicles = get_all_records("Vehicles")
    veh_headers = SHEET_HEADERS["Vehicles"]
    drv_idx = veh_headers.index("DefaultDriver")
    veh_updated_idx = veh_headers.index("UpdatedDate")
    vehicle_driver_map = {}
    for d in drivers:
        veh = str(d.get("AssignedVehicle", "")).strip()
        name = str(d.get("DriverName", "")).strip()
        if veh and name and str(d.get("Status", "")) == "Active":
            vehicle_driver_map[veh] = name
    updated = 0
    for idx, v in enumerate(vehicles):
        vnum = str(v.get("VehicleNumber", "")).strip()
        current_driver = str(v.get("DefaultDriver", "")).strip()
        expected_driver = vehicle_driver_map.get(vnum, "")
        if current_driver != expected_driver:
            veh_row = [v.get(h, "") for h in veh_headers]
            veh_row[drv_idx] = expected_driver
            veh_row[veh_updated_idx] = now_str()
            update_row("Vehicles", idx + 2, veh_row)
            updated += 1
    invalidate_cache("Vehicles")
    add_audit_log("SYNC", "Vehicles", "", f"Synced drivers to {updated} vehicles", user["email"])
    return {"success": True, "updated": updated}


@router.get("/api/{vehicle_id}")
async def get_vehicle(request: Request, vehicle_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    result = find_row_by_id("Vehicles", vehicle_id)
    if not result:
        return JSONResponse({"error": "Vehicle not found"}, 404)
    _, record = result
    from services.db import execute as db_exec
    db_exec("CREATE TABLE IF NOT EXISTS document_files (doc_id TEXT PRIMARY KEY, entity_type TEXT, entity_id TEXT, doc_type TEXT, file_name TEXT, mime_type TEXT, file_data BYTEA, uploaded_by TEXT, uploaded_date TEXT)")
    vehicle_docs = db_exec("SELECT doc_id, entity_type, entity_id, doc_type, file_name, mime_type, uploaded_date FROM document_files WHERE entity_type='Vehicle' AND entity_id=%s ORDER BY uploaded_date DESC", [vehicle_id], fetch=True) or []
    vehicle_docs = [dict(d) for d in vehicle_docs]
    expenses = get_all_records("Expenses")
    vehicle_expenses = [e for e in expenses if str(e.get("VehicleNumber", "")) == str(record.get("VehicleNumber", ""))]
    return {"vehicle": record, "documents": vehicle_docs, "expenses": vehicle_expenses}


@router.post("/api/add")
async def add_vehicle(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    from services.sheets_service import invalidate_cache
    invalidate_cache("Vehicles")
    vehicles = get_all_records("Vehicles")
    new_vnum = str(data.get("VehicleNumber", "")).strip().upper()
    for v in vehicles:
        if str(v.get("VehicleNumber", "")).strip().upper() == new_vnum:
            return JSONResponse({"error": "Vehicle number already exists"}, 400)
    vid = gen_id("VEH")
    from services.sheets_service import build_row
    vals = {**data, "VehicleID": vid, "VehicleNumber": str(data.get("VehicleNumber", "")).strip().upper(), "VehicleStatus": data.get("VehicleStatus", "Active"), "LoanAvailable": data.get("LoanAvailable", "No"), "CreatedDate": now_str(), "UpdatedDate": now_str()}
    row = build_row("Vehicles", vals)
    append_row("Vehicles", row)
    add_audit_log("CREATE", "Vehicles", vid, f"Vehicle {data.get('VehicleNumber','')} added", user["email"])
    return {"success": True, "vehicle_id": vid}


@router.put("/api/{vehicle_id}")
async def update_vehicle(request: Request, vehicle_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    result = find_row_by_id("Vehicles", vehicle_id)
    if not result:
        return JSONResponse({"error": "Vehicle not found"}, 404)
    row_num, existing = result
    vehicles = get_all_records("Vehicles")
    new_num = str(data.get("VehicleNumber", "")).strip().upper()
    for v in vehicles:
        if str(v.get("VehicleNumber", "")).strip().upper() == new_num and str(v.get("VehicleID", "")) != vehicle_id:
            return JSONResponse({"error": "Vehicle number already exists"}, 400)
    from services.sheets_service import build_row
    vals = {**existing, **data, "VehicleID": vehicle_id, "VehicleNumber": new_num, "VehicleStatus": data.get("VehicleStatus", existing.get("VehicleStatus", "Active")), "LoanAvailable": data.get("LoanAvailable", existing.get("LoanAvailable", "No")), "CreatedDate": existing.get("CreatedDate", now_str()), "UpdatedDate": now_str()}
    row = build_row("Vehicles", vals)
    update_row("Vehicles", row_num, row)
    add_audit_log("UPDATE", "Vehicles", vehicle_id, f"Vehicle {new_num} updated", user["email"])
    return {"success": True}


@router.delete("/api/{vehicle_id}")
async def delete_vehicle_api(request: Request, vehicle_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    result = find_row_by_id("Vehicles", vehicle_id)
    if not result:
        return JSONResponse({"error": "Vehicle not found"}, 404)
    row_num, record = result
    delete_row("Vehicles", row_num)
    add_audit_log("DELETE", "Vehicles", vehicle_id, f"Vehicle {record.get('VehicleNumber','')} deleted", user["email"])
    return {"success": True}


@router.post("/api/{vehicle_id}/assign-vendor")
async def assign_vendor(request: Request, vehicle_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    new_vendor = data.get("vendor_name", "").strip()
    result = find_row_by_id("Vehicles", vehicle_id)
    if not result:
        return JSONResponse({"error": "Vehicle not found"}, 404)
    row_num, vehicle = result
    old_vendor = vehicle.get("DefaultVendor", "")
    vehicle_number = vehicle.get("VehicleNumber", "")
    from services.sheets_service import SHEET_HEADERS, now_str as ns
    headers = SHEET_HEADERS["Vehicles"]
    row_data = [vehicle.get(h, "") for h in headers]
    vendor_idx = headers.index("DefaultVendor")
    updated_idx = headers.index("UpdatedDate")
    row_data[vendor_idx] = new_vendor
    row_data[updated_idx] = ns()
    update_row("Vehicles", row_num, row_data)
    add_audit_log("ASSIGN", "Vehicles", vehicle_id, f"Vendor changed from '{old_vendor}' to '{new_vendor}' on {vehicle_number}", user["email"])
    return {"success": True}


@router.post("/api/{vehicle_id}/assign-driver")
async def assign_driver(request: Request, vehicle_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    new_driver = data.get("driver_name", "").strip()
    result = find_row_by_id("Vehicles", vehicle_id)
    if not result:
        return JSONResponse({"error": "Vehicle not found"}, 404)
    row_num, vehicle = result
    old_driver = vehicle.get("DefaultDriver", "")
    vehicle_number = vehicle.get("VehicleNumber", "")
    from services.sheets_service import get_all_records as gar, find_row_by_id as fri, update_row as ur, SHEET_HEADERS, now_str as ns
    headers = SHEET_HEADERS["Vehicles"]
    row_data = [vehicle.get(h, "") for h in headers]
    driver_idx = headers.index("DefaultDriver")
    updated_idx = headers.index("UpdatedDate")
    row_data[driver_idx] = new_driver
    row_data[updated_idx] = ns()
    ur("Vehicles", row_num, row_data)
    all_drivers = gar("Drivers")
    drv_headers = SHEET_HEADERS["Drivers"]
    assigned_idx = drv_headers.index("AssignedVehicle")
    drv_updated_idx = drv_headers.index("UpdatedDate")
    if old_driver:
        for idx, d in enumerate(all_drivers):
            if str(d.get("DriverName", "")).strip() == old_driver and str(d.get("AssignedVehicle", "")).strip() == vehicle_number:
                drv_row = [d.get(h, "") for h in drv_headers]
                drv_row[assigned_idx] = ""
                drv_row[drv_updated_idx] = ns()
                ur("Drivers", idx + 2, drv_row)
                break
    if new_driver:
        for idx, d in enumerate(all_drivers):
            if str(d.get("DriverName", "")).strip() == new_driver:
                drv_row = [d.get(h, "") for h in drv_headers]
                drv_row[assigned_idx] = vehicle_number
                drv_row[drv_updated_idx] = ns()
                ur("Drivers", idx + 2, drv_row)
                break
    add_audit_log("ASSIGN", "Vehicles", vehicle_id, f"Driver changed from '{old_driver}' to '{new_driver}' on {vehicle_number}", user["email"])
    return {"success": True}


@router.post("/api/{vehicle_id}/upload")
async def upload_vehicle_doc(
    request: Request,
    vehicle_id: str,
    doc_type: str = Form(...),
    file: UploadFile = File(...),
):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    content = await file.read()
    doc_id = gen_id("DOC")
    mime = file.content_type or "application/octet-stream"
    from services.db import execute as db_exec
    db_exec("CREATE TABLE IF NOT EXISTS document_files (doc_id TEXT PRIMARY KEY, entity_type TEXT, entity_id TEXT, doc_type TEXT, file_name TEXT, mime_type TEXT, file_data BYTEA, uploaded_by TEXT, uploaded_date TEXT)")
    db_exec("INSERT INTO document_files (doc_id,entity_type,entity_id,doc_type,file_name,mime_type,file_data,uploaded_by,uploaded_date) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        [doc_id, "Vehicle", vehicle_id, doc_type, file.filename, mime, content, user["email"], now_str()])
    add_audit_log("UPLOAD", "Documents", doc_id, f"Uploaded {doc_type} for vehicle {vehicle_id}", user["email"])
    return {"success": True, "doc_id": doc_id}


@router.get("/details/{vehicle_id}")
async def vehicle_details_page(request: Request, vehicle_id: str):
    user = get_user(request)
    if not user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/auth/login-page")
    return templates.TemplateResponse(request=request, name="vehicle_details.html", context={"user": user, "vehicle_id": vehicle_id})
