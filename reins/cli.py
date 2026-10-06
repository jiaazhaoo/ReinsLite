"""reins: the command line over the registry. `reins <group> --help` for each group.

  module    add | new | release | retire | note | status | list           C2 module versions
  dev       start | finish | abandon | release | list                     C2 lifecycle with git worktrees
  batch     open | approve | stage-start | mark | stage-end | skip-stage | pause | resume | fail | close | status | list
  run       BATCH STAGE [opts] -- CMD...                                  C7 launcher (supervised, pgid-controlled)
  ctl       pause | resume | stop BATCH                                   C7/C10 control a running batch
  case      OACHARGEID                                                    C1 one case's whole history
  spend     summary BATCH | price MODEL IN OUT | weekly                   C9
  gateway   serve                                                         C9 paid-call proxy (holds the key)
  watchdog  once | run                                                    C8
  board     serve                                                         C14
  rules     list [rules.toml] | diff OLD NEW                              C7b
  decide    add | list                                                    C13
  lease     list | release RESOURCE                                       C10
  gate      record VERSION ...                                            C5
  notify    list | ack ID...
  lint      PATH...                                                       C0

Project settings come from the nearest reins.toml (project, case_id_pattern, [dev], [modules]).
"""
from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

from . import batches, decisions, dev, gate, glossary, leases, modules, names, notify, rules, runner, spend
from .store import ReinsError, connect


def project_root(cwd: Path) -> Path | None:
    for d in (cwd, *cwd.parents):
        if (d / "reins.toml").is_file():
            return d
    return None


def project_config(cwd: Path) -> dict:
    r = project_root(cwd)
    return tomllib.loads((r / "reins.toml").read_text(encoding="utf-8")) if r else {}


def _project(args, cfg) -> str:
    p = getattr(args, "project", None) or cfg.get("project")
    if not p:
        raise ReinsError("no project: pass --project or add project = \"...\" to the repo's reins.toml")
    return p


def _pins(items) -> dict:
    out = {}
    for it in items or []:
        if "=" not in it:
            raise ReinsError(f"--pin {it!r}: expected key=value")
        k, v = it.split("=", 1)
        out[k] = v
    return out


def _print_status(st: dict) -> None:
    b = st["batch"]
    flags = " MIXED_VERSION" if b["mixed_version"] else ""
    print(f"{b['batch_id']}  [{b['status']}]{flags}  {b['n_cases']} cases  release={b['release_name'] or '-'}"
          f"  spend ${b['spent']:.3f}/${b['spend_cap']:.2f}")
    print(f"  purpose: {b['purpose']}")
    if b["status_reason"]:
        print(f"  reason:  {b['status_reason']}")
    if b["aliases"]:
        print(f"  also called: {', '.join(b['aliases'])}")
    if b["parent"]:
        print(f"  parent: {b['parent']}")
    for s in st["stages"]:
        acc = s["done"] + s["skipped"] + s["failed"]
        paid = " paid" if s["paid"] else ""
        print(f"  {s['ord']:>2} {s['stage']:<20} {s['status']:<8} {acc:>6}/{b['n_cases']:<6} "
              f"done={s['done']} skipped={s['skipped']} failed={s['failed']} running={s['running']}{paid}  {s['module_version'] or ''}")
    for p in st["processes"]:
        print(f"     process #{p['id']} pgid {p['pgid']} {p['state']} attempt {p['attempt']}  log {p['log_path']}")
    for e in st["recent_events"]:
        print(f"     {e['at']}  {e['event']}  {e['detail'] or ''}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="reins", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("module").add_subparsers(dest="sub", required=True)
    x = m.add_parser("add"); x.add_argument("name"); x.add_argument("--project"); x.add_argument("--about", required=True)
    x = m.add_parser("new"); x.add_argument("module"); x.add_argument("suffix"); x.add_argument("--about", required=True)
    x.add_argument("--pin", action="append")
    x = m.add_parser("release"); x.add_argument("version"); x.add_argument("--gate", required=True)
    x = m.add_parser("retire"); x.add_argument("version"); x.add_argument("--why", required=True)
    x = m.add_parser("note"); x.add_argument("version"); x.add_argument("event"); x.add_argument("detail")
    x = m.add_parser("status"); x.add_argument("module")
    m.add_parser("list")

    d = sub.add_parser("dev").add_subparsers(dest="sub", required=True)
    x = d.add_parser("start"); x.add_argument("module"); x.add_argument("suffix"); x.add_argument("--about", required=True)
    x.add_argument("--from-batch"); x.add_argument("--cases", help="comma-separated oachargeids: the pilot set")
    x.add_argument("--files", help="space-separated globs this version touches"); x.add_argument("--repo")
    x = d.add_parser("finish"); x.add_argument("version"); x.add_argument("--skip-tier"); x.add_argument("--why")
    x = d.add_parser("abandon"); x.add_argument("version"); x.add_argument("--why", required=True)
    x = d.add_parser("release"); x.add_argument("--note", default=""); x.add_argument("--repo")
    x = d.add_parser("list"); x.add_argument("--project")

    b = sub.add_parser("batch").add_subparsers(dest="sub", required=True)
    x = b.add_parser("open")
    for a in ("--council", "--wp", "--purpose", "--cases"):
        x.add_argument(a, required=True)
    x.add_argument("--type", required=True, choices=names.BATCH_TYPES)
    x.add_argument("--stage", action="append", required=True,
                   help="NAME[=MODULE_VERSION][:paid][:cap=USD][:limit=SECONDS], in run order")
    x.add_argument("--parent"); x.add_argument("--release"); x.add_argument("--alias", action="append")
    x.add_argument("--project"); x.add_argument("--work-dir"); x.add_argument("--cap", type=float, default=0.0)
    x = b.add_parser("approve"); x.add_argument("batch"); x.add_argument("--cap", type=float, required=True)
    x.add_argument("--by", default="user")
    x = b.add_parser("stage-start"); x.add_argument("batch"); x.add_argument("stage")
    x.add_argument("--module-version"); x.add_argument("--output"); x.add_argument("--log")
    x = b.add_parser("mark"); x.add_argument("batch"); x.add_argument("stage"); x.add_argument("--file")
    x.add_argument("case", nargs="?"); x.add_argument("status", nargs="?"); x.add_argument("reason", nargs="?")
    x = b.add_parser("stage-end"); x.add_argument("batch"); x.add_argument("stage")
    x = b.add_parser("skip-stage"); x.add_argument("batch"); x.add_argument("stage"); x.add_argument("--why", required=True)
    for verb in ("pause", "resume", "fail"):
        x = b.add_parser(verb); x.add_argument("batch"); x.add_argument("--why", required=True)
    x = b.add_parser("close"); x.add_argument("batch")
    x = b.add_parser("status"); x.add_argument("batch"); x.add_argument("--json", action="store_true")
    x = b.add_parser("list"); x.add_argument("--live", action="store_true")

    x = sub.add_parser("run"); x.add_argument("batch"); x.add_argument("stage"); x.add_argument("--module-version")
    x.add_argument("--cwd"); x.add_argument("--retries", type=int); x.add_argument("command", nargs=argparse.REMAINDER)
    x = sub.add_parser("ctl"); x.add_argument("verb", choices=["pause", "resume", "stop"]); x.add_argument("batch")
    x.add_argument("--why", default="")
    x = sub.add_parser("case"); x.add_argument("case_id")

    s = sub.add_parser("spend").add_subparsers(dest="sub", required=True)
    x = s.add_parser("summary"); x.add_argument("batch")
    x = s.add_parser("price"); x.add_argument("model"); x.add_argument("input_per_m", type=float)
    x.add_argument("output_per_m", type=float); x.add_argument("--source", default="")
    s.add_parser("weekly")
    x = sub.add_parser("gateway").add_subparsers(dest="sub", required=True).add_parser("serve"); x.add_argument("--port", type=int)
    w = sub.add_parser("watchdog").add_subparsers(dest="sub", required=True)
    w.add_parser("once"); x = w.add_parser("run"); x.add_argument("--interval", type=int, default=60)
    x = sub.add_parser("board").add_subparsers(dest="sub", required=True).add_parser("serve"); x.add_argument("--port", type=int)

    r = sub.add_parser("rules").add_subparsers(dest="sub", required=True)
    x = r.add_parser("list"); x.add_argument("path", nargs="?")
    x = r.add_parser("diff"); x.add_argument("old", type=Path); x.add_argument("new", type=Path)
    x.add_argument("--key", default="oachargeid"); x.add_argument("--lane", default="lane"); x.add_argument("--json", action="store_true")
    dc = sub.add_parser("decide").add_subparsers(dest="sub", required=True)
    x = dc.add_parser("add"); x.add_argument("text"); x.add_argument("--evidence", required=True); x.add_argument("--by", default="user")
    x.add_argument("--supersedes", type=int); x.add_argument("--project")
    x = dc.add_parser("list"); x.add_argument("--all", action="store_true"); x.add_argument("--project")
    le = sub.add_parser("lease").add_subparsers(dest="sub", required=True)
    le.add_parser("list"); x = le.add_parser("release"); x.add_argument("resource"); x.add_argument("--force", action="store_true")
    x = sub.add_parser("gate").add_subparsers(dest="sub", required=True).add_parser("record")
    x.add_argument("version"); x.add_argument("--benchmark", required=True); x.add_argument("--tiers", required=True)
    x.add_argument("--stages", required=True); x.add_argument("--missed-error", type=int, required=True)
    x.add_argument("--review-load", type=int, required=True); x.add_argument("--base-missed-error", type=int, required=True)
    x.add_argument("--base-review-load", type=int, required=True); x.add_argument("--golden-regressions", type=int, default=0)
    x.add_argument("--diff"); x.add_argument("--skipped")
    n = sub.add_parser("notify").add_subparsers(dest="sub", required=True)
    n.add_parser("list"); x = n.add_parser("ack"); x.add_argument("ids", nargs="+", type=int)
    x = sub.add_parser("lint"); x.add_argument("paths", nargs="+", type=Path)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cwd = Path.cwd()
    cfg = project_config(cwd)
    try:
        if args.cmd == "lint":
            hits = glossary.lint(args.paths)
            for h in hits:
                print(f"{h.where}: {h.word!r} -> {h.hint}")
            print(f"{len(hits)} glossary violations")
            return 1 if hits else 0
        if args.cmd == "gateway":
            from . import gateway
            gateway.serve(args.port); return 0
        if args.cmd == "board":
            from . import board
            board.serve(args.port); return 0
        if args.cmd == "watchdog":
            from . import watchdog
            if args.sub == "once":
                found = watchdog.once()
                print("\n".join(found) or "nothing to report"); return 0
            watchdog.run(args.interval); return 0
        if args.cmd == "rules":
            if args.sub == "list":
                p = Path(args.path) if args.path else (project_root(cwd) or cwd) / "rules.toml"
                print(rules.describe(rules.load(p))); return 0
            d = rules.diff(args.old, args.new, args.key, args.lane)
            print(json.dumps(d, ensure_ascii=False, indent=1) if args.json else rules.format_diff(d)); return 0

        con = connect()
        if args.cmd == "module":
            return _module(con, args, cfg)
        if args.cmd == "dev":
            return _dev(con, args, cfg, cwd)
        if args.cmd == "batch":
            return _batch(con, args, cfg)
        if args.cmd == "run":
            cmd = args.command[1:] if args.command and args.command[0] == "--" else args.command
            res = runner.start(con, args.batch, args.stage, cmd, Path(args.cwd or cwd), args.module_version, args.retries)
            for w in res["warnings"]:
                print(f"WARNING {w}", file=sys.stderr)
            print(f"started (supervisor pid {res['supervisor_pid']}); log {res['log']}\n"
                  f"  reins batch status {args.batch}   |   reins ctl pause|resume|stop {args.batch}")
            return 0
        if args.cmd == "ctl":
            print(runner.ctl(con, args.verb, args.batch, args.why)); return 0
        if args.cmd == "case":
            return _case(con, args)
        if args.cmd == "spend":
            if args.sub == "summary":
                print(json.dumps(spend.summary(con, args.batch), ensure_ascii=False, indent=1))
            elif args.sub == "price":
                spend.set_price(con, args.model, args.input_per_m, args.output_per_m, args.source); print("ok")
            else:
                for w in spend.weekly(con):
                    print(f"{w['week']}  {w['project']:<22} ${w['usd'] or 0:8.2f}  {w['calls']:>6} calls  {w['hits']:>6} cache hits")
            return 0
        if args.cmd == "decide":
            if args.sub == "add":
                i = decisions.add(con, args.text, args.evidence, args.by, args.project or cfg.get("project"), args.supersedes)
                print(f"decision #{i} recorded")
            else:
                for d in decisions.list_(con, args.project or cfg.get("project"), args.all):
                    sup = f"  [superseded by #{d['superseded_by']}]" if d["status"] == "superseded" else ""
                    print(f"#{d['id']} {d['at'][:10]} ({d['by_whom']}) {d['text']}{sup}\n     evidence: {d['evidence']}")
            return 0
        if args.cmd == "lease":
            if args.sub == "list":
                for l in leases.list_(con):
                    print(f"{l['resource']:<60} {l['holder']}  since {l['since']}  {l['purpose'] or ''}")
            else:
                leases.release(con, args.resource, force=args.force); print("released")
            return 0
        if args.cmd == "gate":
            r = gate.record(con, args.version, benchmark=args.benchmark, tiers=args.tiers, stages_covered=args.stages,
                            missed_error=args.missed_error, review_load=args.review_load,
                            base_missed_error=args.base_missed_error, base_review_load=args.base_review_load,
                            golden_regressions=args.golden_regressions, diff_path=args.diff, skipped=args.skipped)
            print(f"gate {r['status']}"); return 0 if r["status"] == "green" else 1
        if args.cmd == "notify":
            if args.sub == "list":
                for n in notify.pending(con):
                    print(f"#{n['id']} {n['at']} [{n['level']}] {n['title']}\n     {n['body'] or ''}")
            else:
                notify.ack(con, args.ids); print("acked")
            return 0
        return 0
    except ReinsError as e:
        print(f"reins: {e}", file=sys.stderr)
        return 2


def _module(con, args, cfg) -> int:
    if args.sub == "add":
        modules.add(con, args.name, _project(args, cfg), args.about); print(f"module {args.name} added")
    elif args.sub == "new":
        print(modules.new(con, args.module, args.suffix, args.about, Path.cwd(), _pins(args.pin)))
    elif args.sub == "release":
        modules.release(con, args.version, args.gate); print(f"{args.version} released")
    elif args.sub == "retire":
        modules.retire(con, args.version, args.why); print(f"{args.version} retired")
    elif args.sub == "note":
        modules.note(con, args.version, args.event, args.detail)
    elif args.sub == "status":
        st = modules.status(con, args.module)
        p = st["production"]
        print(f"{args.module}: production = {p['version'] if p else 'none released'}")
        for c in st["candidates"]:
            print(f"  candidate {c['version']}  {c['branch'] or '-'}  session={c['session'] or '-'}  {c['about']}")
    elif args.sub == "list":
        for r in con.execute("SELECT v.version, v.status, m.project, v.about FROM module_version v"
                             " JOIN module m ON m.name = v.module ORDER BY v.module, v.day, v.seq"):
            print(f"{r['version']:<45} {r['status']:<10} {r['project']:<20} {r['about']}")
    return 0


def _dev(con, args, cfg, cwd) -> int:
    repo = Path(getattr(args, "repo", None) or cfg.get("dev", {}).get("repo") or (project_root(cwd) or cwd))
    if args.sub == "start":
        r = dev.start(con, repo, args.module, args.suffix, args.about, args.from_batch,
                      [c for c in (args.cases or "").split(",") if c], args.files.split() if args.files else None)
        for w in r["warnings"]:
            print(f"WARNING {w}", file=sys.stderr)
        print(f"{r['version']}\n  cd {r['worktree']}      # branch {r['branch']}")
        if r["pilot_cases"]:
            print(f"  pilot cases: {', '.join(r['pilot_cases'])}")
    elif args.sub == "finish":
        r = dev.finish(con, args.version, args.skip_tier, args.why)
        print(f"{r['version']} released; main {r['main']}\n  {r['evidence']}")
    elif args.sub == "abandon":
        dev.abandon(con, args.version, args.why); print("abandoned")
    elif args.sub == "release":
        r = dev.release(con, repo, args.note)
        print(f"{r['name']} -> {r['worktree']} (read-only)\n  contains: {', '.join(r['versions']) or 'no released module versions'}")
    elif args.sub == "list":
        d = dev.list_(con, args.project or cfg.get("project"))
        print("candidates:")
        for c in d["candidates"]:
            print(f"  {c['version']:<45} {c['branch'] or '-':<40} session {(c['session'] or '-')[:8]}  {c['about']}")
        print("released:")
        for c in d["released"]:
            print(f"  {c['version']:<45} {c['created'][:10]}  {c['about']}")
        print("releases:")
        for r in d["releases"]:
            print(f"  {r['name']:<40} {r['created'][:16]}  {r['worktree']}")
    return 0


def _batch(con, args, cfg) -> int:
    if args.sub == "open":
        bid = batches.open_(con, project=_project(args, cfg), council=args.council, wp=args.wp, type_=args.type,
                            purpose=args.purpose, case_file=Path(args.cases),
                            stages=[batches.parse_stage_spec(s) for s in args.stage], parent=args.parent,
                            release_name=args.release, aliases=args.alias,
                            case_pattern=cfg.get("case_id_pattern", names.DEFAULT_CASE_PATTERN),
                            work_dir=args.work_dir, spend_cap=args.cap)
        print(bid)
    elif args.sub == "approve":
        batches.approve_spend(con, args.batch, args.cap, args.by); print(f"{args.batch} cap ${args.cap:.2f}")
    elif args.sub == "stage-start":
        for w in batches.stage_start(con, args.batch, args.stage, args.module_version, args.output, args.log):
            print(f"WARNING {w}", file=sys.stderr)
    elif args.sub == "mark":
        if args.file:
            rows = batches.read_marks(Path(args.file))
        elif args.case and args.status:
            rows = [(args.case, args.status, args.reason)]
        else:
            raise ReinsError("mark needs --file TSV or OACHARGEID STATUS [REASON]")
        print(f"{batches.mark(con, args.batch, args.stage, rows)} events recorded")
    elif args.sub == "stage-end":
        led = batches.stage_end(con, args.batch, args.stage)
        print(f"{args.stage} done: {led['done']} done, {led['skipped']} skipped, {led['failed']} failed")
    elif args.sub == "skip-stage":
        batches.skip_stage(con, args.batch, args.stage, args.why)
    elif args.sub in ("pause", "resume", "fail"):
        batches.set_status(con, args.batch, {"pause": "paused", "resume": "running", "fail": "failed"}[args.sub], args.why)
    elif args.sub == "close":
        batches.close(con, args.batch); print(f"{args.batch} closed")
    elif args.sub == "status":
        st = batches.status(con, args.batch)
        print(json.dumps(st, ensure_ascii=False, indent=1)) if args.json else _print_status(st)
    elif args.sub == "list":
        q = "SELECT * FROM batch" + (" WHERE status NOT IN ('closed', 'failed', 'done')" if args.live else "") + " ORDER BY created DESC"
        for r in con.execute(q):
            print(f"{r['batch_id']:<50} {r['status']:<8} {r['n_cases']:>6}  {r['purpose']}")
    return 0


def _case(con, args) -> int:
    members = [r[0] for r in con.execute("SELECT batch_id FROM batch_case WHERE oachargeid=? ORDER BY batch_id", (args.case_id,))]
    if not members:
        print(f"{args.case_id}: in no registered batch"); return 1
    print(f"{args.case_id}: in {len(members)} batches: {', '.join(members)}")
    for e in batches.case_history(con, args.case_id):
        print(f"  {e['at']}  {e['batch_id']}  {e['stage']:<18} {e['status']:<8} {e['reason'] or ''}  {e['module_version'] or ''}")
    return 0
