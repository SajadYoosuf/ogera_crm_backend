"""Run the real API on a throwaway SQLite database, for the mobile end-to-end test.

    python tests/e2e_server.py --port 8765

The database is a fresh file on every start, seeded with the same employees and
lead sources as the pytest suite (see seed_data.py). Not collected by pytest.
"""
import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    db_file = Path(tempfile.gettempdir()) / "ogera_mobile_e2e.sqlite"
    db_file.unlink(missing_ok=True)
    os.environ["DATABASE_URL"] = f"sqlite:///{db_file.as_posix()}"
    os.environ["SQL_ECHO"] = "0"

    from sqlalchemy import BigInteger
    from sqlalchemy.ext.compiler import compiles

    @compiles(BigInteger, "sqlite")
    def _bigint_as_integer(type_, compiler, **kw):  # SQLite only auto-increments INTEGER
        return "INTEGER"

    import uvicorn

    import main  # creates the tables
    from database import SessionLocal
    from seed_data import seed

    with SessionLocal() as db:
        seed(db)
    uvicorn.run(main.app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
