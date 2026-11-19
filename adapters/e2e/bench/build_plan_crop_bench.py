#!/usr/bin/env python3
"""Build the two benchmarks e2e's plan_crop module gate scores on (ReinsLite module gates, 2026-10-19). Moved here
from e2e benchmark/s2_crops/build_bench.py on 2026-11-19 (it registers them with reins); e2e's checkout: $E2E_REPO.

    python3 build_plan_crop_bench.py crops      # bench-plan-crops v1
    python3 build_plan_crop_bench.py boxes      # bench-plan-boxes v1

bench-plan-crops: the 99 crop-regression cases (crop_regress.py SETS). inputs/: the user's golden slot rules
  (golden.json, a rule with known_fail failed on main already), the slot baseline (approved.json, approved_eval.json),
  the crop truth (pages.json: 36 Sheffield cases, boxes drawn by the user). The cases' stage-A copies stay where
  crop_regress keeps them (it re-runs steps 2-4 into a scratch copy).
bench-plan-boxes: pages, not cases. 294 crop-truth pages (labels: golden boxes, Jia, drawing seen) and 398 pages of nine
  councils (labels: reference_boxes = the Gemini teacher's boxes to the user's box spec, checked by eye, 4 doubtful
  pages left out). Page images are hard-linked into inputs/pages/. Box spec (user 2026-10-15): never cut a plan, a loose
  box is fine, the drawing's own frame is the edge, a nested inset gets its own box and the big one keeps its own.
No holdout yet: every page and case here was looked at while tuning (v4 was chosen on the nine-council pages); fresh
never-seen pages join the holdout through `reins bench add-cases --split holdout` in v2."""
from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REINS = str(Path(__file__).resolve().parents[3] / "bin" / "reins")
E2E = Path(os.environ.get("E2E_REPO") or "/env/code/e2e-plan-extract")
REGRESS = Path("/data/benchmarks/sheffield-plan-crops/e2e-qa-sample/review/crop_regress")
TRUTH = Path("/data/benchmarks/sheffield-plan-crops/e2e-qa-sample/review/crop_truth/pages.json")
T2 = Path("/data/lab/datasets/plan-boxes/teacher2")


def reins(*a):
    r = subprocess.run([REINS, *a], capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
    if r.returncode:
        sys.exit(r.returncode)


def crops():
    sys.path.insert(0, str(E2E / "benchmark/s2_crops"))
    import crop_regress as cr
    tmp = Path(tempfile.mkdtemp(prefix="plan-crops-"))
    ids = [src.name for src, _raw, _t in cr.all_cases()]
    (tmp / "cases.txt").write_text("\n".join(ids) + "\n")
    inp = tmp / "inputs"; inp.mkdir()
    for f in ("golden.json", "approved.json", "approved_eval.json"):
        shutil.copy(REGRESS / f, inp / f)
    shutil.copy(TRUTH, inp / "crop_truth_pages.json")
    (inp / "sets.json").write_text(json.dumps([[str(s), str(r) if r else None, t] for s, r, t in cr.SETS], indent=1))
    reins("bench", "init", "plan-crops", "--cases", str(tmp / "cases.txt"), "--holdout-fraction", "0", "--unit", "crop_case", "--project",
          "e2e-plan-extract", "--about", "plan_crop module benchmark: 99 crop-regression cases; slots, the user's golden "
          "slot rules, the crop truth of 36 cases")
    reins("bench", "add-input", "plan-crops", "--from", str(inp))
    reins("bench", "freeze", "plan-crops", "--changelog", "v1: the crop-regression set as of 2026-10-19 (baseline = "
          "approved.json of 2026-09-30, golden rules of 2026-09-30, one known_fail)")


def boxes():
    T = [p for p in json.load(open(TRUTH)) if p["truth_kind"] in ("W", "P", "M", "N")]
    drop = set(json.load(open(T2 / "test_excluded.json"))["dropped"])
    G = json.load(open(T2 / "gemini.json"))
    M = [p for p in json.load(open(T2 / "pool.json")) if p["split"] == "test" and p["id"] not in drop and p["id"] in G]
    tmp = Path(tempfile.mkdtemp(prefix="plan-boxes-", dir="/data/benchmarks/tmp"))   # same filesystem: images hard-link
    pages = tmp / "inputs" / "pages"; pages.mkdir(parents=True)
    rows, labels = [], []
    for p in T:
        pid = f"truth-{p['id']:04d}"
        dst = pages / (pid + Path(p["path"]).suffix.lower())
        os.link(Path(p["path"]).resolve(), dst)
        rows.append({"page_id": pid, "set": "truth", "council": "sheffield", "case": p["case"], "page": p["page"],
                     "file": f"pages/{dst.name}", "kind": p["truth_kind"]})
        labels.append({"case_id": pid, "target": "plan_boxes", "source": "golden", "by": "Jia", "saw_drawing": True,
                       "value": [{"box": b["box"], "type": b["type"], "whole_page": bool(b.get("whole"))} for b in p["truth_boxes"]],
                       "note": "crop truth drawn by the user (2026-09)"})
    for p in M:
        pid = "multi-" + p["id"].replace("/", "_")
        dst = pages / (pid + Path(p["path"]).suffix.lower())
        os.link(Path(p["path"]).resolve(), dst)
        rows.append({"page_id": pid, "set": "multi", "council": p["council"], "case": p["case"], "page": Path(p["path"]).name,
                     "file": f"pages/{dst.name}", "kind": ""})
        labels.append({"case_id": pid, "target": "plan_boxes", "source": "reference_boxes", "by": "gemini-3.8-flash",
                       "saw_drawing": True, "value": [{"box": x["box"], "type": x["kind"]} for x in G[p["id"]]["boxes"]],
                       "note": "teacher boxes to the user's box spec, checked by eye 2026-10-16"})
    with (tmp / "inputs" / "pages.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    (tmp / "cases.txt").write_text("\n".join(r["page_id"] for r in rows) + "\n")
    with (tmp / "labels.jsonl").open("w") as f:
        for l in labels:
            f.write(json.dumps(l) + "\n")
    reins("bench", "init", "plan-boxes", "--cases", str(tmp / "cases.txt"), "--holdout-fraction", "0", "--unit", "page", "--project",
          "e2e-plan-extract", "--about", "plan-box benchmark: 294 user-labelled Sheffield pages + 398 nine-council pages "
          "with checked teacher boxes; scored with the never-cut edge test")
    reins("bench", "add-input", "plan-boxes", "--from", str(tmp / "inputs"))
    reins("bench", "label", "plan-boxes", "--file", str(tmp / "labels.jsonl"))
    reins("bench", "freeze", "plan-boxes", "--changelog", f"v1: {len(T)} truth pages + {len(M)} nine-council pages")


if __name__ == "__main__":
    {"crops": crops, "boxes": boxes}[sys.argv[1]]()
