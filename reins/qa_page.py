"""The one QA review page (user 2026-11-03: every page a person uses to mark cases right / wrong is this template, only this).

    reins page build BATCH --cards cards.jsonl --name NAME [--title '...'] [--reviewer NAME]
    reins page marks BATCH NAME [--all] [--json]     the person's marks (default: only Q and E)
    reins page list [BATCH]

A session never writes review HTML itself. It writes one JSON line per case and reins builds the page:

    {"case": "1066658", "tags": ["WP2", "direct pass"], "meta": "address · proposal", "why": "why it passed",
     "images": [{"path": "/abs/os_close.png", "caption": "OS map, red = delivered polygon"},
                {"path": "/abs/os_wide.png", "caption": "wider OS map"}, {"path": "/abs/plan.png", "caption": "plan"}],
     "table": {"head": ["judge", "verdict", "reason"], "rows": [["model A", "correct", "..."]]},
     "links": [{"href": "https://...", "text": "scans"}], "group": "stratum"}

Only `case` is required; it must be a case of BATCH. Images are copied (shrunk to 1600 px JPEG) into
WORK_DIR/pages/NAME/img/, so the page stays small and keeps working when the sources move.

The page is served by the board at /pages/BATCH/NAME/ . Its HTML is the template in page_ui/qa.html, read at every
request: improving the template improves every page, old ones too. Each mark (grade + code + note) is POSTed to the
board and recorded at once as a signed review_event (payload: page, grade, code), the same records `reins review`
and the improvement loop read; the browser also keeps a copy and re-sends anything the board did not confirm.

Grades and codes come from the project ([page] in its reins.toml), so the framework names no project words:

    [page]
    grades = [{key = "P", verdict = "correct"}, {key = "Q", verdict = "unsure"}, {key = "E", verdict = "wrong"}]
    codes = [{code = "WL", label = "WL wrong location", error_type = "location"}, ...]
    reviewer = "Jia"

Without it: P / Q / E and the review.toml error types.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from . import batches, review
from .store import ReinsError, now, tx

SCHEMA = """
CREATE TABLE IF NOT EXISTS qa_page (
  batch_id  TEXT NOT NULL,
  name      TEXT NOT NULL,
  dir       TEXT NOT NULL,
  title     TEXT,
  n_cards   INTEGER,
  reviewer  TEXT,
  created   TEXT NOT NULL,
  session   TEXT,
  PRIMARY KEY (batch_id, name)
);
"""
UI = Path(__file__).with_name("page_ui")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")
DEFAULT_GRADES = [{"key": "P", "verdict": "correct", "label": "P"}, {"key": "Q", "verdict": "unsure", "label": "Q"},
                  {"key": "E", "verdict": "wrong", "label": "E"}]


def ensure(con) -> None:
    con.executescript(SCHEMA)


def _project(con, batch: str) -> tuple[Path | None, dict]:
    from . import projects
    proj = batches.get(con, batch)["project"]
    for root, c in projects.all_():
        if c.get("project") == proj:
            return Path(root), c
    return None, {}


def settings(con, batch: str) -> dict:
    """Grades, codes, reviewer and taxonomy for this batch's project."""
    root, cfg = _project(con, batch)
    tax = review.taxonomy(root / "review.toml" if root else None)
    pg = cfg.get("page") or {}
    grades = [{"label": g.get("label", g["key"]), **g} for g in (pg.get("grades") or DEFAULT_GRADES)]
    codes = pg.get("codes") or [{"code": t, "label": t, "error_type": t} for t in tax["error_types"]]
    return {"grades": grades, "codes": codes, "reviewer": pg.get("reviewer", ""), "taxonomy": tax}


def _shrink(src: Path, dst: Path, px: int = 1600) -> bool:
    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None
        with Image.open(src) as im:
            if im.mode in ("RGBA", "LA", "P"):                  # transparent map tiles: on white, not black
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.getchannel("A"))
                im = bg
            im = im.convert("RGB")
            im.thumbnail((px, px))
            im.save(dst, "JPEG", quality=82)
        return True
    except ImportError:
        shutil.copyfile(src, dst.with_suffix(src.suffix))
        return True
    except Exception:                                                           # noqa: BLE001
        return False


def build(con, batch: str, cards_file: Path, name: str, title: str = "", reviewer: str = "", session: str = "") -> dict:
    ensure(con)
    if not NAME.match(name):
        raise ReinsError(f"page name {name!r}: letters, digits, _ . - only")
    b = batches.get(con, batch)
    if not b["work_dir"]:
        raise ReinsError(f"{batch} has no work dir")
    members = {r[0] for r in con.execute("SELECT case_id FROM batch_case WHERE batch_id=?", (batch,))}
    cards, seen, bad = [], set(), []
    for i, line in enumerate(Path(cards_file).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        c = json.loads(line)
        cid = str(c.get("case") or "").strip()
        if cid not in members:
            bad.append(f"line {i}: case {cid!r} is not in {batch}")
        elif cid in seen:
            bad.append(f"line {i}: case {cid} twice")
        seen.add(cid)
        cards.append({**c, "case": cid})
    if bad:
        raise ReinsError(f"{len(bad)} problems, no page built:\n  " + "\n  ".join(bad[:20]))
    if not cards:
        raise ReinsError("no cards")
    out = Path(b["work_dir"]) / "pages" / name
    img = out / "img"
    img.mkdir(parents=True, exist_ok=True)
    missing = 0
    for c in cards:
        ims = []
        for k, im in enumerate(c.get("images") or []):
            p = str(im.get("path") or im.get("src") or "")
            if p.startswith(("http://", "https://")):
                ims.append({"src": p, "caption": im.get("caption", "")})
                continue
            dst = img / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', c['case'])}_{k}.jpg"
            if Path(p).is_file() and _shrink(Path(p), dst):
                ims.append({"src": f"img/{dst.name}", "caption": im.get("caption", "")})
            else:
                missing += 1
                ims.append({"src": "", "caption": (im.get("caption", "") + " (image missing)").strip()})
        c["images"] = ims
        c.pop("path", None)
    st = settings(con, batch)
    meta = {"batch": batch, "name": name, "title": title or name, "built": now(),
            "reviewer": reviewer or st["reviewer"], "grades": st["grades"], "codes": st["codes"]}
    (out / "cards.json").write_text(json.dumps({"meta": meta, "cards": cards}, ensure_ascii=False), encoding="utf-8")
    with tx(con):
        con.execute("INSERT INTO qa_page (batch_id, name, dir, title, n_cards, reviewer, created, session)"
                    " VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(batch_id, name) DO UPDATE SET dir=excluded.dir,"
                    " title=excluded.title, n_cards=excluded.n_cards, reviewer=excluded.reviewer, created=excluded.created,"
                    " session=excluded.session", (batch, name, str(out), meta["title"], len(cards), meta["reviewer"], now(), session))
    return {"dir": str(out), "cards": len(cards), "missing_images": missing, "url": f"/pages/{batch}/{name}/"}


def page(con, batch: str, name: str) -> dict | None:
    ensure(con)
    r = con.execute("SELECT * FROM qa_page WHERE batch_id=? AND name=?", (batch, name)).fetchone()
    return dict(r) if r else None


def list_(con, batch: str | None = None) -> list[dict]:
    ensure(con)
    q = "SELECT * FROM qa_page" + (" WHERE batch_id=?" if batch else "") + " ORDER BY created DESC"
    return [dict(r) for r in con.execute(q, (batch,) if batch else ())]


def mark(con, batch: str, name: str, ev: dict) -> dict:
    """One mark from the page -> one signed review_event. ev: case, grade, code, note, reviewer."""
    if not page(con, batch, name):
        raise ReinsError(f"no page {name} for {batch}")
    st = settings(con, batch)
    g = next((x for x in st["grades"] if x["key"] == ev.get("grade")), None)
    if not g:
        raise ReinsError(f"grade {ev.get('grade')!r}")
    who = (ev.get("reviewer") or "").strip()
    if not who:
        raise ReinsError("reviewer name is empty: type it at the top of the page")
    code = (ev.get("code") or "").strip()
    note = (ev.get("note") or "").strip()
    et = None
    if g["verdict"] == "wrong":
        c = next((x for x in st["codes"] if x["code"] == code), None)
        et = (c or {}).get("error_type") or (st["taxonomy"]["error_types"] or ["other"])[-1]
    if g["verdict"] == "unsure" and not note:
        note = g["key"]                                          # the grade itself; the person may add why
    review.record(con, batch, [{"case_id": str(ev["case"]), "reviewer": who, "action": "verdict", "verdict": g["verdict"],
                                "error_type": et, "note": note or None, "client": "qa_page",
                                "payload": {"page": name, "grade": g["key"], "code": code}}], st["taxonomy"])
    return {"ok": True, "at": now()}


def marks(con, batch: str, name: str) -> dict[str, dict]:
    """Latest mark per case made on this page."""
    out = {}
    for r in con.execute("SELECT * FROM review_event WHERE batch_id=? AND action='verdict' AND client='qa_page'"
                         " AND json_extract(payload, '$.page')=? ORDER BY id", (batch, name)):
        p = json.loads(r["payload"] or "{}")
        out[r["case_id"]] = {"grade": p.get("grade"), "code": p.get("code"), "note": r["note"] if r["note"] != p.get("grade") else "",
                             "verdict": r["verdict"], "reviewer": r["reviewer"], "at": r["at"]}
    return out


def text(con, batch: str, name: str, all_: bool = False) -> str:
    p = page(con, batch, name)
    if not p:
        raise ReinsError(f"no page {name} for {batch}")
    m = marks(con, batch, name)
    by = {}
    for c, x in m.items():
        by.setdefault(x["grade"], []).append(c)
    head = f"{name}: {len(m)}/{p['n_cards']} marked · " + " · ".join(f"{k} {len(v)}" for k, v in sorted(by.items()))
    rows = [head]
    for c, x in sorted(m.items(), key=lambda kv: kv[1]["at"]):
        if all_ or x["verdict"] != "correct":
            rows.append(f"  {x['grade']}  {c:<14} {x['code'] or '':<6} {x['note'] or ''}  ({x['reviewer']} {x['at'][:16]})")
    return "\n".join(rows)
