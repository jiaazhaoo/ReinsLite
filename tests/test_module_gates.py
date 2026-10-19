"""Module gates (2026-10-19): a change is scored on the changed module's own benchmark with its own metrics; the
downstream re-runs only the cases whose outputs changed (reins bench changed); benchmarks grow (reins bench add-cases).
Incident: a plan-crop change was to be judged by a 1.5 h end-to-end re-run of 359 cases, scored on final lanes only."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from reins import bench, dev, gate, modules
from reins.store import ReinsError, connect

REPO_ROOT = Path(__file__).resolve().parents[1]

GATE = ("import json, os\n"
        "v = int(open('whole').read())\n"
        "json.dump({'benchmark': 'bench-crops-v1', 'tiers': 'module,downstream', 'stages_covered': 'crops',"
        " 'metrics': {'plans_whole': {'value': v, 'base': 36, 'better': 'higher', 'hard': True},"
        "             'boxes_per_page': {'value': 9.9, 'base': 1.3, 'better': 'lower', 'hard': False}},"
        " 'golden_regressions': 0, 'cases_rerun': 3, 'cases_reused': 40,"
        " 'module_seen': os.environ.get('REINS_GATE_MODULE')}, open(os.environ['REINS_GATE_OUT'], 'w'))\n")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-mg-"))
        os.environ["REINS_HOME"] = str(self.tmp)
        os.environ["REINS_SESSION"] = "sess-A"
        os.environ["PYTHONPATH"] = str(REPO_ROOT)
        (self.tmp / "config.toml").write_text(f'bench_root = "{self.tmp / "benchmarks"}"\ngateway_port = 9\n')
        self.con = connect(self.tmp)

    def tearDown(self):
        for d, dirs, files in os.walk(self.tmp):
            os.chmod(d, 0o755)
        shutil.rmtree(self.tmp, ignore_errors=True)


class Metrics(Base):
    def setUp(self):
        super().setUp()
        modules.add(self.con, "crops", "proj", "plans of land boxed and cropped from drawing pages")
        self.v = modules.new(self.con, "crops", "x", "a test version that does x", self.tmp)

    def test_hard_metric_worse_is_red_soft_is_reported(self):
        g = gate.record(self.con, self.v, benchmark="bench-crops-v1", tiers="module", stages_covered="crops",
                        metrics={"plans_whole": {"value": 45, "base": 36, "better": "higher", "hard": True},
                                 "boxes_per_page": {"value": 2.5, "base": 1.3, "better": "lower", "hard": False}})
        self.assertEqual(g["status"], "green")                     # more boxes is a cost, not a regression
        g = gate.record(self.con, self.v, benchmark="bench-crops-v1", tiers="module", stages_covered="crops",
                        metrics={"plans_cut": {"value": 1, "base": 0, "better": "lower", "hard": True}})
        self.assertEqual((g["status"], g["hard_worse"]), ("red", ["plans_cut"]))
        row = self.con.execute("SELECT metrics, module FROM gate_log ORDER BY id DESC LIMIT 1").fetchone()
        self.assertIn("plans_cut", json.loads(row["metrics"]))

    def test_end_to_end_gate_unchanged(self):
        g = gate.record(self.con, self.v, benchmark="bench-t-v1", tiers="judges", stages_covered="judges", missed_error=1,
                        review_load=50, base_missed_error=0, base_review_load=96)
        self.assertEqual(g["status"], "red")

    def test_bad_direction_refused(self):
        with self.assertRaises(ReinsError):
            gate.record(self.con, self.v, benchmark="bench-crops-v1", tiers="module", stages_covered="crops",
                        metrics={"m": {"value": 1, "base": 2, "better": "bigger", "hard": True}})


class Finish(Base):
    def setUp(self):
        super().setUp()
        modules.add(self.con, "crops", "proj", "plans of land boxed and cropped from drawing pages")
        modules.add(self.con, "judge", "proj", "model judges score a delivered polygon")
        self.repo = self.tmp / "proj"
        self.repo.mkdir()
        run = lambda *a: subprocess.run(["git", "-C", str(self.repo), *a], check=True, capture_output=True)
        run("init", "-q", "-b", "main"); run("config", "user.email", "t@t"); run("config", "user.name", "t")
        (self.repo / "reins.toml").write_text(
            'project = "proj"\n[dev]\ngate = "false"\n'                       # the end-to-end gate would fail
            '[dev.upstream]\nfiles = ["crops/*"]\ngate = "false"\n'
            '[modules.crops]\nabout = "crops"\nfiles = ["crops/*"]\n'
            '[modules.crops.gate]\ncmd = "python3 gate_crops.py"\nbenchmark = "bench-crops"\n'
            '[modules.judge]\nabout = "judges"\nfiles = ["judge/*"]\n')
        (self.repo / "gate_crops.py").write_text(GATE)
        (self.repo / "whole").write_text("45")
        for d in ("crops", "judge"):
            (self.repo / d).mkdir(); (self.repo / d / "a.py").write_text("x = 1\n")
        run("add", "-A"); run("commit", "-q", "-m", "init")

    def change(self, wt, rel, text, msg="change"):
        (wt / rel).write_text(text)
        subprocess.run(["git", "-C", str(wt), "commit", "-qam", msg], check=True)

    def test_module_gate_replaces_the_end_to_end_one(self):
        r = dev.start(self.con, self.repo, "crops", "yolo", "plan boxes from a detector")
        wt = Path(r["worktree"])
        self.change(wt, "crops/a.py", "x = 2\n")
        out = dev.finish(self.con, r["version"])                  # [dev].gate and the upstream gate are `false`
        self.assertIn("crops gate green on bench-crops-v1", out["evidence"])
        self.assertIn("downstream re-ran 3 cases, reused 40", out["evidence"])
        row = self.con.execute("SELECT module, cases_rerun FROM gate_log ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual((row["module"], row["cases_rerun"]), ("crops", 3))

    def test_hard_metric_regression_blocks_the_merge(self):
        r = dev.start(self.con, self.repo, "crops", "worse", "a change that cuts plans")
        wt = Path(r["worktree"])
        self.change(wt, "whole", "30")
        self.change(wt, "crops/a.py", "x = 3\n", "crops")
        with self.assertRaises(ReinsError) as e:
            dev.finish(self.con, r["version"])
        self.assertIn("plans_whole", str(e.exception))
        self.assertEqual(modules.get(self.con, r["version"])["status"], "candidate")

    def test_other_modules_files_bring_their_gate(self):
        r = dev.start(self.con, self.repo, "judge", "touch", "a judge change that also edits crops")
        wt = Path(r["worktree"])
        self.change(wt, "crops/a.py", "x = 4\n")                   # judge has no gate of its own; crops does
        out = dev.finish(self.con, r["version"])
        self.assertIn("crops gate green", out["evidence"])

    def test_without_module_gates_the_project_gate_runs(self):
        (self.repo / "reins.toml").write_text('project = "proj"\n[dev]\ngate = "true"\n'
                                              '[modules.judge]\nabout = "judges"\nfiles = ["judge/*"]\n')
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qam", "no module gates"], check=True)
        r = dev.start(self.con, self.repo, "judge", "plain", "a judge change, project gate")
        wt = Path(r["worktree"])
        self.change(wt, "judge/a.py", "x = 5\n")
        out = dev.finish(self.con, r["version"])
        self.assertIn("gate command exit 0", out["evidence"])


class Benchmarks(Base):
    def setUp(self):
        super().setUp()
        cases = self.tmp / "cases.txt"
        cases.write_text("101\n102\n103\n104\n")
        self.v1 = Path(bench.init(self.con, "crops", "proj", cases, "four cases", holdout_fraction=0)["path"])
        for c, body in (("101", "a"), ("102", "b"), ("103", "c")):
            d = self.v1 / "stages" / "crops" / f"ABC_{c}"
            d.mkdir(parents=True); (d / "boxes.json").write_text(body); (d / "run.log").write_text("t=1")
        bench.freeze(self.con, "crops", "v1")

    def test_changed_lists_only_cases_whose_outputs_differ(self):
        out = self.tmp / "new"
        for c, body in (("101", "a"), ("102", "B"), ("104", "d")):
            d = out / f"ABC_{c}"
            d.mkdir(parents=True); (d / "boxes.json").write_text(body); (d / "run.log").write_text("t=2")
        r = bench.changed(self.con, "crops", "crops", out, ignore=["*.log"])
        self.assertEqual((r["changed"], r["same"], r["new"], r["missing"]), (["102"], ["101"], ["104"], ["103"]))
        r = bench.changed(self.con, "crops", "crops", out)          # the log differs too when it is not ignored
        self.assertEqual(r["changed"], ["101", "102"])

    def test_add_cases_grows_only_an_open_version(self):
        new = ["201", "202"]
        with self.assertRaises(ReinsError):                         # v1 is frozen
            bench.add_cases(self.con, "crops", new, "dev", "regressions from batch 3")
        bench.bump(self.con, "crops", "grow")
        r = bench.add_cases(self.con, "crops", new + ["101"], "holdout", "fresh never-tuned cases")
        self.assertEqual((r["added"], r["already"]), (2, 1))
        self.assertEqual(sorted(bench.cases(self.con, "crops", split="holdout")), new)
        row = self.con.execute("SELECT n_cases, n_holdout FROM benchmark WHERE version=2").fetchone()
        self.assertEqual(tuple(row), (6, 2))
        self.assertIn("+2 holdout cases", (Path(bench._row(self.con, "crops")["path"]) / "CHANGELOG.md").read_text())
        with self.assertRaises(ReinsError):
            bench.add_cases(self.con, "crops", ["301"], "dev", "x")  # a reason is required


class Units(Base):
    def test_a_module_benchmark_has_its_own_unit(self):
        pages = self.tmp / "pages.txt"
        pages.write_text("truth-0001\nmulti-exeter__77-100__03\n")
        with self.assertRaises(ReinsError):                         # held to the project's case id pattern
            bench.init(self.con, "boxes", "proj", pages, "pages", holdout_fraction=0, case_pattern=r"^[0-9]+$")
        r = bench.init(self.con, "boxes", "proj", pages, "pages", holdout_fraction=0, case_pattern=r"^[0-9]+$", unit="page")
        self.assertEqual(json.loads((Path(r["path"]) / "MANIFEST.json").read_text())["unit"], "page")
        dup = self.tmp / "dup.txt"
        dup.write_text("a\na\n")
        with self.assertRaises(ReinsError):                         # still unique
            bench.init(self.con, "dup", "proj", dup, "dup", holdout_fraction=0, unit="page")


if __name__ == "__main__":
    unittest.main()
