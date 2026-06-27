from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse
from services.sheets_service import (
    get_all_records, find_row_by_id, append_row, update_row, delete_row,
    gen_id, now_str, add_audit_log, get_worksheet,
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


@router.get("/api/{vehicle_id}")
async def get_vehicle(request: Request, vehicle_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    result = find_row_by_id("Vehicles", vehicle_id)
    if not result:
        return JSONResponse({"error": "Vehicle not found"}, 404)
    _, record = result
    docs = get_all_records("Documents")
    vehicle_docs = [d for d in docs if d.get("EntityType") == "Vehicle" and d.get("EntityID") == vehicle_id]
    expenses = get_all_records("Expenses")
    vehicle_expenses = [e for e in expenses if str(e.get("VehicleNumber", "")) == str(record.get("VehicleNumber", ""))]
    return {"vehicle": record, "documents": vehicle_docs, "expenses": vehicle_expenses}


@router.post("/api/add")
async def add_vehicle(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    vehicles = get_all_records("Vehicles")
    for v in vehicles:
        if str(v.get("VehicleNumber", "")).strip().upper() == str(data.get("VehicleNumber", "")).strip().upper():
            return JSONResponse({"error": "Vehicle number already exists"}, 400)
    vid = gen_id("VEH")
    row = [
        vid,
        str(data.get("VehicleNumber", "")).strip().upper(),
        data.get("VehicleType", ""),
        data.get("DefaultDriver", ""),
        data.get("DefaultVendor", ""),
        data.get("VehicleStatus", "Active"),
        data.get("RCNumber", ""),
        data.get("RCExpiry", ""),
        data.get("InsurancePolicyNumber", ""),
        data.get("InsuranceCompany", ""),
        data.get("InsuranceStartDate", ""),
        data.get("InsuranceExpiryDate", ""),
        data.get("PermitNumber", ""),
        data.get("PermitExpiryDate", ""),
        data.get("FitnessExpiryDate", ""),
        data.get("PUCExpiryDate", ""),
        data.get("LoanAvailable", "No"),
        data.get("BankName", ""),
        data.get("LoanAccountNumber", ""),
        data.get("EMIAmount", ""),
        data.get("EMIDate", ""),
        data.get("LoanStartDate", ""),
        data.get("LoanEndDate", ""),
        now_str(),
        now_str(),
    ]
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
    row = [
        vehicle_id,
        new_num,
        data.get("VehicleType", ""),
        data.get("DefaultDriver", ""),
        data.get("DefaultVendor", ""),
        data.get("VehicleStatus", existing.get("VehicleStatus", "Active")),
        data.get("RCNumber", ""),
        data.get("RCExpiry", ""),
        data.get("InsurancePolicyNumber", ""),
        data.get("InsuranceCompany", ""),
        data.get("InsuranceStartDate", ""),
        data.get("InsuranceExpiryDate", ""),
        data.get("PermitNumber", ""),
        data.get("PermitExpiryDate", ""),
        data.get("FitnessExpiryDate", ""),
        data.get("PUCExpiryDate", ""),
        data.get("LoanAvailable", "No"),
        data.get("BankName", ""),
        data.get("LoanAccountNumber", ""),
        data.get("EMIAmount", ""),
        data.get("EMIDate", ""),
        data.get("LoanStartDate", ""),
        data.get("LoanEndDate", ""),
        existing.get("CreatedDate", now_str()),
        now_str(),
    ]
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
    result = upload_file(content, file.filename, file.content_type or "application/octet-stream", "Vehicles", doc_type)
    doc_id = gen_id("DOC")
    from services.sheets_service import append_row as sa, now_str as ns
    sa("Documents", [doc_id, "Vehicle", vehicle_id, doc_type, file.filename, result["view_url"], ns()])
    add_audit_log("UPLOAD", "Documents", doc_id, f"Uploaded {doc_type} for vehicle {vehicle_id}", user["email"])
    return {"success": True, "document": result, "doc_id": doc_id}


@router.get("/details/{vehicle_id}")
async def vehicle_details_page(request: Request, vehicle_id: str):
    user = get_user(request)
    if not user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/auth/login-page")
    return templates.TemplateResponse(request=request, name="vehicle_details.html", context={"user": user, "vehicle_id": vehicle_id})
