from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from services.sheets_service import (
    get_all_records, find_row_by_id, append_row, update_row, delete_row,
    gen_id, now_str, add_audit_log,
)
from utils.templates import templates

router = APIRouter(prefix="/vendors", tags=["vendors"])


def get_user(request: Request):
    return request.session.get("user")


@router.get("")
async def vendors_page(request: Request):
    user = get_user(request)
    if not user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/auth/login-page")
    return templates.TemplateResponse(request=request, name="vendors.html", context={"user": user})


@router.get("/api/list")
async def list_vendors(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    return {"vendors": get_all_records("Vendors")}


@router.post("/api/add")
async def add_vendor(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    name = data.get("VendorName", "").strip()
    if not name:
        return JSONResponse({"error": "Vendor name is required"}, 400)
    vendors = get_all_records("Vendors")
    for v in vendors:
        if str(v.get("VendorName", "")).strip().lower() == name.lower():
            return JSONResponse({"error": "Vendor already exists"}, 400)
    vid = gen_id("VND")
    row = [
        vid, name,
        data.get("ContactPerson", ""),
        data.get("MobileNumber", ""),
        data.get("Email", ""),
        data.get("Address", ""),
        data.get("GSTNumber", ""),
        data.get("PaymentTerms", ""),
        data.get("Status", "Active"),
        now_str(), now_str(),
    ]
    append_row("Vendors", row)
    add_audit_log("CREATE", "Vendors", vid, f"Vendor '{name}' added", user["email"])
    return {"success": True, "vendor_id": vid}


@router.put("/api/{vendor_id}")
async def update_vendor(request: Request, vendor_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    result = find_row_by_id("Vendors", vendor_id)
    if not result:
        return JSONResponse({"error": "Vendor not found"}, 404)
    row_num, existing = result
    row = [
        vendor_id,
        data.get("VendorName", ""),
        data.get("ContactPerson", ""),
        data.get("MobileNumber", ""),
        data.get("Email", ""),
        data.get("Address", ""),
        data.get("GSTNumber", ""),
        data.get("PaymentTerms", ""),
        data.get("Status", "Active"),
        existing.get("CreatedDate", now_str()),
        now_str(),
    ]
    update_row("Vendors", row_num, row)
    add_audit_log("UPDATE", "Vendors", vendor_id, f"Vendor '{data.get('VendorName','')}' updated", user["email"])
    return {"success": True}


@router.delete("/api/{vendor_id}")
async def delete_vendor(request: Request, vendor_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    result = find_row_by_id("Vendors", vendor_id)
    if not result:
        return JSONResponse({"error": "Vendor not found"}, 404)
    row_num, record = result
    delete_row("Vendors", row_num)
    add_audit_log("DELETE", "Vendors", vendor_id, f"Vendor '{record.get('VendorName','')}' deleted", user["email"])
    return {"success": True}
