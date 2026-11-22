#!/usr/bin/env python3
"""READYMAP_NOTIFY for ReadyMap's data-host agent: a note on the board (and its notifications).

    python3 notify.py KEY LEVEL TITLE [BODY]

ReadyMap names no framework: it runs whatever command $READYMAP_NOTIFY holds (systemd units read it from
~/.config/readymap/host.env, runs get it from [run.env] beside this file)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from reins import notify  # noqa: E402
from reins.store import connect  # noqa: E402

if __name__ == "__main__":
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    key, level, title = sys.argv[1:4]
    body = sys.argv[4] if len(sys.argv) > 4 else ""
    notify.send(connect(ensure=False), key, level, title, body, cooldown_min=0)
