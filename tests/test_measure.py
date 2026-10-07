"""C4 benchmarks + thresholds, env versions, ruleset versions, provenance, C6 review, C11 acceptance, preflight, C12."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from reins import accept, batches, bench, deliver, envs, gate, modules, preflight, review, rules
from reins.store import ReinsError, connect


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-measure-"))
        os.environ["REINS_HOME"] = str(self.tmp)
        os.environ["REINS_SESSION"] = "sess-A"
        (self.tmp / "config.toml").write_text(f'bench_root = "{self.tmp / "benchmarks"}"\n')
        self.con = connect(self.tmp)
        modules.add(self.con, "judge", "e2e", "judges")
        self.v = modules.new(self.con, "judge", "x", "t", self.tmp)
        self.cases = self.tmp / "cases.txt"
        self.ids = [str(100 + i) for i in range(20)]
        self.cases.write_text("\n".join(self.ids) + "\n")

    def open(self, specs=("ocr",), type_="experiment", **kw):
        return batches.open_(self.con, project="e2e", council="sheffield", wp="wp3", type_=type_, purpose="t",
                             case_file=self.cases, stages=[batches.parse_stage_spec(s) for s in specs], **kw)


class Bench(Base):
    def test_lifecycle(self):
        r = bench.init(self.con, "wp3-20", "e2e", self.cases, "twenty cases", holdout_fraction=0.25, seed=1)
        self.assertEqual((r["n_dev"], r["n_holdout"]), (15, 5))
        path = Path(r["path"])
        src = self.tmp / "stage_out"; src.mkdir(); (src / "a.json").write_text("{}")
        bench.add_stage(self.con, "wp3-20", "judge", src, self.v)
        with self.assertRaises(ReinsError):                      # same stage twice
            bench.add_stage(self.con, "wp3-20", "judge", src, self.v)
        labs = self.tmp / "labels.jsonl"
        # golden by a model, golden without the drawing, unknown case, bad source: all refused, nothing appended
        labs.write_text("\n".join(json.dumps(x) for x in [
            {"oachargeid": "100", "target": "polygon", "value": "correct", "source": "golden", "by": "assistant", "saw_drawing": True},
            {"oachargeid": "101", "target": "polygon", "value": "correct", "source": "golden", "by": "Jia", "saw_drawing": False},
            {"oachargeid": "999", "target": "polygon", "value": "correct", "source": "customer_result", "by": "HMLR"},
            {"oachargeid": "102", "target": "polygon", "value": "x", "source": "truth", "by": "Jia"}]) + "\n")
        with self.assertRaises(ReinsError) as e:
            bench.label(self.con, "wp3-20", labs)
        self.assertEqual(str(e.exception).count("line "), 4)
        self.assertEqual((path / "labels.jsonl").read_text(), "")
        labs.write_text(json.dumps({"oachargeid": "100", "target": "polygon", "value": "correct", "source": "golden",
                                    "by": "Jia", "saw_drawing": True}) + "\n"
                        + json.dumps({"oachargeid": "101", "target": "polygon", "value": "Fail", "source": "customer_result", "by": "HMLR"}) + "\n")
        r2 = bench.label(self.con, "wp3-20", labs)
        self.assertEqual((r2["labelset"], r2["added"]), (1, 2))
        with self.assertRaises(ReinsError):                      # verify needs frozen
            bench.verify(self.con, "wp3-20")
        with self.assertRaises(ReinsError):                      # gate on an open registered benchmark
            gate.record(self.con, self.v, benchmark="bench-wp3-20-v1", tiers="judges", stages_covered="judges",
                        missed_error=0, review_load=0, base_missed_error=0, base_review_load=0)
        bench.freeze(self.con, "wp3-20", "first")
        self.assertTrue(bench.verify(self.con, "wp3-20")["ok"])
        self.assertFalse(os.access(path / "labels.jsonl", os.W_OK))
        with self.assertRaises(ReinsError):                      # frozen: no more labels
            bench.label(self.con, "wp3-20", labs)
        gate.record(self.con, self.v, benchmark="bench-wp3-20-v1", tiers="judges", stages_covered="judges",
                    missed_error=0, review_load=0, base_missed_error=0, base_review_load=0)
        os.chmod(path / "labels.jsonl", 0o644)
        with (path / "labels.jsonl").open("a") as f:
            f.write("tampered\n")
        self.assertFalse(bench.verify(self.con, "wp3-20")["ok"])
        r3 = bench.bump(self.con, "wp3-20", "add reviewer labels")
        self.assertEqual(r3["version"], 2)
        self.assertEqual(bench.label(self.con, "wp3-20", labs)["labelset"], 2)   # v2 is open, labelset continues
        self.assertEqual(len(bench.cases(self.con, "wp3-20", split="holdout")), 5)
        bench.holdout(self.con, "wp3-20", "looked at the 5 failing ones", tuned=True)
        bench.freeze(self.con, "wp3-20", "v2")
        g = gate.record(self.con, self.v, benchmark="bench-wp3-20-v2", tiers="judges", stages_covered="judges",
                        missed_error=0, review_load=0, base_missed_error=0, base_review_load=0)
        self.assertIn("holdout compromised", self.con.execute("SELECT skipped FROM gate_log ORDER BY id DESC LIMIT 1").fetchone()[0])

    def test_thresholds_carry_n(self):
        r = bench.set_threshold(self.con, "georef", "min_score", "15", n=4, split="dev", benchmark="bench-x-v1", by="user")
        self.assertTrue(r["low_n"])
        with self.assertRaises(ReinsError):
            bench.set_threshold(self.con, "georef", "min_score", "15", n=400, split="holdout", benchmark=None, by="user")
        bench.set_threshold(self.con, "georef", "min_score", "12", n=359, split="dev", benchmark="bench-x-v2", by="user")
        t = bench.thresholds(self.con, "georef")
        self.assertEqual((len(t), t[0]["value"], t[0]["low_n"]), (1, "12", 0))


class Envs(Base):
    def test_snapshot_dedupes_and_checks(self):
        a = envs.snapshot(self.con, "test", sys.executable)
        b = envs.snapshot(self.con, "test", sys.executable)
        self.assertEqual((a["name"], a["new"], b["new"]), ("env-test-v1", True, False))
        asset = self.tmp / "w.pt"; asset.write_bytes(b"weights")
        c = envs.snapshot(self.con, "test", sys.executable, [str(asset)])
        self.assertEqual(c["name"], "env-test-v2")
        asset.write_bytes(b"other weights")
        chk = envs.check(self.con, "test", sys.executable, [str(asset)])
        self.assertFalse(chk["same"]); self.assertIn("assets", chk["diff"])


class Rulesets(Base):
    def test_freeze_requires_diff_from_v2(self):
        rp = self.tmp / "rules.toml"
        rp.write_text('[[rule]]\nid="a"\nlane="review"\nwhen="w"\nevidence="e"\ndecided="2026-10-05"\nby="user"\n')
        r1 = rules.freeze(self.con, rp, "e2e", None, None)
        self.assertEqual((r1["name"], rules.freeze(self.con, rp, "e2e", None, None)["new"]), ("ruleset-e2e-v1", False))
        rp.write_text(rp.read_text() + '[[rule]]\nid="b"\nlane="review"\nwhen="w2"\nevidence="e"\ndecided="2026-10-06"\nby="user"\n')
        with self.assertRaises(ReinsError):
            rules.freeze(self.con, rp, "e2e", None, None)
        old = self.tmp / "old.csv"; new = self.tmp / "new.csv"
        old.write_text("oachargeid,lane\n100,auto_accept\n101,review\n"); new.write_text("oachargeid,lane\n100,review\n101,review\n")
        r2 = rules.freeze(self.con, rp, "e2e", old, new)
        self.assertEqual((r2["name"], r2["diff_summary"]), ("ruleset-e2e-v2", "auto_accept->review:1"))
        with self.assertRaises(ReinsError):
            self.open(ruleset="ruleset-e2e-v9")
        b = self.open(ruleset="ruleset-e2e-v2", config_files=[rp], input_files=[old])
        p = batches.provenance(self.con, b)
        self.assertEqual(p["ruleset"], "ruleset-e2e-v2")
        self.assertTrue(p["config_sha"] and str(old) in p["input_shas"])


class Review(Base):
    def setUp(self):
        super().setUp()
        self.b = self.open()
        self.tax = {"version": 2, "error_types": ["location", "polygon_extent"], "unsure_needs_note": True}

    def test_required_fields(self):
        bad = [{"oachargeid": "100", "reviewer": "Maggie", "action": "verdict", "verdict": "wrong"},
               {"oachargeid": "100", "reviewer": "Maggie", "action": "verdict", "verdict": "wrong", "error_type": "colour"},
               {"oachargeid": "100", "reviewer": "Maggie", "action": "verdict", "verdict": "unsure"},
               {"oachargeid": "100", "reviewer": "", "action": "verdict", "verdict": "correct"},
               {"oachargeid": "999", "reviewer": "Maggie", "action": "view"}]
        with self.assertRaises(ReinsError) as e:
            review.record(self.con, self.b, bad, self.tax)
        self.assertEqual(str(e.exception).count("event "), 5)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM review_event").fetchone()[0], 0)
        n = review.record(self.con, self.b, [
            {"oachargeid": "100", "reviewer": "Maggie", "action": "verdict", "verdict": "wrong", "error_type": "location"},
            {"oachargeid": "100", "reviewer": "Yishan", "action": "verdict", "verdict": "correct"},
            {"oachargeid": "101", "reviewer": "Maggie", "action": "verdict", "verdict": "unsure", "note": "need the amended plan"},
            {"oachargeid": "101", "reviewer": "Yishan", "action": "verdict", "verdict": "correct"}], self.tax)
        self.assertEqual(n, 4)
        cal = review.calibration(self.con, self.b)
        pair = cal["pairs"][0]
        self.assertEqual((pair["a"], pair["b"], pair["both"], pair["disagree"]), ("Maggie", "Yishan", 2, 2))
        cands = review.candidates(self.con, self.b)
        self.assertEqual(len(cands), 4)
        self.assertTrue(all(c["source"] == "reviewer_verdict" for c in cands))

    def test_assign_appends_without_reordering(self):
        review.assign(self.con, self.b, "Maggie", ["100", "101"], "Q1", "r1", "user")
        review.assign(self.con, self.b, "Maggie", ["102"], "Q1", "r2", "user")
        ords = [r[0] for r in self.con.execute("SELECT ord FROM review_assignment WHERE reviewer='Maggie' ORDER BY id")]
        self.assertEqual(ords, [1, 2, 3])
        with self.assertRaises(ReinsError):
            review.assign(self.con, self.b, "Maggie", ["999"], None, None, "user")


class Accept(Base):
    def setUp(self):
        super().setUp()
        self.b = self.open()
        self.lanes = self.tmp / "lanes.csv"
        self.lanes.write_text("oachargeid,lane\n" + "\n".join(f"{i},{'auto_accept' if int(i) % 4 else 'review'}" for i in self.ids) + "\n")

    def test_sample_grade_decide(self):
        r = accept.sample(self.con, self.b, self.lanes, n=6, seed=3, ideal=1, max_=2)
        r_again = accept.sample(self.con, self.b, self.lanes, n=6, seed=3, ideal=1, max_=2)
        s1 = json.loads(self.con.execute("SELECT sample FROM acceptance WHERE id=?", (r["acceptance_id"],)).fetchone()[0])
        s2 = json.loads(self.con.execute("SELECT sample FROM acceptance WHERE id=?", (r_again["acceptance_id"],)).fetchone()[0])
        self.assertEqual(s1, s2)                                  # fixed seed -> same sample
        picked = [s["oachargeid"] for s in s2]
        with self.assertRaises(ReinsError):                      # case outside the sample
            accept.grade(self.con, self.b, [("999", "P", "Jia", None)])
        with self.assertRaises(ReinsError):                      # not all graded
            accept.grade(self.con, self.b, [(picked[0], "P", "Jia", None)]); accept.decide(self.con, self.b, "Jia")
        accept.grade(self.con, self.b, [(c, "F" if i < 2 else "P", "Jia", None) for i, c in enumerate(picked)])
        # lane of one sampled case changed since the sample: stale, decide refused with lanes
        new_lanes = self.tmp / "lanes2.csv"
        new_lanes.write_text(self.lanes.read_text().replace(f"{picked[0]},auto_accept", f"{picked[0]},review"))
        self.assertEqual(accept.check(self.con, self.b, new_lanes)["stale"], [picked[0]])
        with self.assertRaises(ReinsError):
            accept.decide(self.con, self.b, "Jia", lanes_file=new_lanes)
        d = accept.decide(self.con, self.b, "Jia", lanes_file=self.lanes)
        self.assertEqual((d["decision"], d["F"]), ("ship_with_note", 2))
        with self.assertRaises(ReinsError):
            accept.decide(self.con, self.b, "Jia")

    def test_lanes_must_be_the_batch(self):
        bad = self.tmp / "bad.csv"; bad.write_text("oachargeid,lane\n999,auto_accept\n")
        with self.assertRaises(ReinsError):
            accept.sample(self.con, self.b, bad)


class Preflight(Base):
    def test_strict_batch_needs_preflight(self):
        modules.release(self.con, self.v, "g")
        self.con.execute("INSERT INTO release VALUES ('rel-e2e-20261007-1','e2e','abc',?, '', '[]', '2026', NULL)", (str(self.tmp),))
        b = self.open(specs=[f"judge={self.v}:paid"], type_="production", release_name="rel-e2e-20261007-1", spend_cap=5)
        with self.assertRaises(ReinsError) as e:
            batches.stage_start(self.con, b, "judge")
        self.assertIn("preflight", str(e.exception))
        r = preflight.run(self.con, b)
        names_ = {c["name"]: c["ok"] for c in r["checks"]}
        self.assertFalse(r["ok"])                                 # gateway down, env/ruleset missing
        self.assertFalse(names_["gateway_up"]); self.assertFalse(names_["env_recorded"]); self.assertTrue(names_["release_tree"])
        root = self.tmp / "proj"; root.mkdir()
        (root / "reins.toml").write_text('project="e2e"\n[[preflight]]\nname="custom"\ncmd="test -n \\"$REINS_BATCH\\" && echo fine"\n')
        r2 = preflight.run(self.con, b, root)
        self.assertEqual([c for c in r2["checks"] if c["name"] == "custom"][0]["detail"], "fine")


class Deliver(Base):
    def test_contract_check_and_register(self):
        b = self.open(work_dir=str(self.tmp / "wd"))
        import openpyxl
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Validation Summary"
        ws.append(["oachargeid", "lane", "note", "const"])
        for i in self.ids[:-1]:                                   # one case missing
            ws.append([i, "auto_accept", "ok", "same"])
        ws.append([self.ids[0], "auto_accept", "dup", "same"])    # duplicate
        ws.append(["999", "review", "外部", "same"])               # outsider + CJK
        f = self.tmp / "d.xlsx"; wb.save(f)
        r = deliver.check(self.con, f, b, sheets_expected=["Validation Summary", "Polygon Validation"])
        text = "\n".join(r["problems"])
        for needle in ("sheets are", "CJK", "repeated", "not in", "absent", "'const' is constant"):
            self.assertIn(needle, text)
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = "S"
        ws.append(["oachargeid", "lane"])
        for k, i in enumerate(self.ids):
            ws.append([i, "auto_accept" if k % 2 else "review"])
        wb.save(f)
        r = deliver.register(self.con, f, b)
        self.assertEqual(r["version"], 1)
        p = Path(r["path"]); self.assertTrue(p.exists() and not os.access(p, os.W_OK))
        man = json.loads(p.with_suffix(".xlsx.manifest.json").read_text())
        self.assertEqual((man["n_cases"], man["data_digest"]), (20, r["data_digest"]))
        r2 = deliver.register(self.con, f, b)                     # again: v2, nothing overwritten
        self.assertEqual(r2["version"], 2)
        wb.save(f)                                                # same rows, new zip bytes: same data digest
        self.assertEqual(deliver.check(self.con, f, b)["data_digest"], r["data_digest"])


if __name__ == "__main__":
    unittest.main()
