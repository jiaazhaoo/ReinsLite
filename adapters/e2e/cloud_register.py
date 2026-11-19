#!/usr/bin/env python3
"""(moved here from e2e tools/cloud/register.py 2026-11-19: e2e knows nothing of reins.)
Record in reins every chunk e2e's tools/cloud/watch.py has brought home (docs/CLOUD.md "Running a council"): per case an
outcome for each cloud stage (done when the georef ran on its plans, placed or not; failed when it has no georef row),
then the stage's end. Run it in the session that owns the batches (reins refuses others), as often as you like: a chunk
is registered once (STATE/registered.txt).

    python3 /env/code/ReinsLite/adapters/e2e/cloud_register.py STATE [--stages georef trace] [--prefix MAN_] [--wait | --follow]

--wait: repeat every 5 min and return when every chunk of STATE/status.json is registered (or held by watch.py as
broken, STATE/held.txt: never registered) or an event needs a person (a released claim, a broken chunk, the cap, a
failed check, a chunk reins refused) -- run it in the background and it wakes the session.
--follow: the same as a service (systemd user unit): repeats every 5 min until the watcher has ended ('watch ends'
after its last progress line) and every chunk is registered or held; never returns on an event.
A chunk reins refuses (preflight failed, a stage refused) is NOT written as registered: the refusal is printed, logged
once per reason in STATE/status.log ('NOT REGISTERED') and in STATE/register_failed.json, and retried next round.
2026-11-09 Braintree: refusals were swallowed and 7 chunks sat as 'registered' for 5 h with their stages never started.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REINS = "/env/code/ReinsLite/bin/reins"


def case_ids(W: Path) -> dict:
    """run key -> the batch's case id: the client's own id the batch was opened with (records' orig_oachargeid when the
    run keys are not it, as Mansfield's MAN_<number>), else the run key without its prefix."""
    recs = json.loads((W / "records.json").read_text())
    out = {}
    for k, r in recs.items():
        out[k] = str(r.get("orig_oachargeid") or r.get("oachargeid") or k.split("_", 1)[-1])
    return out


def marks(W: Path) -> tuple[list[tuple[str, str, str]], dict]:
    R = json.loads((W / "lab/georef/out/results_0of1.json").read_text())
    ids = case_ids(W)
    placed = collections.Counter(); seen = set()
    for r in R:
        seen.add(r["case"]); placed[r["case"]] += bool(r.get("accepted"))
    rows = []
    for c in json.loads((W / "cases.json").read_text()):
        if c in seen:
            rows.append((ids[c], "done", f"{placed[c]} plan(s) placed on RunPod" if placed[c] else "georef ran on RunPod, no plan placed"))
        else:
            rows.append((ids[c], "failed", "no georef row"))
    n = len(rows)
    stats = {"plans": len(R), "placed": sum(1 for r in R if r.get("accepted")), "cases": n,
             "cases_placed": sum(1 for c in json.loads((W / "cases.json").read_text()) if placed[c])}
    return rows, stats


WHY = "ran on RunPod (tools/cloud: worker.sh, archive fetched by watch.py, sha256 checked, unpack.py)"


def reins_rc(*a: str, env: dict | None = None) -> tuple[int, str]:
    r = subprocess.run([REINS, *a], capture_output=True, text=True, env=env)
    return r.returncode, (r.stdout + r.stderr).strip()


def reins(*a: str) -> str:
    return reins_rc(*a)[1]


def register(state: Path, B: str, W: Path, stages: list[str], prefix: str) -> tuple[bool, str]:
    """(registered, what happened): preflight, then per stage start (a stage already running is marked and ended, one
    already done is left), mark every case, end; any refusal stops here and says which command and why."""
    rows, st = marks(W)
    f = state / f"marks_{B}.tsv"
    f.write_text("case_id\tstatus\treason\n" + "".join(f"{a}\t{b}\t{c}\n" for a, b, c in rows))
    rc, out = reins_rc("preflight", B, env={**os.environ, "CASE_PREFIX": prefix})
    if rc:
        fails = [l.strip() for l in out.splitlines() if l.strip().startswith("FAIL")]
        return False, f"{B}: NOT REGISTERED: reins preflight failed: {'; '.join(fails)[:300] or out[-300:]}"
    for s in stages:
        rc, out = reins_rc("batch", "stage-start", B, s)
        if rc and f"stage {s} is already done" in out:
            continue
        if rc and f"stage {s} is already running" not in out:
            return False, f"{B}: NOT REGISTERED: reins batch stage-start {s}: {out[-300:]}"
        for a in (("batch", "mark", B, s, "--file", str(f)), ("batch", "stage-end", B, s, "--why", WHY)):
            rc, out = reins_rc(*a)
            if rc:
                return False, f"{B}: NOT REGISTERED: reins {a[0]} {a[1]} {s}: {out[-300:]}"
    status = reins("batch", "status", B).splitlines()[0]
    return True, (f"{B}: cases {st['cases']}, with a placed plan {st['cases_placed']} ({st['cases_placed'] / max(1, st['cases']):.0%}) "
                  f"| plans {st['plans']} placed {st['placed']} | {status[len(B):].strip()[:20]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("state", type=Path)
    ap.add_argument("--stages", nargs="+", default=["georef", "trace"])
    ap.add_argument("--prefix", default="")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--wait", action="store_true")
    g.add_argument("--follow", action="store_true")
    o = ap.parse_args()
    regf = o.state / "registered.txt"
    ff = o.state / "register_failed.json"
    while True:
        failed = json.loads(ff.read_text()) if ff.exists() else {}
        done = set(regf.read_text().split()) if regf.exists() else set()
        st = json.loads((o.state / "status.json").read_text()) if (o.state / "status.json").exists() else []
        for c in st:
            B = c["batch"]
            if B in done or not (o.state / "results" / f"{B}.tar.gz.unpacked").exists():
                continue
            ok, msg = register(o.state, B, Path(c["work_dir"]), o.stages, o.prefix)
            print(time.strftime("%H:%M:%S"), msg, flush=True)
            if ok:
                with open(regf, "a") as fh:
                    fh.write(B + "\n")
                done.add(B); failed.pop(B, None)
            elif failed.get(B) != msg:                          # a new refusal, or a new reason: once in the run's log
                with open(o.state / "status.log", "a") as fh:
                    fh.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
                failed[B] = msg
        ff.write_text(json.dumps(failed, indent=1))
        if not (o.wait or o.follow):
            return 1 if failed else 0
        logtxt = (o.state / "status.log").read_text() if (o.state / "status.log").exists() else ""
        held = set((o.state / "held.txt").read_text().split()) if (o.state / "held.txt").exists() else set()
        if o.follow:
            lines = logtxt.splitlines()
            last = lambda w: max((i for i, l in enumerate(lines) if w in l), default=-1)      # noqa: E731
            if st and all(c["batch"] in done | held for c in st) and last("watch ends") > last("cases done"):
                print(f"all {len(st)} chunks registered" + (f", {len(held)} held as broken: {sorted(held)}" if held else "")); return 0
            time.sleep(300)
            continue
        events = [l for l in logtxt.splitlines() if "RELEASED" in l or "BROKEN" in l or "CAP " in l or "NOT REGISTERED" in l
                  or ("failed checks:" in l and "failed checks: none" not in l)]
        new_events = [e for e in events if e not in (o.state / "events_seen.txt").read_text().splitlines()] if (o.state / "events_seen.txt").exists() else events
        if new_events:
            with open(o.state / "events_seen.txt", "a") as fh:
                fh.write("".join(e + "\n" for e in new_events))
            print("EVENT:", *new_events, sep="\n  "); return 0
        if st and all(c["batch"] in done | held for c in st):
            print(f"all {len(st)} chunks registered" + (f" but {len(held)} held as broken: {sorted(held)}" if held else "")); return 0
        time.sleep(300)


if __name__ == "__main__":
    sys.exit(main())
