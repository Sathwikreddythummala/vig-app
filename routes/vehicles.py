from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse
from services.sheets_service import (
    get_all_records, find_row_by_id, append_row, update_row, delete_row,
    gen_id, now_str, add_audit_log,
)
from services.drive_service import upload_file
from utils.templates import templates

router = APIRouter(prefix="/vehicles", tags=["vehicles"])


def _record_assignment_change(vehicle_id, vehicle_number, old_driver, new_driver, changeover_date, driver_id="", exit_date=""):
    """Close the vehicle's current open assignment (at the outgoing driver's exit date)
    and open a new one for the incoming driver from the entry date, so each driver's
    period on the vehicle is preserved (used for pro-rata salary)."""
    from datetime import datetime, timedelta
    from services.sheets_service import build_row, SHEET_HEADERS
    assignments = get_all_records("VehicleAssignments")
    # close any open assignment for this vehicle (EndDate blank) at the exit date
    for idx, a in enumerate(assignments):
        if str(a.get("VehicleID", "")) == str(vehicle_id) and not str(a.get("EndDate", "")).strip():
            if exit_date:
                end = exit_date
            else:
                try:
                    end = (datetime.strptime(changeover_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
                except (ValueError, TypeError):
                    end = changeover_date
            headers = SHEET_HEADERS["VehicleAssignments"]
            row = [a.get(h, "") for h in headers]
            row[headers.index("EndDate")] = end
            row[headers.index("UpdatedDate")] = now_str()
            update_row("VehicleAssignments", idx + 2, row)
    # open a new assignment for the incoming driver from the entry date
    if new_driver:
        aid = gen_id("ASGN")
        vals = {
            "AssignmentID": aid, "VehicleID": vehicle_id, "VehicleNumber": vehicle_number,
            "DriverID": driver_id, "DriverName": new_driver,
            "StartDate": changeover_date, "EndDate": "",
            "CreatedDate": now_str(), "UpdatedDate": now_str(),
        }
        append_row("VehicleAssignments", build_row("VehicleAssignments", vals))


def _inactive_driver_names():
    return {str(d.get("DriverName", "")).strip() for d in get_all_records("Drivers")
            if str(d.get("Status", "Active")).strip().lower() == "inactive"}


def _open_driver_map(inactive=None):
    """{vehicle_number: current driver} from the open assignment period (EndDate blank,
    latest start). Inactive drivers are skipped — they can't be a current driver."""
    if inactive is None:
        inactive = _inactive_driver_names()
    out = {}
    for a in get_all_records("VehicleAssignments"):
        if str(a.get("EndDate", "")).strip():
            continue
        vn = str(a.get("VehicleNumber", "")).strip()
        name = str(a.get("DriverName", "")).strip()
        if not vn or not name or name in inactive:
            continue
        prev = out.get(vn)
        if not prev or str(a.get("StartDate", "")) >= str(prev[1]):
            out[vn] = (name, str(a.get("StartDate", "")))
    return {vn: v[0] for vn, v in out.items()}


def reconcile_driver_vehicle():
    """Make Drivers.AssignedVehicle and Vehicles.DefaultDriver consistent with reality:
      - a driver is the current driver of AT MOST ONE vehicle,
      - inactive drivers hold no vehicle and no open assignment,
      - each vehicle's DefaultDriver = its current open-assignment driver (active only).
    Returns a summary dict. Safe to run repeatedly."""
    from services.sheets_service import SHEET_HEADERS, invalidate_cache
    from datetime import datetime
    invalidate_cache()
    inactive = _inactive_driver_names()

    # 1. Close open assignments held by inactive drivers (end at their exit date / today)
    drv_exit = {}
    for d in get_all_records("Drivers"):
        drv_exit[str(d.get("DriverName", "")).strip()] = str(d.get("ExitDate", "")).strip()[:10]
    a_headers = SHEET_HEADERS["VehicleAssignments"]
    closed = 0
    for a in get_all_records("VehicleAssignments"):
        nm = str(a.get("DriverName", "")).strip()
        if nm in inactive and not str(a.get("EndDate", "")).strip():
            end = drv_exit.get(nm) or now_str()[:10]
            res = find_row_by_id("VehicleAssignments", a.get("AssignmentID", ""))
            if res:
                rn, ex = res
                row = [ex.get(h, "") for h in a_headers]
                row[a_headers.index("EndDate")] = end
                row[a_headers.index("UpdatedDate")] = now_str()
                update_row("VehicleAssignments", rn, row)
                closed += 1
    invalidate_cache("VehicleAssignments")

    # 1b. A driver can drive only ONE vehicle at a time. If a driver has >1 OPEN
    #     assignment, keep the latest-start one and close the earlier ones (a vehicle
    #     may still have many drivers over the month — that's separate periods).
    from collections import defaultdict
    from datetime import timedelta
    open_by_driver = defaultdict(list)
    for a in get_all_records("VehicleAssignments"):
        if not str(a.get("EndDate", "")).strip() and str(a.get("DriverName", "")).strip():
            open_by_driver[str(a.get("DriverName", "")).strip()].append(a)
    for _drv, lst in open_by_driver.items():
        if len(lst) <= 1:
            continue
        lst.sort(key=lambda a: str(a.get("StartDate", "")))
        keep = lst[-1]
        try:
            end = (datetime.strptime(str(keep.get("StartDate", ""))[:10], "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            end = now_str()[:10]
        for a in lst[:-1]:
            res = find_row_by_id("VehicleAssignments", a.get("AssignmentID", ""))
            if res:
                rn, ex = res
                row = [ex.get(h, "") for h in a_headers]
                row[a_headers.index("EndDate")] = end
                row[a_headers.index("UpdatedDate")] = now_str()
                update_row("VehicleAssignments", rn, row)
                closed += 1
    invalidate_cache("VehicleAssignments")

    # 2. Effective current driver per active vehicle (open assignment else DefaultDriver, active only)
    omap = _open_driver_map(inactive)
    veh_current = {}   # vehicle_number -> driver
    v_headers = SHEET_HEADERS["Vehicles"]
    vehicles = get_all_records("Vehicles")
    for v in vehicles:
        if str(v.get("VehicleStatus", "Active")).strip().lower() in ("inactive", "sold", "scrapped"):
            continue
        vn = str(v.get("VehicleNumber", "")).strip()
        drv = omap.get(vn)
        if not drv:
            dd = str(v.get("DefaultDriver", "")).strip()
            drv = dd if dd and dd not in inactive else ""
        if drv:
            veh_current[vn] = drv

    # a driver current on >1 vehicle: keep the open-assignment one, else the first
    seen = {}
    for vn, drv in list(veh_current.items()):
        if drv in seen:
            keep_open = vn in omap
            prev_vn = seen[drv]
            if keep_open and prev_vn not in omap:
                veh_current.pop(prev_vn, None); seen[drv] = vn
            else:
                veh_current.pop(vn, None)
        else:
            seen[drv] = vn

    # 3. Sync Vehicles.DefaultDriver to the effective driver.
    #    A vehicle not in veh_current has no current driver: clear a stale
    #    DefaultDriver if that person is inactive or now drives ANOTHER vehicle
    #    (otherwise leave it as-is, e.g. a standalone or inactive/sold vehicle's
    #    historical driver that conflicts with nothing).
    claimed = {drv: vn for vn, drv in veh_current.items()}  # driver -> their one current vehicle
    veh_fixed = 0
    for idx, v in enumerate(get_all_records("Vehicles")):
        vn = str(v.get("VehicleNumber", "")).strip()
        dd = str(v.get("DefaultDriver", "")).strip()
        if vn in veh_current:
            want = veh_current[vn]
        elif dd and (dd in inactive or (dd in claimed and claimed[dd] != vn)):
            want = ""
        else:
            want = dd
        if dd != want:
            row = [v.get(h, "") for h in v_headers]
            row[v_headers.index("DefaultDriver")] = want
            row[v_headers.index("UpdatedDate")] = now_str()
            update_row("Vehicles", idx + 2, row)
            veh_fixed += 1
    invalidate_cache("Vehicles")

    # 4. Set each driver's AssignedVehicle to the one vehicle they currently drive (else clear)
    driver_vehicle = {drv: vn for vn, drv in veh_current.items()}
    d_headers = SHEET_HEADERS["Drivers"]
    drv_fixed = 0
    for idx, d in enumerate(get_all_records("Drivers")):
        nm = str(d.get("DriverName", "")).strip()
        want = "" if nm in inactive else driver_vehicle.get(nm, "")
        if str(d.get("AssignedVehicle", "")).strip() != want:
            row = [d.get(h, "") for h in d_headers]
            row[d_headers.index("AssignedVehicle")] = want
            row[d_headers.index("UpdatedDate")] = now_str()
            update_row("Drivers", idx + 2, row)
            drv_fixed += 1
    invalidate_cache()
    return {"closed_inactive_assignments": closed, "vehicles_fixed": veh_fixed, "drivers_fixed": drv_fixed}


def _sync_default_driver(vehicle_id):
    """Set a vehicle's DefaultDriver to its current open-assignment driver (if any)."""
    from services.sheets_service import SHEET_HEADERS
    res = find_row_by_id("Vehicles", vehicle_id)
    if not res:
        return
    row_num, v = res
    vnum = str(v.get("VehicleNumber", "")).strip()
    name = _open_driver_map().get(vnum)
    if name is None:
        return  # no open assignment -> leave DefaultDriver untouched
    headers = SHEET_HEADERS["Vehicles"]
    if str(v.get("DefaultDriver", "")).strip() != name:
        row = [v.get(h, "") for h in headers]
        row[headers.index("DefaultDriver")] = name
        row[headers.index("UpdatedDate")] = now_str()
        update_row("Vehicles", row_num, row)


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
    vehicles = [{**v} for v in get_all_records("Vehicles")]
    # The current driver is the OPEN assignment period (source of truth), not the
    # possibly-stale DefaultDriver field. Show that on the cards/lists.
    open_by_vehicle = {}
    for a in get_all_records("VehicleAssignments"):
        if str(a.get("EndDate", "")).strip():
            continue
        vn = str(a.get("VehicleNumber", "")).strip()
        prev = open_by_vehicle.get(vn)
        if not prev or str(a.get("StartDate", "")) >= str(prev.get("StartDate", "")):
            open_by_vehicle[vn] = a
    for v in vehicles:
        cur = open_by_vehicle.get(str(v.get("VehicleNumber", "")).strip())
        if cur and str(cur.get("DriverName", "")).strip():
            v["DefaultDriver"] = cur.get("DriverName", "")
    vehicles.sort(key=lambda v: str(v.get("VehicleNumber", "")))
    return {"vehicles": vehicles}


@router.post("/api/sync-drivers")
async def sync_drivers_to_vehicles(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    summary = reconcile_driver_vehicle()
    add_audit_log("SYNC", "Vehicles", "", f"Reconciled drivers/vehicles {summary}", user["email"])
    return {"success": True, "updated": summary.get("drivers_fixed", 0) + summary.get("vehicles_fixed", 0), **summary}


@router.get("/api/{vehicle_id}")
async def get_vehicle(request: Request, vehicle_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    result = find_row_by_id("Vehicles", vehicle_id)
    if not result:
        return JSONResponse({"error": "Vehicle not found"}, 404)
    _, record = result
    # show the current driver from the open assignment (not the possibly-stale field)
    cur = _open_driver_map().get(str(record.get("VehicleNumber", "")).strip())
    if cur:
        record = {**record, "DefaultDriver": cur}
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
    entry_date = str(data.get("entry_date", "")).strip()
    exit_date = str(data.get("exit_date", "")).strip()
    changeover_date = entry_date or str(data.get("changeover_date", "")).strip() or now_str()[:10]
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
        status_idx = drv_headers.index("Status")
        exit_idx = drv_headers.index("ExitDate")
        for idx, d in enumerate(all_drivers):
            if str(d.get("DriverName", "")).strip() == new_driver:
                drv_row = [d.get(h, "") for h in drv_headers]
                drv_row[assigned_idx] = vehicle_number
                # A driver taking over a vehicle is working again — reactivate if inactive.
                drv_row[status_idx] = "Active"
                drv_row[exit_idx] = ""
                drv_row[drv_updated_idx] = ns()
                ur("Drivers", idx + 2, drv_row)
                break
    # record assignment history (for pro-rata vehicle-wise salary)
    if str(old_driver).strip() != str(new_driver).strip():
        new_driver_id = ""
        for d in all_drivers:
            if str(d.get("DriverName", "")).strip() == new_driver:
                new_driver_id = d.get("DriverID", "")
                break
        _record_assignment_change(vehicle_id, vehicle_number, old_driver, new_driver, changeover_date, new_driver_id, exit_date=exit_date)
    reconcile_driver_vehicle()  # a driver can only be current on one vehicle; keep everything consistent
    add_audit_log("ASSIGN", "Vehicles", vehicle_id, f"Driver changed from '{old_driver}' to '{new_driver}' on {vehicle_number} (exit {exit_date or '-'}, entry {changeover_date})", user["email"])
    return {"success": True}


@router.get("/api/{vehicle_id}/assignments")
async def list_assignments(request: Request, vehicle_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    result = find_row_by_id("Vehicles", vehicle_id)
    vnum = result[1].get("VehicleNumber", "") if result else ""
    asg = [a for a in get_all_records("VehicleAssignments") if str(a.get("VehicleID", "")) == str(vehicle_id)]
    asg.sort(key=lambda x: str(x.get("StartDate", "")))
    return {"assignments": asg, "vehicle_number": vnum}


@router.post("/api/{vehicle_id}/assignments")
async def add_assignment(request: Request, vehicle_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    driver_name = str(data.get("driver_name", "")).strip()
    start = str(data.get("start_date", "")).strip()
    end = str(data.get("end_date", "")).strip()
    if not driver_name or not start:
        return JSONResponse({"error": "Driver and start date are required"}, 400)
    result = find_row_by_id("Vehicles", vehicle_id)
    if not result:
        return JSONResponse({"error": "Vehicle not found"}, 404)
    vnum = result[1].get("VehicleNumber", "")
    did = ""
    for d in get_all_records("Drivers"):
        if str(d.get("DriverName", "")).strip() == driver_name:
            did = d.get("DriverID", "")
            break
    from services.sheets_service import build_row
    aid = gen_id("ASGN")
    vals = {
        "AssignmentID": aid, "VehicleID": vehicle_id, "VehicleNumber": vnum,
        "DriverID": did, "DriverName": driver_name,
        "StartDate": start, "EndDate": end,
        "CreatedDate": now_str(), "UpdatedDate": now_str(),
    }
    append_row("VehicleAssignments", build_row("VehicleAssignments", vals))
    reconcile_driver_vehicle()
    add_audit_log("CREATE", "VehicleAssignments", aid, f"{driver_name} on {vnum}: {start} to {end or 'open'}", user["email"])
    return {"success": True, "assignment_id": aid}


@router.put("/api/assignments/{assignment_id}")
async def update_assignment(request: Request, assignment_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    data = await request.json()
    result = find_row_by_id("VehicleAssignments", assignment_id)
    if not result:
        return JSONResponse({"error": "Not found"}, 404)
    row_num, existing = result
    from services.sheets_service import build_row
    vals = {**existing, **{k: v for k, v in data.items() if k in ("StartDate", "EndDate", "DriverName")},
            "AssignmentID": assignment_id, "UpdatedDate": now_str()}
    update_row("VehicleAssignments", row_num, build_row("VehicleAssignments", vals))
    reconcile_driver_vehicle()
    add_audit_log("UPDATE", "VehicleAssignments", assignment_id, "Assignment period updated", user["email"])
    return {"success": True}


@router.delete("/api/assignments/{assignment_id}")
async def delete_assignment(request: Request, assignment_id: str):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, 401)
    result = find_row_by_id("VehicleAssignments", assignment_id)
    if not result:
        return JSONResponse({"error": "Not found"}, 404)
    row_num, record = result
    delete_row("VehicleAssignments", row_num)
    reconcile_driver_vehicle()
    add_audit_log("DELETE", "VehicleAssignments", assignment_id, f"Assignment removed ({record.get('DriverName','')})", user["email"])
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
