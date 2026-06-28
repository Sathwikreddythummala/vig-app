from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from services.sheets_service import (
    get_all_records, append_row, delete_row, get_worksheet,
    gen_id, now_str, add_audit_log, invalidate_cache,
)
from utils.templates import templates

router = APIRouter(prefix="/attendance", tags=["attendance"])


def get_user(request: Request):
    return request.session.get("user")


@router.get("")
async def attendance_page(request: Request):
    user = get_user(request)
    if not user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/auth/login-page")
    return templates.TemplateResponse(request=request, name="attendance.html", context={"user": user})


@router.get("/api/list")
async def list_attendance(request: Request, date: str = ""):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    drivers = get_all_records("Drivers")
    active_drivers = [d for d in drivers if str(d.get("Status", "Active")).strip().lower() != "inactive"]
    attendance = get_all_records("Attendance")
    absents = {}
    if date:
        for a in attendance:
            if a.get("Date") == date:
                absents[a.get("DriverID")] = a.get("AttendanceID")
    result = []
    for d in active_drivers:
        did = d.get("DriverID", "")
        result.append({
            "DriverID": did,
            "DriverName": d.get("DriverName", ""),
            "EmployeeType": d.get("EmployeeType", "Driver"),
            "AssignedVehicle": d.get("AssignedVehicle", ""),
            "IsAbsent": did in absents,
            "AttendanceID": absents.get(did, ""),
        })
    result.sort(key=lambda x: x["DriverName"])
    total = len(result)
    absent_count = sum(1 for r in result if r["IsAbsent"])
    return {"drivers": result, "total": total, "absent": absent_count, "present": total - absent_count, "date": date}


@router.post("/api/mark-absent")
async def mark_absent(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    if user.get("role") not in ("admin", "editor"):
        return JSONResponse({"error": "Only admins/editors can mark attendance"}, 403)
    data = await request.json()
    date = data.get("date", "")
    driver_id = data.get("driver_id", "")
    driver_name = data.get("driver_name", "")
    if not date or not driver_id:
        return JSONResponse({"error": "Date and driver required"}, 400)
    attendance = get_all_records("Attendance")
    for a in attendance:
        if a.get("Date") == date and a.get("DriverID") == driver_id:
            return JSONResponse({"error": "Already marked absent"}, 400)
    aid = gen_id("ATT")
    append_row("Attendance", [aid, date, driver_id, driver_name, "Absent", user["email"], now_str()])
    add_audit_log("CREATE", "Attendance", aid, f"{driver_name} marked absent on {date}", user["email"])
    return {"success": True, "attendance_id": aid}


@router.post("/api/mark-present")
async def mark_present(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    if user.get("role") not in ("admin", "editor"):
        return JSONResponse({"error": "Only admins/editors can mark attendance"}, 403)
    data = await request.json()
    attendance_id = data.get("attendance_id", "")
    if not attendance_id:
        return JSONResponse({"error": "Attendance ID required"}, 400)
    records = get_all_records("Attendance")
    for idx, a in enumerate(records):
        if a.get("AttendanceID") == attendance_id:
            delete_row("Attendance", idx + 2)
            add_audit_log("DELETE", "Attendance", attendance_id, f"{a.get('DriverName','')} unmarked absent on {a.get('Date','')}", user["email"])
            return {"success": True}
    return JSONResponse({"error": "Record not found"}, 404)


@router.get("/api/summary")
async def attendance_summary(request: Request, month: str = ""):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    drivers = get_all_records("Drivers")
    active_drivers = [d for d in drivers if str(d.get("Status", "Active")).strip().lower() != "inactive"]
    attendance = get_all_records("Attendance")
    if month:
        attendance = [a for a in attendance if str(a.get("Date", ""))[:7] == month]
    result = []
    for d in active_drivers:
        did = d.get("DriverID", "")
        absent_days = [a.get("Date") for a in attendance if a.get("DriverID") == did]
        result.append({
            "DriverID": did,
            "DriverName": d.get("DriverName", ""),
            "EmployeeType": d.get("EmployeeType", "Driver"),
            "AbsentDays": len(absent_days),
            "AbsentDates": sorted(absent_days),
        })
    result.sort(key=lambda x: x["DriverName"])
    return {"summary": result, "month": month}
