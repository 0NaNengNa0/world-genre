"""One-time (idempotent) warehouse setup - applies sql/schema.sql.

Run from the backend/ directory, once, before the first run_load.py:
    python -m scripts.run_init_db

Safe to rerun any time - every statement in schema.sql is
CREATE TABLE/INDEX IF NOT EXISTS, so this never destroys existing data.
"""
import logging
from pathlib import Path

from app.core.db import get_connection

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "sql" / "schema.sql"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("run_init_db")


def main() -> None:
    sql = SCHEMA_PATH.read_text()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
    logger.info("Schema applied from %s", SCHEMA_PATH)


if __name__ == "__main__":
    main()
