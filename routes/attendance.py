from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from services.sheets_service import (
    get_all_records, append_row, delete_row, update_row, find_row_by_id,
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
    driver_by_name = {str(d.get("DriverName", "")).strip(): d for d in drivers}
    vehicles = get_all_records("Vehicles")
    active_vehicles = [v for v in vehicles if str(v.get("VehicleStatus", "Active")).strip().lower() not in ("inactive", "sold")]
    attendance = get_all_records("Attendance")
    assignments = get_all_records("VehicleAssignments")

    # attendance status by driver for the date
    status_by_driver = {}
    if date:
        for a in attendance:
            if a.get("Date") == date:
                status_by_driver[a.get("DriverID")] = a

    def driver_on_vehicle(v):
        """Who drove this vehicle on the selected date: single-day override > covering range > default driver."""
        vnum = str(v.get("VehicleNumber", "")).strip()
        vid = v.get("VehicleID", "")
        default_driver = str(v.get("DefaultDriver", "")).strip()
        if not date:
            return default_driver
        matches_v = lambda a: str(a.get("VehicleID", "")) == str(vid) or str(a.get("VehicleNumber", "")).strip() == vnum
        single, rng, has_range = None, None, False
        for a in assignments:
            if matches_v(a):
                s, e = str(a.get("StartDate", ""))[:10], str(a.get("EndDate", ""))[:10]
                if s and s == e == date:
                    single = str(a.get("DriverName", "")).strip()
                elif s != e or not e:  # a real range
                    has_range = True
                    if s and s <= date and (not e or e >= date) and rng is None:
                        rng = str(a.get("DriverName", "")).strip()
        if single is not None:
            return single
        if rng is not None:
            return rng
        # a vehicle with range history but no range covering this date has no driver that day
        return "" if has_range else default_driver

    result = []
    for v in active_vehicles:
        dname = driver_on_vehicle(v)
        d = driver_by_name.get(dname, {})
        did = d.get("DriverID", "")
        rec = status_by_driver.get(did) if did else None
        status = (rec.get("Status", "Absent") if rec else "Present")
        result.append({
            "VehicleNumber": v.get("VehicleNumber", ""),
            "VehicleID": v.get("VehicleID", ""),
            "VehicleType": v.get("VehicleType", ""),
            "DriverName": dname,
            "DriverID": did,
            "DriverStatus": d.get("Status", "") or ("Active" if d else ""),
            "Status": status if dname else "",
            "AttendanceID": rec.get("AttendanceID", "") if rec else "",
        })
    result.sort(key=lambda x: str(x["VehicleNumber"]))
    with_driver = [r for r in result if r["DriverName"]]
    total = len(with_driver)
    absent_count = sum(1 for r in with_driver if r["Status"] == "Absent")
    half_count = sum(1 for r in with_driver if r["Status"] == "Half Day")
    present_count = total - absent_count - half_count
    return {"rows": result, "total": total, "absent": absent_count, "half_day": half_count, "present": present_count, "date": date}


@router.post("/api/mark")
async def mark(request: Request):
    """Set a driver's attendance for a date to Present / Half Day / Absent."""
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    if user.get("role") not in ("admin", "editor"):
        return JSONResponse({"error": "Only admins/editors can mark attendance"}, 403)
    data = await request.json()
    date = str(data.get("date", "")).strip()
    driver_id = str(data.get("driver_id", "")).strip()
    driver_name = str(data.get("driver_name", "")).strip()
    status = str(data.get("status", "")).strip()
    if not date or not driver_id or status not in ("Present", "Half Day", "Absent"):
        return JSONResponse({"error": "Date, driver and valid status required"}, 400)
    existing = None
    for a in get_all_records("Attendance"):
        if a.get("Date") == date and a.get("DriverID") == driver_id:
            existing = a
            break
    # "Present" means no exception row is stored
    if status == "Present":
        if existing:
            delete_row("Attendance", existing.get("AttendanceID", ""))
            add_audit_log("UPDATE", "Attendance", existing.get("AttendanceID", ""), f"{driver_name} marked Present on {date}", user["email"])
        return {"success": True}
    if existing:
        aid = existing.get("AttendanceID", "")
        row = [aid, date, driver_id, driver_name, status, user["email"], existing.get("CreatedDate", now_str())]
        update_row("Attendance", aid, row)
    else:
        aid = gen_id("ATT")
        append_row("Attendance", [aid, date, driver_id, driver_name, status, user["email"], now_str()])
    add_audit_log("CREATE", "Attendance", aid, f"{driver_name} marked {status} on {date}", user["email"])
    return {"success": True, "attendance_id": aid}


@router.post("/api/set-day-driver")
async def set_day_driver(request: Request):
    """Assign a driver to a vehicle for ONE day only (a single-day override).
    This moves that day's salary to the chosen driver; the regular driver loses it."""
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    if user.get("role") not in ("admin", "editor"):
        return JSONResponse({"error": "Only admins/editors can change assignments"}, 403)
    data = await request.json()
    date = str(data.get("date", "")).strip()
    driver_id = str(data.get("driver_id", "")).strip()
    driver_name = str(data.get("driver_name", "")).strip()
    vehicle_number = str(data.get("vehicle_number", "")).strip()
    if not date or not vehicle_number:
        return JSONResponse({"error": "Date and vehicle required"}, 400)
    # Remove any existing single-day override for this driver on this date, and for this
    # vehicle on this date (so one driver per vehicle-day, one vehicle per driver-day).
    while True:
        stale = None
        for a in get_all_records("VehicleAssignments"):
            s, e = str(a.get("StartDate", ""))[:10], str(a.get("EndDate", ""))[:10]
            if s == e == date and (str(a.get("DriverName", "")).strip() == driver_name
                                   or (vehicle_number and str(a.get("VehicleNumber", "")).strip() == vehicle_number)):
                stale = a.get("AssignmentID", "")
                break
        if not stale:
            break
        res = find_row_by_id("VehicleAssignments", stale)
        if res:
            delete_row("VehicleAssignments", res[0])
        else:
            break
    if driver_name and vehicle_number != "-":
        vid = ""
        for v in get_all_records("Vehicles"):
            if str(v.get("VehicleNumber", "")).strip() == vehicle_number:
                vid = v.get("VehicleID", "")
                break
        from services.sheets_service import build_row
        aid = gen_id("ASGN")
        vals = {"AssignmentID": aid, "VehicleID": vid, "VehicleNumber": vehicle_number,
                "DriverID": driver_id, "DriverName": driver_name,
                "StartDate": date, "EndDate": date, "CreatedDate": now_str(), "UpdatedDate": now_str()}
        append_row("VehicleAssignments", build_row("VehicleAssignments", vals))
        add_audit_log("CREATE", "VehicleAssignments", aid, f"{driver_name} drove {vehicle_number} on {date} (single day)", user["email"])
    else:
        add_audit_log("UPDATE", "VehicleAssignments", "", f"{vehicle_number} single-day driver cleared for {date}", user["email"])
    return {"success": True}


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
    active_drivers = [d for d in drivers if str(d.get("Status", "Active")).strip().lower() != "inactive" and str(d.get("EmployeeType", "Driver")).strip().lower() == "driver"]
    attendance = get_all_records("Attendance")
    if month:
        attendance = [a for a in attendance if str(a.get("Date", ""))[:7] == month]
    result = []
    for d in active_drivers:
        did = d.get("DriverID", "")
        recs = [a for a in attendance if a.get("DriverID") == did]
        absent_days = [a.get("Date") for a in recs if a.get("Status", "Absent") == "Absent"]
        half_days = [a.get("Date") for a in recs if a.get("Status", "") == "Half Day"]
        result.append({
            "DriverID": did,
            "DriverName": d.get("DriverName", ""),
            "EmployeeType": d.get("EmployeeType", "Driver"),
            "AbsentDays": len(absent_days),
            "AbsentDates": sorted(absent_days),
            "HalfDays": len(half_days),
            "HalfDates": sorted(half_days),
        })
    result.sort(key=lambda x: x["DriverName"])
    return {"summary": result, "month": month}
