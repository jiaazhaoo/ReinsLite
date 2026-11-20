"""A case key of several columns (user 2026-11-20: case 名单必须按照三个字段): a case set with the key columns is
read, refused when a column is missing or a key repeats, stored per case, used for the batch's marks and shown."""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

from reins import batches
from reins.store import ReinsError
from reins.store import connect

KEY = ["council", "oachargeid", "further_information_reference"]
ST = [{"stage": "s", "module_version": None, "paid": 0, "spend_cap": None, "time_limit_s": None}]


class CaseKeyFields(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("REINS_HOME", "REINS_SESSION", "REINS_CODE_ROOT")}
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-key-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_SESSION"] = "owner"
        os.environ["REINS_CODE_ROOT"] = str(self.tmp / "code")
        (self.tmp / "code").mkdir()
        self.con = connect(self.tmp / "home")

    def tearDown(self):
        self.con.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def case_set(self, rows, name="cases.tsv"):
        f = self.tmp / name
        f.write_text("\t".join(KEY + ["wp_hmlr"]) + "\n" + "".join("\t".join(r) + "\n" for r in rows))
        return f

    def open(self, f, **kw):
        return batches.open_(self.con, project="p", scope="braintree-wp5", type_="experiment", purpose="key test",
                             case_file=f, stages=ST, case_key="oachargeid", key_fields=KEY, **kw)

    def test_the_three_fields_are_stored_per_case(self):
        f = self.case_set([("braintree", "152853", "10/00313/FUL", "wp5"), ("braintree", "1077994", "TPO 6/88", "wp5"),
                           ("braintree", "99", "", "wp5")])                       # a blank reference is still part of the key
        b = self.open(f)
        self.assertEqual(batches.case_key_of(self.con, b, "1077994"),
                         {"council": "braintree", "oachargeid": "1077994", "further_information_reference": "TPO 6/88"})
        self.assertEqual(batches.case_key_of(self.con, b, "99")["further_information_reference"], "")
        self.assertEqual(batches.batch_case_column(self.con, b), "oachargeid")
        self.assertEqual(json.loads(self.con.execute("SELECT case_key_fields FROM batch WHERE batch_id=?", (b,)).fetchone()[0])["fields"], KEY)

    def test_a_missing_column_is_refused(self):
        f = self.tmp / "two.tsv"
        f.write_text("council\toachargeid\nbraintree\t1\n")
        with self.assertRaises(ReinsError) as e:
            self.open(f)
        self.assertIn("further_information_reference", str(e.exception))

    def test_a_repeated_key_is_refused(self):
        f = self.case_set([("braintree", "1", "A/1", "wp5"), ("braintree", "1", "A/1", "wp5")])
        with self.assertRaises(ReinsError):
            self.open(f)

    def test_one_id_with_two_references_is_refused_with_a_reason(self):
        f = self.case_set([("braintree", "1", "A/1", "wp5"), ("braintree", "1", "A/2", "wp5")])
        with self.assertRaises(ReinsError) as e:
            self.open(f)
        self.assertIn("separate batches", str(e.exception))

    def test_marks_by_the_batchs_own_case_column(self):
        b = self.open(self.case_set([("braintree", "1", "A/1", "wp5"), ("braintree", "2", "A/2", "wp5")]))
        m = self.tmp / "marks.tsv"
        m.write_text("council\toachargeid\tfurther_information_reference\tstatus\treason\n"
                     "braintree\t1\tA/1\tdone\t\nbraintree\t2\tA/2\tskipped\tno scans\n")
        rows = batches.read_marks(m, batches.batch_case_column(self.con, b))
        self.assertEqual(rows, [("1", "done", None), ("2", "skipped", "no scans")])

    def test_without_key_fields_nothing_changes(self):
        f = self.tmp / "ids.txt"
        f.write_text("101\n102\n")
        b = batches.open_(self.con, project="p", scope="x-wp1", type_="experiment", purpose="old way", case_file=f, stages=ST)
        self.assertEqual(batches.case_key_of(self.con, b, "101"), {})
        self.assertIsNone(batches.batch_case_column(self.con, b))

    def test_adopt_stores_the_key_too(self):
        f = self.case_set([("braintree", "7", "A/7", "wp5")])
        b = batches.adopt(self.con, project="p", scope="braintree-wp5", type_="experiment", purpose="adopted", case_file=f,
                          work_dir=str(self.tmp / "w"), stages=[("s", "planned")], release_name=None, owner="owner",
                          case_key="oachargeid", key_fields=KEY)
        self.assertEqual(batches.case_key_of(self.con, b, "7")["further_information_reference"], "A/7")


class RunControlAdapter(unittest.TestCase):
    """The e2e run-control adapter passes the key when the case set carries the three columns."""

    def test_key_args(self):
        spec = importlib.util.spec_from_file_location("runctl", Path(__file__).resolve().parents[1] / "adapters/e2e/runctl.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        d = Path(tempfile.mkdtemp())
        (d / "k.tsv").write_text("council\toachargeid\tfurther_information_reference\nx\t1\tA\n")
        (d / "ids.txt").write_text("1\n")
        self.assertEqual(m.key_args(["--cases", str(d / "k.tsv")]),
                         ["--key-fields", "council,oachargeid,further_information_reference", "--case-column", "oachargeid"])
        self.assertEqual(m.key_args(["--cases", str(d / "ids.txt")]), [])


if __name__ == "__main__":
    unittest.main()
