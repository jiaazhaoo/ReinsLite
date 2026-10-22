"""Recorded paths survive a folder reorganisation: the registry keeps where a batch ran; reading goes through the
machine's move logs. Regression for 2026-10-22: after /data was reorganised (858 moves) 103 of 120 batch work dirs
were gone under their recorded names and the production page showed 0 results everywhere."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from reins import paths
from reins.store import connect, tx


class Moves(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("REINS_HOME", "REINS_SESSION")}
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-mv-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_SESSION"] = "owner"
        (self.tmp / "home").mkdir()
        d = self.tmp / "data"
        (d / "councils" / "town" / "runs" / "town-wp3-production-20261007-1" / "lab").mkdir(parents=True)
        moves = self.tmp / "moves.tsv"
        moves.write_text("when\told\tnew\tsymlink_left\twhy\n"
                         f"t1\t{d}/town/wp3-e2e\t{d}/town/runs/town-wp3-production-20261007-1\tno\tmorning\n"
                         f"t2\t{d}/town\tcouncils/town\tyes\tevening, a link-style relative target\n")
        (self.tmp / "home" / "config.toml").write_text(f'[paths]\nmoves = ["{moves}"]\n')
        self.d = d
        self.con = connect(self.tmp / "home")

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_resolve_replays_moves_in_order(self):
        old = f"{self.d}/town/wp3-e2e/lab"
        self.assertEqual(paths.resolve(old), f"{self.d}/councils/town/runs/town-wp3-production-20261007-1/lab")
        self.assertEqual(paths.resolve(f"{self.d}/nowhere/x"), f"{self.d}/nowhere/x")          # unknown: as recorded
        here = str(self.d / "councils")
        self.assertEqual(paths.resolve(here), here)                                               # existing: untouched

    def test_check_names_what_is_lost(self):
        with tx(self.con):
            for seq, bid, wd in ((1, "a", f"{self.d}/town/wp3-e2e"), (2, "b", f"{self.d}/town/gone-e2e")):
                self.con.execute("INSERT INTO batch (batch_id, project, scope, type, day, seq, purpose, case_set_path, case_set_sha,"
                                 " n_cases, status, work_dir, created) VALUES (?, 'p', 'x', 'experiment', '20261007', ?, 'p', '', '',"
                                 " 0, 'paused', ?, '2026-10-07T00:00:00')", (bid, seq, wd))
        lost = paths.check(self.con)
        self.assertEqual([x["what"] for x in lost], ["batch b"])
        b = self.con.execute("SELECT * FROM batch WHERE batch_id='a'").fetchone()
        self.assertTrue(paths.work_dir(b).endswith("councils/town/runs/town-wp3-production-20261007-1"))
        self.assertEqual(b["work_dir"], f"{self.d}/town/wp3-e2e")                                # history kept as recorded


if __name__ == "__main__":
    unittest.main()
