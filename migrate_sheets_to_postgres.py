"""
One-time migration: Google Sheets → Neon Postgres
Run: python migrate_sheets_to_postgres.py
"""
import gspread
from google.oauth2.service_account import Credentials
from config import settings
from services.sheets_service import SHEET_HEADERS
from services.db import initialize_db, execute

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_sheets_client():
    creds = Credentials.from_service_account_file(settings.SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return gspread.authorize(creds)


def migrate():
    print("Connecting to Google Sheets...")
    client = get_sheets_client()
    ss = client.open_by_key(settings.SPREADSHEET_ID)

    print("Initializing Postgres tables...")
    initialize_db(SHEET_HEADERS)

    existing_sheets = [ws.title for ws in ss.worksheets()]

    for table_name, headers in SHEET_HEADERS.items():
        if table_name not in existing_sheets:
            print(f"  {table_name}: sheet not found, skipping")
            continue

        print(f"  Migrating {table_name}...", end=" ")
        ws = ss.worksheet(table_name)
        records = ws.get_all_records()

        if not records:
            print("0 rows")
            continue

        # Clear existing data in Postgres table
        execute(f'DELETE FROM "{table_name}"')

        cols = ", ".join(f'"{h}"' for h in headers)
        placeholders = ", ".join(["%s"] * len(headers))

        count = 0
        for record in records:
            values = [str(record.get(h, "") or "") for h in headers]
            if not values[0].strip():
                continue
            try:
                execute(f'INSERT INTO "{table_name}" ({cols}) VALUES ({placeholders})', values)
                count += 1
            except Exception as e:
                print(f"\n    Error on row {count + 1}: {e}")
                continue

        print(f"{count} rows")

    print("\nMigration complete!")


if __name__ == "__main__":
    migrate()
