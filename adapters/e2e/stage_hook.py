#!/usr/bin/env python3
"""e2e's stage boundaries and per-case outcomes, reported to ReinsLite (`reins`). A no-op without $REINS_BATCH.

e2e's tools/run_local_qa.sh calls $E2E_STAGE_HOOK at each stage boundary (`$E2E_STAGE_HOOK start|end STAGE EVAL_DIR`,
from its repo root; a non-zero exit stops the run); a reins run sets it to this file ([run.env] in reins.toml). It
reads e2e's own output checks from the checkout being run (<repo>/tools/run_record.py; the repo is the current
directory, or $E2E_REPO). Until 2026-11-19 this file lived in e2e as tools/reins_hook.py.

    python3 stage_hook.py start STAGE EVAL_DIR      reins batch stage-start (skipped when already running/done)
    python3 stage_hook.py end STAGE EVAL_DIR        per-case outcome from the stage's outputs -> reins batch mark,
                                                    then reins batch stage-end (refused if a case has none)
    python3 stage_hook.py cases EVAL_DIR > X.tsv    the batch's case set on stdout: every record as council, oachargeid,
                                                    further_information_reference (open the batch with --cases X.tsv)
    python3 stage_hook.py check-cases EVAL_DIR FILE exit 0 when FILE (a batch's case set) equals the records

A case is its three fields together, in every workflow (user 2026-11-20: 一个case在我们这永远是3字段作为id,无论任何工作流和
框架): the case set, the check and the marks name each case by council + oachargeid + further_information_reference.
A batch opened before 2026-11-20 on a one-id case set is checked and marked in that form until it ends.

Stages and the steps of tools/run_local_qa.sh they cover; a case's outcome is read from what the steps wrote:
  prepare    0                cases.json + out/prepare/<case>.json (e2e_plan_extract/s1_inputs/sources.py)
                                                               done = pages to work on, skipped = no scans,
                                                               failed = scans that gave 0 pages (the inventory's reason)
  plan_crop  1-5 stage A      out/stageA/<case>/summary.json   done = summary written (0 plans is a valid result);
                                                               failed = no page of the case decoded (skipped_pages)
  georef     6-10             lab/georef/out/results_0of1.json done = a row for each of its plans with an address
                                                               and its own OCR / scale / road names (steps 6-8, by plan key)
  trace      11               lab/local.json                   done = a row per plan with an address; failed = all rows errored
  check      12-15            lab/qa_final.csv + judge_*.json  done = the case has a lane AND every step-14 answer it
                                                               should have (both judges on an open case; the tie-break
                                                               on a split when on; the boundary recheck when both
                                                               judges passed it); failed = names the missing answer
A case without the inputs a stage needs is `skipped` with the reason (no scans, no plan_image, no address); one
whose inputs exist but whose output is missing is `failed`. The hook never changes an output.

`check` also hands its steps' output checks (tools/run_record.py, computed afresh, steps 12-15) to `reins batch
stage-end --checks`: one failing check ends the stage `failed`, not `done`, so the batch cannot be reported done.
2026-10-10, sheffield-wp1wp7-rework-20261008-2: `check` closed done=1839 from lanes alone while gemini had answered
1577/1745 open cases, luna 1619/1745 and the boundary recheck 0/399 -- and qa_final takes a missing recheck as a
pass, so 393 auto passes skipped it.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(os.environ.get("E2E_REPO") or os.getcwd()).resolve()
sys.path.insert(0, str(REPO / "tools"))
import run_record  # noqa: E402  (tools/run_record.py: the step output checks)
from run_record import plan_keys  # noqa: E402  (s3_georef.plan_key: each plan's own output name)

STAGES = ("prepare", "plan_crop", "georef", "trace", "check")
KEY = ["council", "oachargeid", "further_information_reference"]
FIR = "further-information-reference"                     # the client's field name, as records.json keeps it
STAGE_CHECKS = {"check": ("12", "13", "14", "15")}        # stages whose step checks decide done / failed
PREFIX = os.environ.get("CASE_PREFIX", "SHF_")


def load(p: Path, default):
    try:
        return json.loads(p.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def oaid(case: str) -> str:
    """the work dir's case key without CASE_PREFIX (only where no record says what the client calls it)"""
    return case[len(PREFIX):] if case.startswith(PREFIX) else case


def records(W: Path) -> list[str]:
    return list(load(W / "records.json", {}))


def native_ids(W: Path) -> dict[str, str]:
    """work-dir case key -> the id reins knows the case by: the client's own oachargeid (council = the batch scope, the
    further-information-reference stays in records.json; user 2026-11-06: 三个字段组合起来全部唯一). A work dir keyed by
    another id kept the client's in orig_oachargeid (Mansfield: locallandchargeid in oachargeid). Never a prefix.
    Two records naming one oachargeid cannot both be cases: refused."""
    recs = load(W / "records.json", {})
    out = {c: str((r or {}).get("orig_oachargeid") or (r or {}).get("oachargeid") or oaid(c)).strip() for c, r in recs.items()}
    seen: dict[str, str] = {}
    for c, k in out.items():
        if k in seen:
            sys.exit(f"stage_hook: records {seen[k]} and {c} both name oachargeid {k!r}: one case, two records")
        seen[k] = c
    return out


def council_of(W: Path, rec: dict | None = None) -> str:
    """The case's council: the record's own, else the work dir's (/data/councils/<council>/...), else the batch scope's.
    None of them: refused -- a case is never named without its council."""
    if rec and str(rec.get("council") or "").strip():
        return str(rec["council"]).strip().lower()
    parts = Path(W).resolve().parts
    if "councils" in parts and parts.index("councils") + 1 < len(parts):
        return parts[parts.index("councils") + 1]
    if os.environ.get("REINS_BATCH"):
        return os.environ["REINS_BATCH"].split("-", 1)[0]
    sys.exit(f"stage_hook: {W} is not under /data/councils/<council>/ and its records name no council: "
             "a case is council + oachargeid + further_information_reference")


def native_keys(W: Path) -> dict[str, dict]:
    """work-dir case key -> the case's three fields (council, the client's oachargeid as native_ids, the record's
    further-information-reference, blank where the client left it blank)."""
    recs = load(W / "records.json", {})
    ids = native_ids(W)
    return {c: {"council": council_of(W, r), "oachargeid": ids[c],
                "further_information_reference": str((r or {}).get(FIR) or "").strip()} for c, r in recs.items()}


def key_tsv(rows: list[dict], extra: tuple = ()) -> str:
    cols = KEY + list(extra)
    return "\t".join(cols) + "\n" + "".join("\t".join(str(r.get(c, "")) for c in cols) + "\n" for r in rows)


def read_case_set(path: str) -> tuple[bool, set]:
    """(keyed, cases): a case set with the three columns gives triples; an older one-id set gives ids."""
    lines = [ln for ln in Path(path).read_text(encoding="utf-8-sig").splitlines() if ln.strip()]
    sep = "\t" if lines and "\t" in lines[0] else ","
    if lines and all(k in lines[0].split(sep) for k in KEY):
        rows = csv.DictReader(lines, delimiter=sep)
        return True, {tuple((r.get(k) or "").strip() for k in KEY) for r in rows}
    return False, {ln.strip() for ln in lines if ln.strip() not in ("oachargeid", "case_id")}


def batch_keyed(batch: str) -> bool:
    """True when the batch was opened on a case set of the three fields (reins stores its case_key_fields)."""
    r = reins("batch", "status", batch, "--json")
    return bool(r.returncode == 0 and (json.loads(r.stdout).get("batch") or {}).get("case_key_fields"))


def outcomes(stage: str, W: Path) -> list[tuple[str, str, str]]:
    """(case, status, reason) for every record."""
    recs = records(W)
    in_pipe = set(load(W / "cases.json", []))
    scans = {c for c in in_pipe if (W / "raw" / c).exists()}
    LAB, G = W / "lab", W / "lab/georef"
    plans = load(LAB / "plans.json", [])
    by_case: dict[str, list[dict]] = {}
    for p in plans:
        by_case.setdefault(p["case"], []).append(p)
    # each output is read once, not once per case (local.json of a 2k batch is hundreds of MB)
    georef_rows = {(r["case"], r.get("cls")) for r in load(G / "out/results_0of1.json", [])} if stage == "georef" else set()
    # GEOREF=off (run_local_qa.sh): rows marked georef 'off' need no OCR / scale / road names of their own
    georef_off = {(r["case"], r.get("cls")) for r in load(G / "out/results_0of1.json", []) if r.get("georef") == "off"} \
        if stage == "georef" else set()
    # steps 6-8 per plan, under the plan's own key: a name shared with another case's plan once fed it that plan's OCR
    keys = plan_keys(load(G / "cases.json", [])) if stage == "georef" else {}
    vlm_read = load(G / "read_vlm.json", {}) if stage == "georef" else {}
    scale = load(G / "scale_text.json", {}) if stage == "georef" else {}
    loc = load(LAB / "local.json", {}) if stage == "trace" else {}
    lanes = ({r["case"] for r in csv.DictReader(open(LAB / "qa_final.csv"))} if (LAB / "qa_final.csv").exists() else set()) \
        if stage == "check" else set()
    gaps: dict[str, list[str]] = {}                       # case -> the step-14 answers it lacks
    if stage == "check":
        for name, _, miss in run_record.judge_gaps(W):
            for c in miss:
                gaps.setdefault(c, []).append(name.split(" answered")[0])
    out = []
    for c in recs:
        if stage == "prepare":
            inv = load(W / "out/prepare" / f"{c}.json", None)
            if c not in scans and not inv:
                out.append((c, "skipped", "no scans"))
            elif inv is None:
                out.append((c, "done", ""))                       # raw/<case> without an inventory (before step 0)
            else:
                out.append((c, inv["status"], inv.get("status_reason", "")))
            continue
        if stage != "check" and c not in scans:
            out.append((c, "skipped", "no scans")); continue
        if stage == "plan_crop":
            summ = load(W / "out/stageA" / c / "summary.json", None)
            if summ is None:
                out.append((c, "failed", "stage A wrote no summary"))
            elif summ.get("skipped_pages") and not summ.get("readable_pages"):
                out.append((c, "failed", f"no page decoded: {summ['skipped_pages'][0]['reason'][:80]}"))
            else:
                out.append((c, "done", ""))
        elif stage in ("georef", "trace"):
            mine = by_case.get(c, [])
            if not mine:
                out.append((c, "skipped", "no plan_image")); continue
            need = [p for p in mine if p.get("address")]
            if not need:
                out.append((c, "skipped", "no address")); continue
            if stage == "georef":
                miss = [p for p in need if (c, p["cls"]) not in georef_rows]
                unread = [p for p in need if (c, p["cls"]) not in georef_off and (
                          p["image"] not in keys or not (G / "ocr" / f"{keys[p['image']]}.json").exists()
                          or keys[p["image"]] not in vlm_read or "plan:" + keys[p["image"]] not in scale)]
                out.append((c, "failed", f"no georef row for {len(miss)} plan_image(s)") if miss else
                           (c, "failed", f"no OCR / scale / road names of its own for {len(unread)} plan_image(s)") if unread
                           else (c, "done", ""))
            else:
                rows = [loc.get(f"{c}|{p['cls']}") for p in need]
                if any(r is None for r in rows):
                    out.append((c, "failed", "no trace row"))
                elif all(r.get("error") for r in rows):
                    out.append((c, "failed", f"trace error: {str(rows[0].get('error'))[:80]}"))
                else:
                    out.append((c, "done", ""))
        elif stage == "check":
            if c not in lanes:
                out.append((c, "failed", "no lane in qa_final.csv"))
            elif c in gaps:
                out.append((c, "failed", "no answer from " + ", ".join(gaps[c])))
            else:
                out.append((c, "done", ""))
    return out


def reins(*args: str) -> subprocess.CompletedProcess:
    exe = os.environ.get("REINS_BIN", "reins")
    return subprocess.run([exe, *args], capture_output=True, text=True)


def stage_status(batch: str, stage: str) -> str | None:
    r = reins("batch", "status", batch, "--json")
    if r.returncode:
        return None
    for s in json.loads(r.stdout)["stages"]:
        if s["stage"] == stage:
            return s["status"]
    return None


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "cases":
        print(key_tsv(list(native_keys(Path(sys.argv[2])).values())), end="")
        return 0
    if len(sys.argv) == 4 and sys.argv[1] == "check-cases":
        keyed, have = read_case_set(sys.argv[3])
        W = Path(sys.argv[2])
        want = {tuple(k[f] for f in KEY) for k in native_keys(W).values()} if keyed else set(native_ids(W).values())
        form = "council, oachargeid, further_information_reference" if keyed else "oachargeid only (opened before 2026-11-20)"
        print(f"batch case set = records.json ({len(want)}; {form})" if want == have else
              f"batch case set differs from records.json ({form}): {len(have - want)} extra, {len(want - have)} missing"
              + (f", e.g. extra {sorted(have - want)[:2]} missing {sorted(want - have)[:2]}" if want != have else ""))
        return 0 if want == have else 1
    batch = os.environ.get("REINS_BATCH")
    if not batch:
        return 0                                                   # not run under reins: nothing to report
    if len(sys.argv) != 4 or sys.argv[1] not in ("start", "end") or sys.argv[2] not in STAGES:
        sys.exit(__doc__)
    verb, stage, W = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    st = stage_status(batch, stage)
    if st is None:
        print(f"stage_hook: stage {stage} is not planned in {batch}; nothing reported", file=sys.stderr)
        return 0
    if verb == "start":
        if st in ("running", "done", "skipped"):
            return 0
        r = reins("batch", "stage-start", batch, stage, "--output", str(W / "lab"), "--log", str(W / "logs"))
        print(r.stdout + r.stderr, end="", file=sys.stderr)
        return r.returncode
    if st in ("done", "skipped"):
        return 0
    if st in ("planned", "failed"):
        r = reins("batch", "stage-start", batch, stage, "--output", str(W / "lab"), "--log", str(W / "logs"))
        if r.returncode:
            print(r.stderr, end="", file=sys.stderr); return r.returncode
    with tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False) as f:
        if batch_keyed(batch):                          # each case by its three fields
            keys = native_keys(W)
            f.write(key_tsv([{**keys[c], "status": s, "reason": why} for c, s, why in outcomes(stage, W)],
                            ("status", "reason")))
        else:                                           # a batch opened on one id before 2026-11-20
            ids = native_ids(W)
            rows = [(ids.get(c) or oaid(c), s, why) for c, s, why in outcomes(stage, W)]
            f.write("oachargeid\tstatus\treason\n" + "".join(f"{c}\t{s}\t{why}\n" for c, s, why in rows))
    end = ["batch", "stage-end", batch, stage]
    if stage in STAGE_CHECKS:
        with tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False) as g:
            g.write("check\tok\tdetail\n" + "".join(
                f"step {n}: {name}\t{'ok' if ok else 'FAIL'}\t{detail}\n"
                for n in STAGE_CHECKS[stage] for name, ok, detail in run_record.checks(n, W)))
        end += ["--checks", g.name]
    try:
        for args in (("batch", "mark", batch, stage, "--file", f.name), tuple(end)):
            r = reins(*args)
            print(r.stdout + r.stderr, end="", file=sys.stderr)
            if r.returncode:
                return r.returncode
    finally:
        os.unlink(f.name)
        if stage in STAGE_CHECKS:
            os.unlink(g.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
