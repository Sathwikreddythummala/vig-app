import gspread
from google.oauth2.service_account import Credentials
from config import settings
from datetime import datetime
import uuid
import time
import threading

SCOPES = settings.SCOPES
_client = None
_spreadsheet = None
_spreadsheet_ts = 0
_SPREADSHEET_TTL = 300
_ws_cache = {}


def get_client() -> gspread.Client:
    global _client
    if _client is None:
        creds = Credentials.from_service_account_file(settings.SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        _client = gspread.authorize(creds)
    return _client


def get_spreadsheet():
    global _spreadsheet, _spreadsheet_ts
    now = time.time()
    if _spreadsheet is None or (now - _spreadsheet_ts) > _SPREADSHEET_TTL:
        _spreadsheet = get_client().open_by_key(settings.SPREADSHEET_ID)
        _spreadsheet_ts = now
        _ws_cache.clear()
    return _spreadsheet


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
        "CreatedDate",
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
        "BillID", "InvoiceNumber", "InvoiceDate", "VehicleNumber", "VendorName",
        "FixedAmount", "VariableAmount", "TrafficChallan", "Tollgates",
        "SubTotal", "SGST", "CGST", "TDS", "TotalAmount",
        "PaymentStatus", "PaidAmount", "BalanceAmount",
        "Description", "CreatedDate", "UpdatedDate",
    ],
    "Receivables": [
        "ReceivableID", "ReceiveDate", "BillID", "VendorName",
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
}


def initialize_sheets():
    ss = get_spreadsheet()
    existing = [ws.title for ws in ss.worksheets()]
    for sheet_name, headers in SHEET_HEADERS.items():
        if sheet_name not in existing:
            ws = ss.add_worksheet(title=sheet_name, rows=1000, cols=len(headers))
            ws.update("A1", [headers])
            ws.format("A1:{}1".format(chr(64 + len(headers))), {"textFormat": {"bold": True}})
        else:
            ws = ss.worksheet(sheet_name)
            row1 = ws.row_values(1)
            if row1 != headers:
                if not row1:
                    ws.update("A1", [headers])
                else:
                    records = ws.get_all_records()
                    ws.clear()
                    ws.update("A1", [headers])
                    for record in records:
                        row_data = [str(record.get(h, "")) for h in headers]
                        ws.append_row(row_data, value_input_option="USER_ENTERED")
                    try:
                        ws.format("A1:{}1".format(chr(64 + min(len(headers), 26))), {"textFormat": {"bold": True}})
                    except Exception:
                        pass
                    print(f"Updated headers for {sheet_name}")


def get_worksheet(name: str):
    if name not in _ws_cache:
        _ws_cache[name] = get_spreadsheet().worksheet(name)
    return _ws_cache[name]


def gen_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


_cache = {}
CACHE_TTL = 30


def invalidate_cache(sheet_name: str = ""):
    if sheet_name:
        _cache.pop(sheet_name, None)
    else:
        _cache.clear()


def get_all_records(sheet_name: str) -> list[dict]:
    now = time.time()
    if sheet_name in _cache and (now - _cache[sheet_name]["ts"]) < CACHE_TTL:
        return _cache[sheet_name]["data"]
    ws = get_worksheet(sheet_name)
    data = ws.get_all_records()
    _cache[sheet_name] = {"data": data, "ts": now}
    return data


def find_row_by_id(sheet_name: str, id_value: str) -> tuple[int, dict] | None:
    invalidate_cache(sheet_name)
    ws = get_worksheet(sheet_name)
    records = ws.get_all_records()
    _cache[sheet_name] = {"data": records, "ts": time.time()}
    id_col = SHEET_HEADERS[sheet_name][0]
    for idx, record in enumerate(records):
        if str(record.get(id_col, "")) == id_value:
            return idx + 2, record
    return None


def append_row(sheet_name: str, row_data: list):
    ws = get_worksheet(sheet_name)
    ws.append_row(row_data, value_input_option="USER_ENTERED")
    if sheet_name in _cache and sheet_name in SHEET_HEADERS:
        headers = SHEET_HEADERS[sheet_name]
        new_record = {headers[i]: row_data[i] if i < len(row_data) else "" for i in range(len(headers))}
        _cache[sheet_name]["data"].append(new_record)
    else:
        invalidate_cache(sheet_name)


def update_row(sheet_name: str, row_num: int, row_data: list):
    ws = get_worksheet(sheet_name)
    col_end = chr(64 + len(row_data)) if len(row_data) <= 26 else "Z"
    ws.update(f"A{row_num}:{col_end}{row_num}", [row_data], value_input_option="USER_ENTERED")
    if sheet_name in _cache and sheet_name in SHEET_HEADERS:
        headers = SHEET_HEADERS[sheet_name]
        idx = row_num - 2
        if 0 <= idx < len(_cache[sheet_name]["data"]):
            _cache[sheet_name]["data"][idx] = {headers[i]: row_data[i] if i < len(row_data) else "" for i in range(len(headers))}
    else:
        invalidate_cache(sheet_name)


def delete_row(sheet_name: str, row_num: int):
    ws = get_worksheet(sheet_name)
    ws.delete_rows(row_num)
    if sheet_name in _cache:
        idx = row_num - 2
        if 0 <= idx < len(_cache[sheet_name]["data"]):
            _cache[sheet_name]["data"].pop(idx)
    else:
        invalidate_cache(sheet_name)


def _write_audit_log(row_data):
    try:
        ws = get_worksheet("AuditLogs")
        ws.append_row(row_data, value_input_option="USER_ENTERED")
    except Exception:
        pass


def add_audit_log(action: str, module: str, entity_id: str, details: str, user_email: str):
    row_data = [gen_id("LOG"), action, module, entity_id, details, user_email, now_str()]
    threading.Thread(target=_write_audit_log, args=(row_data,), daemon=True).start()
