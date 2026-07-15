from config import settings
from datetime import datetime
from zoneinfo import ZoneInfo
_IST = ZoneInfo("Asia/Kolkata")
import uuid
import time
import threading
from services.db import execute, initialize_db


SHEET_HEADERS = {
    "Vehicles": [
        "VehicleID", "VehicleNumber", "VehicleType", "DefaultDriver", "DefaultVendor", "VehicleStatus",
        "RCNumber", "RCExpiry",
        "InsurancePolicyNumber", "InsuranceCompany", "InsuranceStartDate", "InsuranceExpiryDate",
        "PermitNumber", "PermitExpiryDate",
        "FitnessExpiryDate", "PUCExpiryDate",
        "LoanAvailable", "BankName", "LoanAccountNumber",
        "EMIAmount", "EMIDate",
        "LoanStartDate", "LoanEndDate",
        "CreatedDate", "UpdatedDate",
    ],
    "Drivers": [
        "DriverID", "EmployeeType", "DriverName", "Email", "MobileNumber", "EmergencyContact", "Address",
        "AadhaarNumber",
        "DrivingLicenseNumber", "LicenseExpiryDate",
        "BankName", "AccountNumber", "IFSCCode",
        "Salary", "JoiningDate",
        "Status", "ExitDate", "AssignedVehicle",
        "CreatedDate", "UpdatedDate",
    ],
    "Expenses": [
        "ExpenseID", "ExpenseDate", "ForMonth", "ExpenseFor", "VehicleNumber", "DriverName",
        "Category", "SubCategory", "Description", "Amount",
        "PaymentMode", "PaidBy", "CreatedDate",
    ],
    "Documents": [
        "DocumentID", "EntityType", "EntityID", "DocumentType",
        "FileName", "DriveURL", "UploadedDate",
    ],
    "Settings": ["Key", "Value", "UpdatedDate"],
    "AuditLogs": ["LogID", "Action", "Module", "EntityID", "Details", "UserEmail", "Timestamp"],
    "OutsideVehicles": [
        "OVID", "VehicleNumber", "OwnerName", "MobileNumber",
        "BankName", "AccountNumber", "IFSCCode",
        "Status", "CreatedDate", "UpdatedDate",
    ],
    "OutsideTransactions": [
        "TransID", "Date", "ForMonth", "VehicleNumber", "OwnerName",
        "Type", "Category", "Description", "Amount",
        "PaymentMode", "CreatedDate",
    ],
    "GSTpurchases": [
        "PurchaseID", "InvoiceDate", "InvoiceNumber", "CompanyName",
        "Amount", "SGST", "CGST", "TotalAmount", "Description",
        "CreditTo", "CreatedDate",
    ],
    "Purse": [
        "PurseID", "Date", "Holder", "Type", "Amount", "Description",
        "VehicleNumber", "Category", "ReferenceID", "CreatedDate",
    ],
    "Users": [
        "UserID", "Email", "Name", "Role", "Status", "CreatedDate", "UpdatedDate",
    ],
    "Vendors": [
        "VendorID", "VendorName", "ContactPerson", "MobileNumber", "Email",
        "Address", "GSTNumber", "PaymentTerms", "Status",
        "CreatedDate", "UpdatedDate",
    ],
    "Income": [
        "IncomeID", "IncomeDate", "VehicleNumber", "DriverName",
        "VendorName", "TripFrom", "TripTo", "Material",
        "Quantity", "Unit", "Rate", "Amount",
        "InvoiceNumber", "PaymentStatus", "PaymentDate",
        "Description", "CreatedDate",
    ],
    "Billing": [
        "BillID", "InvoiceNumber", "BillType", "InvoiceDate", "PaymentMonth", "VehicleNumber", "VendorName",
        "FixedAmount", "VariableAmount", "TrafficChallan", "Tollgates",
        "SubTotal", "SGST", "CGST", "TDS", "TotalAmount",
        "PaymentStatus", "PaidAmount", "BalanceAmount",
        "Description", "InvoiceDescription", "CreatedDate", "UpdatedDate",
    ],
    "Receivables": [
        "ReceivableID", "ReceiveDate", "PaymentMonth", "BillID", "VendorName",
        "Amount", "PaymentMode", "ReferenceNumber", "Description",
        "CreatedDate",
    ],
    "FuelEntries": [
        "FuelID", "EntryDate", "VehicleNumber", "DriverName",
        "FuelType", "Litres", "Amount", "Kilometre",
        "FuelStation", "PaymentMode", "CreatedDate",
    ],
    "OtherEMIs": [
        "EMIID", "EMIName", "Category", "Description", "VehicleNumber",
        "LenderName", "TotalAmount", "DownPayment", "EMIAmount", "EMIDate",
        "StartDate", "EndDate", "TotalInstallments", "PaidInstallments",
        "Status", "CreatedDate", "UpdatedDate",
    ],
    "Attendance": [
        "AttendanceID", "Date", "DriverID", "DriverName", "Status", "MarkedBy", "CreatedDate",
    ],
    "Incentives": [
        "IncentiveID", "DriverID", "DriverName", "ForMonth", "Amount",
        "Description", "EnteredBy", "CreatedDate", "UpdatedDate",
    ],
}


def initialize_sheets():
    initialize_db(SHEET_HEADERS)


def gen_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


def now_str() -> str:
    return datetime.now(_IST).strftime("%Y-%m-%d %H:%M:%S")


def today_str() -> str:
    return datetime.now(_IST).strftime("%Y-%m-%d")


_cache = {}
CACHE_TTL = 5


def invalidate_cache(sheet_name: str = ""):
    if sheet_name:
        _cache.pop(sheet_name, None)
    else:
        _cache.clear()


def get_all_records(sheet_name: str) -> list[dict]:
    now = time.time()
    if sheet_name in _cache and (now - _cache[sheet_name]["ts"]) < CACHE_TTL:
        return list(_cache[sheet_name]["data"])
    headers = SHEET_HEADERS[sheet_name]
    cols = ", ".join(f'"{h}"' for h in headers)
    rows = execute(f'SELECT {cols} FROM "{sheet_name}"', fetch=True)
    result = []
    for row in rows:
        record = {}
        for h in headers:
            val = row.get(h, "") or ""
            record[h] = val
        result.append(record)
    _cache[sheet_name] = {"data": result, "ts": now}
    return list(result)


def find_row_by_id(sheet_name: str, id_value: str) -> tuple[str, dict] | None:
    invalidate_cache(sheet_name)
    headers = SHEET_HEADERS[sheet_name]
    id_col = headers[0]
    cols = ", ".join(f'"{h}"' for h in headers)
    rows = execute(f'SELECT {cols} FROM "{sheet_name}" WHERE "{id_col}" = %s', (str(id_value).strip(),), fetch=True)
    if not rows:
        return None
    record = {}
    for h in headers:
        record[h] = rows[0].get(h, "") or ""
    return id_value, record


def build_row(sheet_name: str, vals: dict) -> list:
    return [str(vals.get(h, "")) for h in SHEET_HEADERS[sheet_name]]


def append_row(sheet_name: str, row_data: list):
    headers = SHEET_HEADERS[sheet_name]
    cols = ", ".join(f'"{h}"' for h in headers)
    placeholders = ", ".join(["%s"] * len(headers))
    values = [str(row_data[i]) if i < len(row_data) else "" for i in range(len(headers))]
    execute(f'INSERT INTO "{sheet_name}" ({cols}) VALUES ({placeholders})', values)
    invalidate_cache(sheet_name)


def update_row(sheet_name: str, row_id, row_data: list):
    headers = SHEET_HEADERS[sheet_name]
    id_col = headers[0]
    entity_id = str(row_id) if isinstance(row_id, str) else str(row_data[0])
    set_clause = ", ".join(f'"{h}" = %s' for h in headers)
    values = [str(row_data[i]) if i < len(row_data) else "" for i in range(len(headers))]
    values.append(entity_id)
    execute(f'UPDATE "{sheet_name}" SET {set_clause} WHERE "{id_col}" = %s', values)
    invalidate_cache(sheet_name)


def delete_row(sheet_name: str, row_id):
    headers = SHEET_HEADERS[sheet_name]
    id_col = headers[0]
    execute(f'DELETE FROM "{sheet_name}" WHERE "{id_col}" = %s', (str(row_id),))
    invalidate_cache(sheet_name)


def _write_audit_log(row_data):
    try:
        headers = SHEET_HEADERS["AuditLogs"]
        cols = ", ".join(f'"{h}"' for h in headers)
        placeholders = ", ".join(["%s"] * len(headers))
        values = [str(row_data[i]) if i < len(row_data) else "" for i in range(len(headers))]
        execute(f'INSERT INTO "AuditLogs" ({cols}) VALUES ({placeholders})', values)
    except Exception:
        pass


def add_audit_log(action: str, module: str, entity_id: str, details: str, user_email: str):
    row_data = [gen_id("LOG"), action, module, entity_id, details, user_email, now_str()]
    threading.Thread(target=_write_audit_log, args=(row_data,), daemon=True).start()
