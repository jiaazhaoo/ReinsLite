"""Operations leave a record too: a cap change is a decision with the old file kept and the comments untouched; a
training run is a batch reins started, with its weights registered when it ends well."""
from __future__ import annotations

import os
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path

from reins import batches, runner, settings_edit, training
from reins.store import ReinsError, connect


class Ops(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("REINS_HOME", "REINS_SESSION", "PYTHONPATH")}
        os.environ["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])     # the supervisor runs python -m reins.runner
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-ops-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_SESSION"] = "owner"
        self.con = connect(self.tmp / "home")
        (self.tmp / "home" / "config.toml").write_text(
            '# machine settings\nnotify_cmd = ""\n\n[pool]\ndaily_cap = 20.0      # USD a day\nweekly_cap = 60.0\n\n[resources]\nmax_fraction = 0.75\n')

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_config_set_keeps_comments_and_records(self):
        r = settings_edit.set_(self.con, "pool.daily_cap", "30", "two judge passes today")
        self.assertEqual((r["old"], r["new"]), (20.0, 30))
        text = (self.tmp / "home" / "config.toml").read_text()
        self.assertIn("daily_cap = 30      # USD a day", text)                   # the comment stays
        settings_edit.set_(self.con, "pool.hourly_cap", "8", "new key in an existing section")
        settings_edit.set_(self.con, "backup.dir", "/other/disk", "new section")
        settings_edit.set_(self.con, "disk_warn_pct", "80", "top level")
        cfg = tomllib.loads((self.tmp / "home" / "config.toml").read_text())
        self.assertEqual((cfg["pool"]["hourly_cap"], cfg["backup"]["dir"], cfg["disk_warn_pct"], cfg["resources"]["max_fraction"]),
                         (8, "/other/disk", 80, 0.75))
        self.assertNotIn("disk_warn_pct", (self.tmp / "home" / "config.toml").read_text().split("[pool]")[1])
        h = settings_edit.history(self.con)
        self.assertEqual(len(h), 4)
        self.assertIn("pool.daily_cap 20.0 -> 30", h[-1]["text"] if h[-1]["text"].endswith("30") else h[0]["text"] + h[-1]["text"])
        self.assertEqual(len(list((self.tmp / "home" / "config-history").glob("config-*.toml"))) >= 1, True)
        with self.assertRaises(ReinsError):
            settings_edit.set_(self.con, "pool.daily_cap", "40", "  ")                # a reason is required

    def test_training_run_registers_its_weights(self):
        out = self.tmp / "run" / "best.pt"
        b = training.open_(self.con, "p", "plot-seg", "segment plots on clean plans", weights=str(out))
        self.assertTrue(b.startswith("plot-seg-training-"))
        self.assertEqual(batches.get(self.con, b)["n_cases"], 0)
        cmd = [sys.executable, "-c", f"import pathlib; p=pathlib.Path({str(out)!r}); p.parent.mkdir(parents=True); p.write_bytes(b'w'*10)"]
        runner.start(self.con, b, training.STAGE, cmd, self.tmp, None, 0)
        for _ in range(200):
            if batches.get(self.con, b)["status"] == "done" and self.con.execute(
                    "SELECT 1 FROM batch_event WHERE batch_id=? AND event='weights_registered'", (b,)).fetchone():
                break
            time.sleep(0.05)
        self.assertEqual(batches.get(self.con, b)["status"], "done")
        rows = training.list_(self.con)
        self.assertTrue(rows[0]["weights"].startswith("weights-plot_seg-v1"))
        with self.assertRaises(ReinsError):
            batches.open_(self.con, project="p", scope="x-wp1", type_="experiment", purpose="no cases", case_file=None,
                          stages=[{"stage": "s", "module_version": None, "paid": 0, "spend_cap": None, "time_limit_s": None}])


if __name__ == "__main__":
    unittest.main()
