"""The rules of C0-C3, each tested against the incident that motivated it (docs/DESIGN.md)."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from reins import batches, glossary, modules, names
from reins.store import ReinsError, connect


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-test-"))
        self.con = connect(self.tmp)
        modules.add(self.con, "georef", "e2e-plan-extract", "place plan images on the map")

    def cases(self, ids, name="cases.txt"):
        p = self.tmp / name
        p.write_text("\n".join(ids) + "\n")
        return p


class Names(unittest.TestCase):
    def test_module_version_format(self):
        self.assertEqual(names.module_version("georef", "roadnames", "20261002", 1), "georef-roadnames-20261002-1")
        for bad in ("georef-roadnames-20261002", "georef-roadnames-20261002-0", "georef-road-names-20261002-1",
                    "Georef-x-20261002-1", "georef-x-20261302-1"):
            with self.assertRaises(ReinsError, msg=bad):
                names.parse_module_version(bad)

    def test_batch_id_format(self):
        self.assertEqual(names.batch_id("sheffield", "wp3", "eval", "20261006", 2), "sheffield-wp3-eval-20261006-2")
        with self.assertRaises(ReinsError):
            names.batch_id("sheffield", "wp3", "adhoc", "20261006", 1)

    def test_case_set_problems(self):
        # a downloader once read the header line as a case; SHF_ ids are display, not the key
        probs = names.case_problems(["oachargeid", "101", "101", " 102", "SHF_103", ""])
        text = "\n".join(probs)
        for needle in ("header word", "appears 2 times", "whitespace", "pattern", "empty"):
            self.assertIn(needle, text)
        self.assertEqual(names.case_problems(["101", "102"]), [])


class Modules(Base):
    def test_same_day_numbering(self):
        a = modules.new(self.con, "georef", "roadnames", "first", self.tmp, day="20261002")
        b = modules.new(self.con, "georef", "roadfit", "second", self.tmp, day="20261002")
        c = modules.new(self.con, "georef", "roadfit", "next day", self.tmp, day="20261003")
        self.assertEqual((a, b, c), ("georef-roadnames-20261002-1", "georef-roadfit-20261002-2",
                                     "georef-roadfit-20261003-1"))

    def test_release_needs_gate_and_candidate(self):
        v = modules.new(self.con, "georef", "x", "t", self.tmp)
        with self.assertRaises(ReinsError):
            modules.release(self.con, v, "  ")
        modules.release(self.con, v, "judges tier green on bench-sheffield-wp3-359-v6")
        with self.assertRaises(ReinsError):
            modules.release(self.con, v, "again")
        self.assertEqual(modules.status(self.con, "georef")["production"]["version"], v)

    def test_unregistered_module(self):
        with self.assertRaises(ReinsError):
            modules.new(self.con, "judge", "x", "t", self.tmp)


class Batches(Base):
    def open(self, ids=("101", "102", "103"), type_="experiment", stages=(("ocr", None), ("georef", None)), **kw):
        return batches.open_(self.con, project="e2e-plan-extract", council="sheffield", wp="wp3", type_=type_,
                             purpose="test", case_file=self.cases(list(ids)), stages=list(stages), **kw)

    def test_numbering_and_bad_case_set(self):
        self.assertTrue(self.open().endswith("-1"))
        self.assertTrue(self.open().endswith("-2"))
        with self.assertRaises(ReinsError):
            self.open(ids=("101", "101"))

    def test_production_runs_released_versions_only(self):
        v = modules.new(self.con, "georef", "x", "t", self.tmp)
        with self.assertRaises(ReinsError):
            self.open(type_="production", stages=[("georef", v)])
        with self.assertRaises(ReinsError):                     # no version at all
            self.open(type_="production", stages=[("georef", None)])
        modules.release(self.con, v, "gate green")
        self.open(type_="production", stages=[("georef", v)])

    def test_conservation_blocks_stage_end(self):
        # incident: 104 cases taken out of rework were never merged back; nobody noticed
        b = self.open()
        batches.stage_start(self.con, b, "ocr")
        batches.mark(self.con, b, "ocr", [("101", "done", None), ("102", "skipped", "no scans")])
        with self.assertRaises(ReinsError) as e:
            batches.stage_end(self.con, b, "ocr")
        self.assertIn("103", str(e.exception))
        batches.mark(self.con, b, "ocr", [("103", "failed", "timeout")])
        led = batches.stage_end(self.con, b, "ocr")
        self.assertEqual((led["done"], led["skipped"], led["failed"]), (1, 1, 1))

    def test_mark_rejects_outsiders_duplicates_and_reasonless_failures(self):
        # incident: 689 polygons that never entered the pipeline still got lanes
        b = self.open()
        batches.stage_start(self.con, b, "ocr")
        for rows in ([("999", "done", None)], [("101", "done", None), ("101", "done", None)],
                     [("101", "failed", None)], [("101", "ok", None)]):
            with self.assertRaises(ReinsError, msg=rows):
                batches.mark(self.con, b, "ocr", rows)
        self.assertEqual(batches.ledger(self.con, b, "ocr")["missing"], ["101", "102", "103"])

    def test_retry_latest_outcome_wins_history_kept(self):
        b = self.open()
        batches.stage_start(self.con, b, "ocr")
        batches.mark(self.con, b, "ocr", [("101", "failed", "gpu watchdog")])
        batches.mark(self.con, b, "ocr", [("101", "done", None)])
        led = batches.ledger(self.con, b, "ocr")
        self.assertEqual((led["done"], led["failed"]), (1, 0))
        self.assertEqual([e["status"] for e in batches.case_history(self.con, "101")], ["failed", "done"])

    def test_mixed_version_flagged(self):
        # incident: batch2 started on one commit, switched release at step 8 and again at step 10
        v1 = modules.new(self.con, "georef", "a", "t", self.tmp)
        v2 = modules.new(self.con, "georef", "b", "t", self.tmp)
        b = self.open(stages=[("georef", v1)])
        warns = batches.stage_start(self.con, b, "georef", module_version=v2)
        self.assertTrue(warns and "MIXED" in warns[0])
        self.assertEqual(batches.status(self.con, b)["batch"]["mixed_version"], 1)

    def test_close_requires_every_stage_and_freezes(self):
        b = self.open()
        with self.assertRaises(ReinsError):
            batches.close(self.con, b)
        batches.stage_start(self.con, b, "ocr")
        batches.mark(self.con, b, "ocr", [(c, "done", None) for c in ("101", "102", "103")])
        batches.stage_end(self.con, b, "ocr")
        batches.skip_stage(self.con, b, "georef", "experiment covers OCR only")
        batches.close(self.con, b)
        with self.assertRaises(ReinsError):
            batches.mark(self.con, b, "ocr", [("101", "done", None)])

    def test_mark_requires_running_stage(self):
        b = self.open()
        with self.assertRaises(ReinsError):
            batches.mark(self.con, b, "ocr", [("101", "done", None)])


class Glossary(unittest.TestCase):
    def setUp(self):
        self.terms = glossary.load()

    def hits(self, ident):
        return [t["term"] for _, t in glossary.check_identifier(ident, self.terms)]

    def test_forbidden(self):
        self.assertEqual(self.hits("qa_final"), ["check"])
        self.assertEqual(self.hits("confidence"), ["confidence"])
        self.assertEqual(self.hits("high"), ["confidence"])
        self.assertEqual(self.hits("ground_truth"), ["golden"])
        self.assertEqual(self.hits("Plan"), ["plan_image"])

    def test_allowed(self):
        for ok in ("trace_confidence", "oachargeid", "plan_image", "plan_type", "customer_result", "lane",
                   "module_version", "spend_cap"):
            self.assertEqual(self.hits(ok), [], ok)

    def test_markdown_ignores_code(self):
        d = Path(tempfile.mkdtemp())
        (d / "a.md").write_text("The `qa_final.csv` file\n```\nqa\n```\nthe QA layer\n")
        hits = glossary.lint([d / "a.md"], self.terms)
        self.assertEqual([h.where.split(":")[-1] for h in hits], ["5"])

    def test_csv_headers(self):
        d = Path(tempfile.mkdtemp())
        (d / "t.csv").write_text("oachargeid,confidence,lane\n1,high,review\n")
        self.assertEqual([h.word for h in glossary.lint([d / "t.csv"], self.terms)], ["confidence"])


class Store(unittest.TestCase):
    def test_refuses_code_folder(self):
        old = os.environ.get("REINS_HOME")
        os.environ["REINS_HOME"] = "/env/code/ReinsLite/data"
        try:
            with self.assertRaises(ReinsError):
                connect()
        finally:
            if old is None:
                del os.environ["REINS_HOME"]
            else:
                os.environ["REINS_HOME"] = old


if __name__ == "__main__":
    unittest.main()
