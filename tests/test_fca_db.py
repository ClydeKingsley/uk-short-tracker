from pathlib import Path
import tempfile
import unittest

from short_tracker.db import Database


class DatabaseTests(unittest.TestCase):
    def test_migrations_are_repeatable_and_transactions_rollback(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tracker.sqlite3"
            first = Database(path)
            second = Database(path)
            self.assertEqual(1, first.schema_version)
            self.assertEqual(1, second.schema_version)

            with self.assertRaises(RuntimeError):
                with first.transaction() as connection:
                    connection.execute(
                        """
                        INSERT INTO issuers(
                            canonical_name, normalized_name, canonical_priority,
                            created_at, updated_at
                        ) VALUES ('Rollback plc', 'rollback plc', 1, 'now', 'now')
                        """
                    )
                    raise RuntimeError("force rollback")
            count = first.query_one("SELECT COUNT(*) AS n FROM issuers")["n"]
            self.assertEqual(0, count)

    def test_memory_database_is_rejected_because_helpers_use_separate_connections(self):
        with self.assertRaisesRegex(ValueError, "temporary file"):
            Database(":memory:")


if __name__ == "__main__":
    unittest.main()
