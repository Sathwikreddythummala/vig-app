import psycopg
from psycopg.rows import dict_row
from config import settings

_conn = None


def get_conn():
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg.connect(settings.DATABASE_URL, row_factory=dict_row)
    return _conn


def execute(sql, params=None, fetch=False):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if fetch:
                return cur.fetchall()
            conn.commit()
            return None
    except Exception:
        conn.rollback()
        raise


def initialize_db(sheet_headers: dict):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Get existing columns for all tables in one query
            cur.execute("""
                SELECT table_name, column_name FROM information_schema.columns
                WHERE table_schema = 'public'
            """)
            existing = {}
            for row in cur.fetchall():
                existing.setdefault(row["table_name"], set()).add(row["column_name"])

            for table, cols in sheet_headers.items():
                col_defs = ", ".join(f'"{c}" TEXT' for c in cols)
                pk = cols[0]
                cur.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({col_defs}, PRIMARY KEY ("{pk}"))')
                # Only ALTER for columns not yet in DB
                table_cols = existing.get(table, set())
                for col in cols[1:]:
                    if col not in table_cols:
                        cur.execute(f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS "{col}" TEXT')
        conn.commit()
        print("PostgreSQL tables initialized")
    except Exception as e:
        conn.rollback()
        print(f"DB init error: {e}")
        raise
