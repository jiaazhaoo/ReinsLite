"""reins: the command line over the registry.

    reins module add NAME --project P --about '...'
    reins module new MODULE SUFFIX --about '...' [--pin key=value ...]     -> MODULE-SUFFIX-YYYYMMDD-n (candidate)
    reins module release VERSION --gate '...' | retire VERSION --why '...' | note VERSION EVENT '...'
    reins module status MODULE | list

    reins batch open --council C --wp W --type T --purpose '...' --cases FILE --stage NAME[=MODULE_VERSION] ...
                     [--parent BATCH] [--release R] [--alias '第一批'] [--project P]
    reins batch stage-start BATCH STAGE [--module-version V] [--output PATH] [--log PATH]
    reins batch mark BATCH STAGE (--file TSV | OACHARGEID STATUS [REASON])
    reins batch stage-end BATCH STAGE | skip-stage BATCH STAGE --why '...'
    reins batch pause|resume|fail BATCH --why '...' | close BATCH
    reins batch status BATCH | list [--live]

    reins case CASE_ID                                                       every batch and stage this case went through
    reins lint PATH ...                                                      table headers and docs against the glossary

Project settings come from the nearest reins.toml (project = "...", case_id_pattern = "..."); --project overrides.
"""
from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

from . import batches, glossary, modules, names
from .store import ReinsError, connect


def project_config(cwd: Path) -> dict:
    for d in (cwd, *cwd.parents):
        f = d / "reins.toml"
        if f.is_file():
            return tomllib.loads(f.read_text(encoding="utf-8"))
    return {}


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


def _stages(items) -> list[tuple[str, str | None]]:
    out = []
    for it in items:
        s, _, mv = it.partition("=")
        out.append((s, mv or None))
    return out


def _print_status(st: dict) -> None:
    b = st["batch"]
    flags = " MIXED_VERSION" if b["mixed_version"] else ""
    print(f"{b['batch_id']}  [{b['status']}]{flags}  {b['n_cases']} cases  release={b['release_name'] or '-'}")
    print(f"  purpose: {b['purpose']}")
    if b["aliases"]:
        print(f"  also called: {', '.join(b['aliases'])}")
    if b["parent"]:
        print(f"  parent: {b['parent']}")
    for s in st["stages"]:
        acc = s["done"] + s["skipped"] + s["failed"]
        print(f"  {s['ord']:>2} {s['stage']:<20} {s['status']:<8} {acc:>6}/{b['n_cases']:<6} "
              f"done={s['done']} skipped={s['skipped']} failed={s['failed']}  {s['module_version'] or ''}")
    for e in st["recent_events"]:
        print(f"     {e['at']}  {e['event']}  {e['detail'] or ''}")


def main(argv: list[str] | None = None) -> int:
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

    b = sub.add_parser("batch").add_subparsers(dest="sub", required=True)
    x = b.add_parser("open")
    for a in ("--council", "--wp", "--purpose", "--cases"):
        x.add_argument(a, required=True)
    x.add_argument("--type", required=True, choices=names.BATCH_TYPES)
    x.add_argument("--stage", action="append", required=True, help="NAME or NAME=MODULE_VERSION, in run order")
    x.add_argument("--parent"); x.add_argument("--release"); x.add_argument("--alias", action="append")
    x.add_argument("--project")
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
    x = b.add_parser("list"); x.add_argument("--live", action="store_true", help="only batches not closed/failed")

    x = sub.add_parser("case"); x.add_argument("case_id")
    x = sub.add_parser("lint"); x.add_argument("paths", nargs="+", type=Path)

    args = ap.parse_args(argv)
    cfg = project_config(Path.cwd())
    try:
        if args.cmd == "lint":
            hits = glossary.lint(args.paths)
            for h in hits:
                print(f"{h.where}: {h.word!r} -> {h.hint}")
            print(f"{len(hits)} glossary violations")
            return 1 if hits else 0

        con = connect()
        if args.cmd == "module":
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
        elif args.cmd == "batch":
            if args.sub == "open":
                bid = batches.open_(con, project=_project(args, cfg), council=args.council, wp=args.wp,
                                    type_=args.type, purpose=args.purpose, case_file=Path(args.cases),
                                    stages=_stages(args.stage), parent=args.parent, release_name=args.release,
                                    aliases=args.alias,
                                    case_pattern=cfg.get("case_id_pattern", names.DEFAULT_CASE_PATTERN))
                print(bid)
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
                print(f"{batches.mark(con, args.batch, args.stage, rows)} outcomes recorded")
            elif args.sub == "stage-end":
                led = batches.stage_end(con, args.batch, args.stage)
                print(f"{args.stage} done: {led['done']} done, {led['skipped']} skipped, {led['failed']} failed")
            elif args.sub == "skip-stage":
                batches.skip_stage(con, args.batch, args.stage, args.why)
            elif args.sub in ("pause", "resume", "fail"):
                batches.set_status(con, args.batch, {"pause": "paused", "resume": "running", "fail": "failed"}[args.sub],
                                   args.why)
            elif args.sub == "close":
                batches.close(con, args.batch); print(f"{args.batch} closed")
            elif args.sub == "status":
                st = batches.status(con, args.batch)
                print(json.dumps(st, ensure_ascii=False, indent=1)) if args.json else _print_status(st)
            elif args.sub == "list":
                q = "SELECT * FROM batch" + (" WHERE status NOT IN ('closed', 'failed')" if args.live else "") \
                    + " ORDER BY created DESC"
                for r in con.execute(q):
                    print(f"{r['batch_id']:<50} {r['status']:<8} {r['n_cases']:>6}  {r['purpose']}")
        elif args.cmd == "case":
            members = [r[0] for r in con.execute("SELECT batch_id FROM batch_case WHERE oachargeid=? ORDER BY batch_id",
                                                 (args.case_id,))]
            if not members:
                print(f"{args.case_id}: in no registered batch")
                return 1
            print(f"{args.case_id}: in {len(members)} batches: {', '.join(members)}")
            for e in batches.case_history(con, args.case_id):
                print(f"  {e['at']}  {e['batch_id']}  {e['stage']:<18} {e['status']:<8} {e['reason'] or ''}"
                      f"  {e['module_version'] or ''}")
        return 0
    except ReinsError as e:
        print(f"reins: {e}", file=sys.stderr)
        return 2
