from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from services.sheets_service import (
    get_all_records, find_row_by_id, append_row, update_row, delete_row,
    gen_id, now_str, add_audit_log,
)
from utils.templates import templates

router = APIRouter(prefix="/access", tags=["access"])


def get_admin_user(request: Request):
    user = request.session.get("user")
    if not user or user.get("role") != "admin":
        return None
    return user


@router.get("")
async def access_page(request: Request):
    user = get_admin_user(request)
    if not user:
        return RedirectResponse("/")
    from config import settings
    return templates.TemplateResponse(request=request, name="access.html", context={"user": user, "allowed_emails": settings.ALLOWED_EMAILS})


@router.get("/api/list")
async def list_users(request: Request):
    user = request.session.get("user")
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    users = get_all_records("Users")
    return {"users": users}


@router.post("/api/add")
async def add_user(request: Request):
    user = get_admin_user(request)
    if not user:
        return JSONResponse({"error": "Admin access required"}, 403)
    data = await request.json()
    email = str(data.get("Email", "")).strip().lower()
    if not email:
        return JSONResponse({"error": "Email is required"}, 400)
    users = get_all_records("Users")
    for u in users:
        if str(u.get("Email", "")).strip().lower() == email:
            return JSONResponse({"error": "User already exists"}, 400)
    uid = gen_id("USR")
    row = [
        uid, email, data.get("Name", ""),
        data.get("Role", "viewer"),
        data.get("Status", "Active"),
        now_str(), now_str(),
    ]
    append_row("Users", row)
    add_audit_log("CREATE", "Users", uid, f"User {email} added as {data.get('Role','viewer')}", user["email"])
    return {"success": True, "user_id": uid}


@router.put("/api/{user_id}")
async def update_user(request: Request, user_id: str):
    user = get_admin_user(request)
    if not user:
        return JSONResponse({"error": "Admin access required"}, 403)
    data = await request.json()
    result = find_row_by_id("Users", user_id)
    if not result:
        return JSONResponse({"error": "User not found"}, 404)
    row_num, existing = result
    row = [
        user_id,
        data.get("Email", existing.get("Email", "")),
        data.get("Name", existing.get("Name", "")),
        data.get("Role", existing.get("Role", "viewer")),
        data.get("Status", existing.get("Status", "Active")),
        existing.get("CreatedDate", now_str()),
        now_str(),
    ]
    update_row("Users", row_num, row)
    add_audit_log("UPDATE", "Users", user_id, f"User {data.get('Email','')} role changed to {data.get('Role','')}", user["email"])
    return {"success": True}


@router.delete("/api/{user_id}")
async def delete_user(request: Request, user_id: str):
    user = get_admin_user(request)
    if not user:
        return JSONResponse({"error": "Admin access required"}, 403)
    result = find_row_by_id("Users", user_id)
    if not result:
        return JSONResponse({"error": "User not found"}, 404)
    row_num, record = result
    delete_row("Users", row_num)
    add_audit_log("DELETE", "Users", user_id, f"User {record.get('Email','')} removed", user["email"])
    return {"success": True}
