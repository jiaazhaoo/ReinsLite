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
  bench     init | add-stage | add-input | label | freeze | bump | verify | holdout | list     C4
  threshold set | list                                                    C4 (every threshold carries its n)
  env       snapshot | check | list                                       env-<name>-vN manifests
  review    assign | verdict | import | calibration | candidates | taxonomy      C6
  accept    sample | grade | check | decide | show                        C11
  preflight BATCH                                                         input health check
  deliver   check | register                                              C12 contract check + versioned copy
  rules     freeze                                                        ruleset-<project>-vN

Project settings come from the nearest reins.toml (project, case_id_pattern, [dev], [modules]).
"""
from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

from . import (accept, batches, bench, decisions, deliver, dev, envs, gate, glossary, leases, modules, names, notify,
               preflight, review, rules, runner, spend)
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
    x = m.add_parser("describe", help="set the one-line description of a module or a module version")
    x.add_argument("name", help="module name or module version"); x.add_argument("--about", required=True)
    x = m.add_parser("status"); x.add_argument("module")
    m.add_parser("list")

    d = sub.add_parser("dev").add_subparsers(dest="sub", required=True)
    x = d.add_parser("start"); x.add_argument("module"); x.add_argument("suffix"); x.add_argument("--about", required=True)
    x.add_argument("--from-batch"); x.add_argument("--cases", help="comma-separated oachargeids: the pilot set")
    x.add_argument("--files", help="space-separated globs this version touches"); x.add_argument("--repo")
    x = d.add_parser("finish"); x.add_argument("version"); x.add_argument("--skip-tier"); x.add_argument("--why")
    x = d.add_parser("abandon"); x.add_argument("version"); x.add_argument("--why", required=True)
    x = d.add_parser("release"); x.add_argument("--note", default=""); x.add_argument("--repo")
    x = d.add_parser("adopt-release"); x.add_argument("tag"); x.add_argument("--note", default=""); x.add_argument("--repo")
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
    x.add_argument("--ruleset"); x.add_argument("--env"); x.add_argument("--config", action="append", type=Path)
    x.add_argument("--input", action="append", type=Path, help="mapping tables and other inputs to hash into provenance")
    x = b.add_parser("adopt", help="bring a batch that already exists on disk under reins")
    for a in ("--council", "--wp", "--purpose", "--cases", "--work-dir"):
        x.add_argument(a, required=True)
    x.add_argument("--type", required=True, choices=names.BATCH_TYPES)
    x.add_argument("--stage", action="append", required=True, help="NAME:planned|running|done|skipped, in order")
    x.add_argument("--release"); x.add_argument("--owner", help="session id that owns it"); x.add_argument("--alias", action="append")
    x.add_argument("--project"); x.add_argument("--note", default="")
    x = b.add_parser("probe", help="set the command that prints this batch's progress (board shows it)")
    x.add_argument("batch"); x.add_argument("--cmd", required=True)
    x = b.add_parser("skip-cases", help="mark cases that never enter a stage as skipped")
    x.add_argument("batch"); x.add_argument("stage"); x.add_argument("--file", required=True, type=Path); x.add_argument("--why", required=True)
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

    x = sub.add_parser("run"); x.add_argument("batch"); x.add_argument("stage", nargs="?", default=None)
    x.add_argument("--self-staged", action="store_true", help="the command reports its own stage boundaries")
    x.add_argument("--module-version")
    x.add_argument("--cwd"); x.add_argument("--retries", type=int); x.add_argument("command", nargs=argparse.REMAINDER)
    x = sub.add_parser("ctl"); x.add_argument("verb", choices=["pause", "resume", "stop"]); x.add_argument("batch")
    x.add_argument("--why", default="")
    x = sub.add_parser("case"); x.add_argument("case_id"); x.add_argument("--provenance", action="store_true")

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
    x = r.add_parser("freeze"); x.add_argument("path", type=Path); x.add_argument("--project")
    x.add_argument("--old-lanes", type=Path); x.add_argument("--new-lanes", type=Path)

    bn = sub.add_parser("bench").add_subparsers(dest="sub", required=True)
    x = bn.add_parser("init"); x.add_argument("name"); x.add_argument("--cases", required=True, type=Path)
    x.add_argument("--about", required=True); x.add_argument("--holdout-fraction", type=float, default=0.2)
    x.add_argument("--seed", type=int, default=7); x.add_argument("--project")
    x = bn.add_parser("adopt"); x.add_argument("name"); x.add_argument("version", type=int); x.add_argument("--path", required=True, type=Path)
    x.add_argument("--cases", required=True, type=Path); x.add_argument("--about", default=""); x.add_argument("--project")
    x.add_argument("--holdout-fraction", type=float, default=0.0); x.add_argument("--seed", type=int, default=7)
    x = bn.add_parser("add-stage"); x.add_argument("name"); x.add_argument("stage"); x.add_argument("--from", dest="src", required=True, type=Path)
    x.add_argument("--module-version", required=True)
    x = bn.add_parser("add-input"); x.add_argument("name"); x.add_argument("--from", dest="src", required=True, type=Path)
    x = bn.add_parser("label"); x.add_argument("name"); x.add_argument("--file", required=True, type=Path)
    x = bn.add_parser("freeze"); x.add_argument("name"); x.add_argument("--changelog", required=True)
    x = bn.add_parser("bump"); x.add_argument("name"); x.add_argument("--changelog", required=True)
    x = bn.add_parser("verify"); x.add_argument("name"); x.add_argument("version", nargs="?", type=int)
    x = bn.add_parser("holdout"); x.add_argument("name"); x.add_argument("--why", required=True); x.add_argument("--tuned", action="store_true")
    bn.add_parser("list")
    th = sub.add_parser("threshold").add_subparsers(dest="sub", required=True)
    x = th.add_parser("set"); x.add_argument("module"); x.add_argument("name"); x.add_argument("value")
    x.add_argument("--n", type=int, required=True); x.add_argument("--split", default="dev"); x.add_argument("--benchmark")
    x.add_argument("--by", default="user"); x.add_argument("--note", default="")
    x = th.add_parser("list"); x.add_argument("module", nargs="?")
    en = sub.add_parser("env").add_subparsers(dest="sub", required=True)
    for verb in ("snapshot", "check"):
        x = en.add_parser(verb); x.add_argument("name"); x.add_argument("--python", required=True)
        x.add_argument("--asset", action="append")
    en.add_parser("list")
    rv = sub.add_parser("review").add_subparsers(dest="sub", required=True)
    x = rv.add_parser("assign"); x.add_argument("batch"); x.add_argument("--reviewer", required=True)
    x.add_argument("--cases", required=True); x.add_argument("--queue"); x.add_argument("--round"); x.add_argument("--by", default="user")
    x = rv.add_parser("verdict"); x.add_argument("batch"); x.add_argument("case"); x.add_argument("verdict", choices=review.VERDICTS)
    x.add_argument("--reviewer", required=True); x.add_argument("--error-type"); x.add_argument("--note"); x.add_argument("--target", default="case")
    x = rv.add_parser("import"); x.add_argument("batch"); x.add_argument("--file", required=True, type=Path)
    x = rv.add_parser("calibration"); x.add_argument("batch")
    x = rv.add_parser("candidates"); x.add_argument("batch"); x.add_argument("--out", type=Path)
    x = rv.add_parser("taxonomy"); x.add_argument("path", nargs="?", type=Path)
    ac = sub.add_parser("accept").add_subparsers(dest="sub", required=True)
    x = ac.add_parser("sample"); x.add_argument("batch"); x.add_argument("--lanes", required=True, type=Path)
    x.add_argument("--n", type=int, default=315); x.add_argument("--seed", type=int, default=20261007)
    x.add_argument("--from-lane", default="auto_accept"); x.add_argument("--ideal", type=int, default=3); x.add_argument("--max", type=int, default=6)
    x = ac.add_parser("grade"); x.add_argument("batch"); x.add_argument("--file", required=True, type=Path)
    x = ac.add_parser("check"); x.add_argument("batch"); x.add_argument("--lanes", required=True, type=Path)
    x = ac.add_parser("decide"); x.add_argument("batch"); x.add_argument("--by", default="user"); x.add_argument("--note", default="")
    x.add_argument("--lanes", type=Path)
    x = ac.add_parser("show"); x.add_argument("batch")
    x = sub.add_parser("preflight"); x.add_argument("batch"); x.add_argument("--project-root", type=Path)
    dl = sub.add_parser("deliver").add_subparsers(dest="sub", required=True)
    for verb in ("check", "register"):
        x = dl.add_parser(verb); x.add_argument("file", type=Path); x.add_argument("--batch", required=True)
        x.add_argument("--key", default="oachargeid"); x.add_argument("--sheets"); x.add_argument("--required")
        x.add_argument("--allow-missing", type=int, default=0)
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
            print(f"{len(hits)} glossary violations (glossary v{glossary.version()})")
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
        if args.cmd == "rules" and args.sub != "freeze":
            if args.sub == "list":
                p = Path(args.path) if args.path else (project_root(cwd) or cwd) / "rules.toml"
                print(rules.describe(rules.load(p))); return 0
            d = rules.diff(args.old, args.new, args.key, args.lane)
            print(json.dumps(d, ensure_ascii=False, indent=1) if args.json else rules.format_diff(d)); return 0
        if args.cmd == "review" and args.sub == "taxonomy":
            print(json.dumps(review.taxonomy(args.path or (project_root(cwd) or cwd) / "review.toml"), indent=1)); return 0

        con = connect()
        if args.cmd == "rules":
            r = rules.freeze(con, args.path, _project(args, cfg), args.old_lanes, args.new_lanes)
            print(f"{r['name']}" + ("" if r["new"] else "  (unchanged rules: same version)")
                  + (f"\n  lane diff: {r['diff_summary']}  -> {r['diff_path']}" if r.get("diff_summary") else ""))
            return 0
        if args.cmd in ("bench", "threshold", "env", "review", "accept", "preflight", "deliver"):
            return _measure(con, args, cfg, cwd)
        if args.cmd == "module":
            return _module(con, args, cfg)
        if args.cmd == "dev":
            return _dev(con, args, cfg, cwd)
        if args.cmd == "batch":
            return _batch(con, args, cfg)
        if args.cmd == "run":
            cmd = args.command[1:] if args.command and args.command[0] == "--" else args.command
            if bool(args.self_staged) == bool(args.stage and args.stage != "--"):
                raise ReinsError("give either a STAGE or --self-staged")
            res = runner.start(con, args.batch, runner.SELF if args.self_staged else args.stage, cmd, Path(args.cwd or cwd),
                               args.module_version, args.retries)
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
    elif args.sub == "describe":
        if names.MODULE_VERSION.match(args.name):
            old = modules.describe(con, args.name, args.about)
        else:
            old = modules.describe_module(con, args.name, args.about)
        print(f"{args.name}: {args.about}\n  (was: {old})")
    elif args.sub == "status":
        st = modules.status(con, args.module)
        p = st["production"]
        print(f"{args.module}: production = {p['version'] if p else 'none released'}")
        for c in st["candidates"]:
            print(f"  candidate {c['version']}  {c['branch'] or '-'}  session={c['session'] or '-'}  {c['about']}")
    elif args.sub == "list":
        for r in con.execute("SELECT v.version, v.status, m.project, v.about FROM module_version v"
                             " JOIN module m ON m.name = v.module ORDER BY v.module, v.day, v.seq"):
            print(f"{r['version']:<34} {r['status']:<10} {r['about']}")
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
    elif args.sub == "adopt-release":
        r = dev.adopt_release(con, repo, args.tag, args.note); print(f"{r['name']} ({r['commit']}) -> {r['worktree']}")
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
                            work_dir=args.work_dir, spend_cap=args.cap, ruleset=args.ruleset, env_name=args.env,
                            config_files=args.config, input_files=args.input)
        probe = cfg.get("run", {}).get("probe")
        if probe:
            batches.set_probe(con, bid, probe)
        print(bid)
    elif args.sub == "adopt":
        st = []
        for s in args.stage:
            n, _, state = s.partition(":")
            if state not in ("planned", "running", "done", "skipped"):
                raise ReinsError(f"--stage {s!r}: NAME:planned|running|done|skipped")
            st.append((n, state))
        bid = batches.adopt(con, project=_project(args, cfg), council=args.council, wp=args.wp, type_=args.type,
                            purpose=args.purpose, case_file=Path(args.cases), work_dir=args.work_dir, stages=st,
                            release_name=args.release, owner=args.owner, aliases=args.alias,
                            case_pattern=cfg.get("case_id_pattern", names.DEFAULT_CASE_PATTERN), note=args.note)
        print(bid)
    elif args.sub == "probe":
        batches.set_probe(con, args.batch, args.cmd); print("probe set")
    elif args.sub == "skip-cases":
        n = batches.skip_cases(con, args.batch, args.stage, names.read_case_set(args.file), args.why); print(f"{n} cases skipped")
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
    if args.provenance:
        for m in members:
            print(json.dumps(batches.provenance(con, m), ensure_ascii=False, indent=1))
        return 0
    for e in batches.case_history(con, args.case_id):
        print(f"  {e['at']}  {e['batch_id']}  {e['stage']:<18} {e['status']:<8} {e['reason'] or ''}  {e['module_version'] or ''}")
    return 0


def _measure(con, args, cfg, cwd) -> int:
    if args.cmd == "bench":
        if args.sub == "init":
            r = bench.init(con, args.name, _project(args, cfg), args.cases, args.about, args.holdout_fraction, args.seed,
                           cfg.get("case_id_pattern", names.DEFAULT_CASE_PATTERN))
            print(f"{r['name']} v1 at {r['path']}  dev={r['n_dev']} holdout={r['n_holdout']}")
        elif args.sub == "adopt":
            r = bench.adopt(con, args.name, args.version, _project(args, cfg), args.path, args.cases, args.holdout_fraction, args.seed, args.about)
            print(f"{r['name']} v{r['version']} adopted (frozen): {r['files']} files, {r['n_cases']} cases")
        elif args.sub == "add-stage":
            print(f"{bench.add_stage(con, args.name, args.stage, args.src, args.module_version)} files added")
        elif args.sub == "add-input":
            print(f"{bench.add_input(con, args.name, args.src)} files added")
        elif args.sub == "label":
            r = bench.label(con, args.name, args.file); print(f"labelset {r['labelset']}: +{r['added']} {r['by_source']}")
        elif args.sub == "freeze":
            r = bench.freeze(con, args.name, args.changelog); print(f"{r['name']} v{r['version']} frozen: {r['files']} files, manifest {r['manifest_sha'][:12]}")
        elif args.sub == "bump":
            r = bench.bump(con, args.name, args.changelog); print(f"{r['name']} v{r['version']} open at {r['path']}")
        elif args.sub == "verify":
            r = bench.verify(con, args.name, args.version)
            print("ok" if r["ok"] else f"MISMATCH changed={r['changed'][:5]} missing={r['missing'][:5]} extra={r['extra'][:5]}")
            return 0 if r["ok"] else 1
        elif args.sub == "holdout":
            bench.holdout(con, args.name, args.why, args.tuned); print("recorded" + (" (holdout now compromised)" if args.tuned else ""))
        else:
            for b in bench.list_(con):
                print(f"{b['name']:<32} v{b['version']:<3} {b['status']:<7} cases={b['n_cases']} dev={b['n_dev']} holdout={b['n_holdout']}"
                      f" labelset={b['labelset']}{'  HOLDOUT COMPROMISED' if b['holdout_compromised'] else ''}  {b['path']}")
    elif args.cmd == "threshold":
        if args.sub == "set":
            r = bench.set_threshold(con, args.module, args.name, args.value, args.n, args.split, args.benchmark, args.by, args.note)
            print(f"{r['module']}.{r['name']} = {r['value']} (n={r['n']}{', LOW N: re-validate on the next council' if r['low_n'] else ''})")
        else:
            for t in bench.thresholds(con, args.module):
                print(f"{t['module']}.{t['name']:<28} = {t['value']:<10} n={t['n']:<5} {t['split']:<7} {t['benchmark'] or '-':<30}"
                      f"{' LOW_N' if t['low_n'] else ''}  {t['at'][:10]}")
    elif args.cmd == "env":
        if args.sub == "snapshot":
            r = envs.snapshot(con, args.name, args.python, args.asset)
            print(f"{r['name']}  ({'new' if r['new'] else 'unchanged'}; {r['packages']} packages, {r['assets']} assets)")
        elif args.sub == "check":
            r = envs.check(con, args.name, args.python, args.asset)
            print(f"against {r['against']}: " + ("same" if r["same"] else json.dumps(r["diff"], indent=1)[:2000]))
            return 0 if r["same"] else 1
        else:
            for e in envs.list_(con):
                print(f"{e['name']:<30} {e['sha'][:12]}  {e['created']}")
    elif args.cmd == "review":
        tax = review.taxonomy((project_root(cwd) or cwd) / "review.toml")
        if args.sub == "assign":
            n = review.assign(con, args.batch, args.reviewer, [c for c in args.cases.split(",") if c], args.queue, args.round, args.by)
            print(f"{n} cases appended to {args.reviewer}'s list")
        elif args.sub == "verdict":
            review.record(con, args.batch, [{"oachargeid": args.case, "target": args.target, "reviewer": args.reviewer,
                                             "action": "verdict", "verdict": args.verdict, "error_type": args.error_type,
                                             "note": args.note}], tax)
            print("recorded")
        elif args.sub == "import":
            print(f"{review.record(con, args.batch, review.read_events(args.file), tax)} events recorded")
        elif args.sub == "calibration":
            print(json.dumps(review.calibration(con, args.batch), ensure_ascii=False, indent=1))
        elif args.sub == "candidates":
            c = review.candidates(con, args.batch)
            text = "\n".join(json.dumps(x, ensure_ascii=False) for x in c) + ("\n" if c else "")
            if args.out:
                args.out.write_text(text); print(f"{len(c)} candidate labels -> {args.out} (reins bench label after a person confirms)")
            else:
                print(text, end="")
    elif args.cmd == "accept":
        if args.sub == "sample":
            r = accept.sample(con, args.batch, args.lanes, args.n, args.seed, args.from_lane, args.ideal, args.max)
            print(f"acceptance #{r['acceptance_id']}: {r['n']} of {r['pool']} {args.from_lane} cases, seed {r['seed']} {r['note']}")
        elif args.sub == "grade":
            print(f"{accept.grade(con, args.batch, accept.read_grades(args.file))} grades recorded")
        elif args.sub == "check":
            r = accept.check(con, args.batch, args.lanes); print(f"{len(r['stale'])} of {r['n']} sampled cases changed lane: {r['stale'][:10]}")
            return 1 if r["stale"] else 0
        elif args.sub == "decide":
            r = accept.decide(con, args.batch, args.by, args.note, args.lanes)
            print(f"{r['decision'].upper()}  F={r['F']} A={r['A']} P={r['P']} of {r['graded']} (ideal {r['budget_ideal']}, max {r['budget_max']})")
            print(f"  registry line: {r['registry_line']}")
        else:
            print(json.dumps(accept.show(con, args.batch), ensure_ascii=False, indent=1))
    elif args.cmd == "preflight":
        r = preflight.run(con, args.batch, args.project_root or project_root(cwd))
        for c in r["checks"]:
            print(f"  {'ok  ' if c['ok'] else 'FAIL'} {c['name']:<24} {c['detail']}")
        print("preflight PASS" if r["ok"] else "preflight FAIL"); return 0 if r["ok"] else 1
    elif args.cmd == "deliver":
        kw = dict(key=args.key, sheets_expected=args.sheets.split(",") if args.sheets else None,
                  required=args.required.split(",") if args.required else None, allow_missing=args.allow_missing)
        if args.sub == "check":
            r = deliver.check(con, args.file, args.batch, **kw)
            for p in r["problems"]:
                print(f"  {p}")
            print(f"{'ok' if r['ok'] else str(len(r['problems'])) + ' problems'}: {r['n_rows']} rows, sheets {r['sheets']}, digest {r['data_digest'][:12]}")
            return 0 if r["ok"] else 1
        r = deliver.register(con, args.file, args.batch, **kw)
        print(f"v{r['version']} -> {r['path']}  (read-only; manifest alongside)")
    return 0
