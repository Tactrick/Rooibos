import tempfile
import unittest
from pathlib import Path

from tools.audit_chart_engine_access import audit_tree


class ChartEngineAccessAuditTests(unittest.TestCase):
    def test_clean_runtime_source_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "engine.py").write_text(
                "def analyse(snapshot):\n    return len(snapshot.candles)\n", encoding="utf-8")
            self.assertEqual(audit_tree(root), [])

    def test_sqlite_import_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "engine.py").write_text(
                "import sqlite3\nconn = sqlite3.connect('moby_market.db')\n", encoding="utf-8")
            findings = audit_tree(root)
            self.assertTrue(any("forbidden direct-access import sqlite3" in x for x in findings))
            self.assertTrue(any("possible direct database connection" in x for x in findings))

    def test_protected_table_query_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "engine.py").write_text(
                "cursor.execute('SELECT * FROM a6_candles')\n", encoding="utf-8")
            findings = audit_tree(root)
            self.assertTrue(any("protected collector/governance table" in x for x in findings))


if __name__ == "__main__":
    unittest.main()
