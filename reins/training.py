"""Training runs in the ledger: a model trained on this machine is a batch of type `training` (no cases, one stage
`train`), started by reins so it has a process record, resource claims, running time, spend if it calls anything paid,
and, when it ends well, its weights registered as weights-<name>-vN with the training record beside them.

    reins train start NAME --about '...' [--weights PATH] [--work-dir DIR] [--cwd DIR] -- CMD ...
    reins train list

Before this, YOLO runs were started by hand: the board saw "some process on the GPU" and nothing was kept
(2026-10-18, plot-seg v2clean / v2all, 120 epochs each).
"""
from __future__ import annotations

from pathlib import Path

from . import artifacts, batches, names, notify
from .store import ReinsError, now, tx

STAGE = "train"


def open_(con, project: str, name: str, about: str, weights: str | None = None, work_dir: str | None = None) -> str:
    names.check_scope(name)                               # the batch is <name>-training-<day>-<n>
    bid = batches.open_(con, project=project, scope=name, type_="training", purpose=about, case_file=None,
                        stages=[{"stage": STAGE, "module_version": None, "paid": 0, "spend_cap": None, "time_limit_s": None}],
                        work_dir=work_dir)
    if weights:
        with tx(con):
            con.execute("INSERT INTO batch_event (at, batch_id, event, detail) VALUES (?,?, 'weights_out', ?)",
                        (now(), bid, str(Path(weights).resolve())))
    return bid


def finished(con, batch: str) -> str | None:
    """Called when the train stage ended well: register the weights it said it would write. Returns the artifact name."""
    b = batches.get(con, batch)
    if b["type"] != "training":
        return None
    row = con.execute("SELECT detail FROM batch_event WHERE batch_id=? AND event='weights_out' ORDER BY id DESC LIMIT 1",
                      (batch,)).fetchone()
    if not row:
        return None
    path = Path(row[0])
    if not path.exists():
        notify.send(con, f"weights_missing:{batch}", "action", f"{batch}: training ended but {path} is not there",
                    "register the weights by hand: reins weights register ...", cooldown_min=0)
        return None
    spec = {"dir": str(path)} if path.is_dir() else {"file": str(path)}
    r = artifacts.register_weights(con, b["scope"].replace("-", "_"), spec, b["purpose"])
    with tx(con):
        con.execute("INSERT INTO batch_event (at, batch_id, event, detail) VALUES (?,?, 'weights_registered', ?)",
                    (now(), batch, f"{r['name']} ({'new' if r['new'] else 'same files as before'})"))
    notify.send(con, f"trained:{batch}", "info", f"{batch}: trained, weights {r['name']}", str(path), cooldown_min=0)
    return r["name"]


def list_(con) -> list[dict]:
    out = []
    for b in con.execute("SELECT * FROM batch WHERE type='training' ORDER BY created DESC"):
        w = con.execute("SELECT detail FROM batch_event WHERE batch_id=? AND event='weights_registered' ORDER BY id DESC LIMIT 1",
                        (b["batch_id"],)).fetchone()
        out.append({"batch_id": b["batch_id"], "status": b["status"], "about": b["purpose"], "created": b["created"],
                    "running_h": round(batches.elapsed_h(con, b["batch_id"]) or 0, 1), "weights": w[0] if w else None})
    return out
