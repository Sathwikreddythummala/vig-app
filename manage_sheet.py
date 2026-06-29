"""
Direct database management utility.
Usage:
  python manage_sheet.py list Vehicles
  python manage_sheet.py list Drivers
  python manage_sheet.py find Vehicles VehicleNumber TG08U1489
  python manage_sheet.py delete Vehicles VEH-ABC123
  python manage_sheet.py count Vehicles
"""
import sys
from services.sheets_service import SHEET_HEADERS, get_all_records, find_row_by_id, delete_row
from services.db import execute


def list_rows(sheet_name):
    records = get_all_records(sheet_name)
    headers = SHEET_HEADERS[sheet_name]
    key_cols = headers[:4]
    print(f"\n{'#':<5}", end="")
    for c in key_cols:
        print(f"{c:<25}", end="")
    print(f"\n{'='*80}")
    for i, r in enumerate(records):
        print(f"{i+1:<5}", end="")
        for c in key_cols:
            print(f"{str(r.get(c,'')):<25}", end="")
        print()
    print(f"\nTotal: {len(records)} rows")


def find_rows(sheet_name, col_name, value):
    records = get_all_records(sheet_name)
    value_upper = value.strip().upper()
    matches = [(i, r) for i, r in enumerate(records) if str(r.get(col_name, "")).strip().upper() == value_upper]
    if not matches:
        print(f"No rows found where {col_name} = '{value}'")
        return
    print(f"\nFound {len(matches)} match(es):")
    for _, r in matches:
        print(f"\n--- {r.get(SHEET_HEADERS[sheet_name][0], '')} ---")
        for k, v in r.items():
            if v:
                print(f"  {k}: {v}")


def delete_by_id(sheet_name, entity_id):
    result = find_row_by_id(sheet_name, entity_id)
    if not result:
        print(f"Not found: {entity_id}")
        return
    _, record = result
    print(f"Deleting: {record}")
    confirm = input("Are you sure? (y/n): ")
    if confirm.lower() == 'y':
        delete_row(sheet_name, entity_id)
        print("Deleted.")
    else:
        print("Cancelled.")


def count_rows(sheet_name):
    rows = execute(f'SELECT COUNT(*) as cnt FROM "{sheet_name}"', fetch=True)
    print(f"{sheet_name}: {rows[0]['cnt']} rows")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    sheet = sys.argv[2]
    if cmd == "list":
        list_rows(sheet)
    elif cmd == "find" and len(sys.argv) >= 5:
        find_rows(sheet, sys.argv[3], sys.argv[4])
    elif cmd == "delete" and len(sys.argv) >= 4:
        delete_by_id(sheet, sys.argv[3])
    elif cmd == "count":
        count_rows(sheet)
    else:
        print(__doc__)
