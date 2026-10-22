"""A client manifest (a JSON list of records) is a case set: the intake of a new batch opens a reins batch on it."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from reins import names
from reins.store import ReinsError


class CaseJson(unittest.TestCase):
    def test_records_by_the_projects_key(self):
        f = Path(tempfile.mkdtemp()) / "NGM-PROD3-x-3.json"
        f.write_text(json.dumps([{"originating-authority-charge-identifier": "119782", "x": 1},
                                 {"originating-authority-charge-identifier": "119783"},
                                 {"originating-authority-charge-identifier": None}]))
        self.assertEqual(names.read_case_set(f, "originating-authority-charge-identifier"), ["119782", "119783"])
        with self.assertRaises(ReinsError):
            names.read_case_set(f, "oachargeid")
        self.assertIn("intake", names.BATCH_TYPES)


if __name__ == "__main__":
    unittest.main()
