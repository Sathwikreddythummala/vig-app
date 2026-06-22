from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse
from services.sheets_service import (
    get_all_records, find_row_by_id, append_row, update_row, delete_row,
    gen_id, now_str, add_audit_log,
)
from services.drive_service import upload_file
from utils.templates import templates

router = APIRouter(prefix="/drivers", tags=["drivers"])


def get_user(request: Request):
    return request.session.get("user")


def _sync_user_access(email: str, name: str, emp_type: str, access_type: str):
    if not email or not email.strip():
        return
    email = email.strip().lower()
    if emp_type == "Employee" and access_type:
        users = get_all_records("Users")
        for idx, u in enumerate(users):
            if str(u.get("Email", "")).strip().lower() == email:
                from services.sheets_service import SHEET_HEADERS
                headers = SHEET_HEADERS["Users"]
                row_data = [str(u.get(h, "")) for h in headers]
                row_data[headers.index("Name")] = name
                row_data[headers.index("Role")] = access_type
                row_data[headers.index("Status")] = "Active"
                row_data[headers.index("UpdatedDate")] = now_str()
                update_row("Users", idx + 2, row_data)
                return
        append_row("Users", [gen_id("USR"), email, name, access_type, "Active", now_str(), now_str()])
    elif emp_type == "Driver":
        users = get_all_records("Users")
        for idx, u in enumerate(users):
            if str(u.get("Email", "")).strip().lower() == email:
                from services.sheets_service import SHEET_HEADERS
                headers = SHEET_HEADERS["Users"]
                row_data = [str(u.get(h, "")) for h in headers]
                row_data[headers.index("Status")] = "Inactive"
                row_data[headers.index("UpdatedDate")] = now_str()
                update_row("Users", idx + 2, row_data)
                return


@router.get("")
async def drivers_page(request: Request):
    user = get_user(request)
    if not user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/auth/login-page")
    return templates.TemplateResponse(request=request, name="drivers.html", context={"user": user})


@router.get("/api/list")
async def list_drivers(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    drivers = get_all_records("Drivers")
    return {"drivers": drivers}


@router.get("/api/{driver_id}")
async def get_driver(request: Request, driver_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    result = find_row_by_id("Drivers", driver_id)
    if not result:
        return JSONResponse({"error": "Driver not found"}, 404)
    _, record = result
    docs = get_all_records("Documents")
    driver_docs = [d for d in docs if d.get("EntityType") == "Driver" and d.get("EntityID") == driver_id]
    expenses = get_all_records("Expenses")
    driver_expenses = [e for e in expenses if str(e.get("DriverName", "")) == str(record.get("DriverName", ""))]
    return {"driver": record, "documents": driver_docs, "expenses": driver_expenses}


@router.post("/api/add")
async def add_driver(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    drivers = get_all_records("Drivers")
    lic = str(data.get("DrivingLicenseNumber", "")).strip().upper()
    if lic:
        for d in drivers:
            if str(d.get("DrivingLicenseNumber", "")).strip().upper() == lic:
                return JSONResponse({"error": "Driving license number already exists"}, 400)
    did = gen_id("DRV")
    row = [
        did,
        data.get("EmployeeType", "Driver"),
        data.get("DriverName", ""),
        data.get("Email", ""),
        data.get("MobileNumber", ""),
        data.get("EmergencyContact", ""),
        data.get("Address", ""),
        data.get("AadhaarNumber", ""),
        data.get("DrivingLicenseNumber", ""),
        data.get("LicenseExpiryDate", ""),
        data.get("BankName", ""),
        data.get("AccountNumber", ""),
        data.get("IFSCCode", ""),
        data.get("Salary", ""),
        data.get("JoiningDate", ""),
        data.get("Status", "Active"),
        data.get("AssignedVehicle", ""),
        now_str(),
        now_str(),
    ]
    append_row("Drivers", row)
    _sync_user_access(data.get("Email", ""), data.get("DriverName", ""), data.get("EmployeeType", "Driver"), data.get("AccessType", "viewer"))
    add_audit_log("CREATE", "Drivers", did, f"{data.get('EmployeeType','Driver')} {data.get('DriverName','')} added", user["email"])
    return {"success": True, "driver_id": did}


@router.put("/api/{driver_id}")
async def update_driver(request: Request, driver_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    result = find_row_by_id("Drivers", driver_id)
    if not result:
        return JSONResponse({"error": "Driver not found"}, 404)
    row_num, existing = result
    lic = str(data.get("DrivingLicenseNumber", "")).strip().upper()
    if lic:
        drivers = get_all_records("Drivers")
        for d in drivers:
            if str(d.get("DrivingLicenseNumber", "")).strip().upper() == lic and str(d.get("DriverID", "")) != driver_id:
                return JSONResponse({"error": "Driving license number already exists"}, 400)
    row = [
        driver_id,
        data.get("EmployeeType", existing.get("EmployeeType", "Driver")),
        data.get("DriverName", ""),
        data.get("Email", existing.get("Email", "")),
        data.get("MobileNumber", ""),
        data.get("EmergencyContact", ""),
        data.get("Address", ""),
        data.get("AadhaarNumber", ""),
        data.get("DrivingLicenseNumber", ""),
        data.get("LicenseExpiryDate", ""),
        data.get("BankName", ""),
        data.get("AccountNumber", ""),
        data.get("IFSCCode", ""),
        data.get("Salary", ""),
        data.get("JoiningDate", ""),
        data.get("Status", data.get("Status", "Active")),
        data.get("AssignedVehicle", ""),
        existing.get("CreatedDate", now_str()),
        now_str(),
    ]
    update_row("Drivers", row_num, row)
    _sync_user_access(data.get("Email", existing.get("Email", "")), data.get("DriverName", ""), data.get("EmployeeType", existing.get("EmployeeType", "Driver")), data.get("AccessType", "viewer"))
    add_audit_log("UPDATE", "Drivers", driver_id, f"{data.get('EmployeeType','Driver')} {data.get('DriverName','')} updated", user["email"])
    return {"success": True}


@router.delete("/api/{driver_id}")
async def delete_driver_api(request: Request, driver_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    result = find_row_by_id("Drivers", driver_id)
    if not result:
        return JSONResponse({"error": "Driver not found"}, 404)
    row_num, record = result
    delete_row("Drivers", row_num)
    add_audit_log("DELETE", "Drivers", driver_id, f"Driver {record.get('DriverName','')} deleted", user["email"])
    return {"success": True}


@router.post("/api/{driver_id}/upload")
async def upload_driver_doc(
    request: Request,
    driver_id: str,
    doc_type: str = Form(...),
    file: UploadFile = File(...),
):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    content = await file.read()
    result = upload_file(content, file.filename, file.content_type or "application/octet-stream", "Drivers", doc_type)
    doc_id = gen_id("DOC")
    append_row("Documents", [doc_id, "Driver", driver_id, doc_type, file.filename, result["view_url"], now_str()])
    add_audit_log("UPLOAD", "Documents", doc_id, f"Uploaded {doc_type} for driver {driver_id}", user["email"])
    return {"success": True, "document": result, "doc_id": doc_id}


@router.get("/details/{driver_id}")
async def driver_details_page(request: Request, driver_id: str):
    user = get_user(request)
    if not user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/auth/login-page")
    return templates.TemplateResponse(request=request, name="driver_details.html", context={"user": user, "driver_id": driver_id})
