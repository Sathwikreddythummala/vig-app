"""
Direct Google Sheet management utility.
Usage:
  python manage_sheet.py list Vehicles
  python manage_sheet.py list Drivers
  python manage_sheet.py find Vehicles VehicleNumber TG08U1489
  python manage_sheet.py delete Vehicles 5          (deletes row 5)
  python manage_sheet.py cleanup Vehicles            (removes rows with empty ID)
"""
import sys
from config import settings
from services.sheets_service import get_spreadsheet, SHEET_HEADERS


def get_ws(sheet_name):
    return get_spreadsheet().worksheet(sheet_name)


def list_rows(sheet_name):
    ws = get_ws(sheet_name)
    records = ws.get_all_records()
    id_col = SHEET_HEADERS[sheet_name][0]
    print(f"\n{'Row':<5} {id_col:<20} ", end="")
    key_cols = SHEET_HEADERS[sheet_name][1:4]
    for c in key_cols:
        print(f"{c:<25}", end="")
    print(f"\n{'='*80}")
    for i, r in enumerate(records):
        print(f"{i+2:<5} {str(r.get(id_col,'')):<20} ", end="")
        for c in key_cols:
            print(f"{str(r.get(c,'')):<25}", end="")
        print()
    print(f"\nTotal: {len(records)} rows")


def find_rows(sheet_name, col_name, value):
    ws = get_ws(sheet_name)
    records = ws.get_all_records()
    value_upper = value.strip().upper()
    matches = []
    for i, r in enumerate(records):
        if str(r.get(col_name, "")).strip().upper() == value_upper:
            matches.append((i + 2, r))
    if not matches:
        print(f"No rows found where {col_name} = '{value}'")
        return
    print(f"\nFound {len(matches)} match(es):")
    for row_num, r in matches:
        print(f"\n--- Row {row_num} ---")
        for k, v in r.items():
            if v:
                print(f"  {k}: {v}")


def delete_row(sheet_name, row_num):
    ws = get_ws(sheet_name)
    row_vals = ws.row_values(row_num)
    print(f"Deleting row {row_num}: {row_vals[:4]}")
    confirm = input("Are you sure? (y/n): ")
    if confirm.lower() == 'y':
        ws.delete_rows(row_num)
        print("Deleted.")
    else:
        print("Cancelled.")


def cleanup(sheet_name):
    ws = get_ws(sheet_name)
    all_vals = ws.get_all_values()
    deleted = 0
    for i in range(len(all_vals) - 1, 0, -1):
        if not str(all_vals[i][0]).strip():
            print(f"Deleting empty-ID row {i+1}: {all_vals[i][:4]}")
            ws.delete_rows(i + 1)
            deleted += 1
    print(f"Cleaned up {deleted} rows.")


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
        delete_row(sheet, int(sys.argv[3]))
    elif cmd == "cleanup":
        cleanup(sheet)
    else:
        print(__doc__)
