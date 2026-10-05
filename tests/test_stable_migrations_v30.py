import sqlite3
import tempfile
import unittest
from pathlib import Path

import hadron_storage
from hadron_storage import migrate_database
from hadron_version import SCHEMA_VERSION


MIGRATIONS = [
    hadron_storage._create_schema_v1,
    hadron_storage._migrate_v1_to_v2,
    hadron_storage._migrate_v2_to_v3,
    hadron_storage._migrate_v3_to_v4,
    hadron_storage._migrate_v4_to_v5,
    hadron_storage._migrate_v5_to_v6,
    hadron_storage._migrate_v6_to_v7,
    hadron_storage._migrate_v7_to_v8,
    hadron_storage._migrate_v8_to_v9,
    hadron_storage._migrate_v9_to_v10,
    hadron_storage._migrate_v10_to_v11,
    hadron_storage._migrate_v11_to_v12,
]


def make_historical_database(path: Path, version: int):
    conn = sqlite3.connect(path)
    try:
        for index in range(version):
            MIGRATIONS[index](conn)
            conn.execute(f"PRAGMA user_version = {index + 1}")
        conn.commit()
    finally:
        conn.close()


class StableMigrationTests(unittest.TestCase):
    def test_selected_historical_schemas_migrate_to_stable(self):
        for historical in (4, 6, 9, 11):
            with self.subTest(historical=historical):
                with tempfile.TemporaryDirectory() as tmp:
                    db = Path(tmp) / "hadron.db"
                    make_historical_database(db, historical)
                    result = migrate_database(db)
                    self.assertEqual(
                        result["new_version"],
                        SCHEMA_VERSION,
                    )
                    conn = sqlite3.connect(db)
                    try:
                        self.assertEqual(
                            conn.execute(
                                "PRAGMA user_version"
                            ).fetchone()[0],
                            12,
                        )
                        self.assertEqual(
                            conn.execute(
                                "PRAGMA integrity_check"
                            ).fetchone()[0],
                            "ok",
                        )
                    finally:
                        conn.close()


if __name__ == "__main__":
    unittest.main()
