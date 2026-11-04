"""Claims learn from measured peaks; production goes first; a GPU claim is shared by shard processes (2026-11-04:
11 watchdog kills in two weeks, the user: 我不想再花时间管理这些)."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from reins import resources
from reins.store import tx

from test_resources import Base, rule


class Learn(Base):
    def test_declared_until_measured_then_peak_with_margin(self):
        r = {**rule("geo", mem=2, gpu=4, match="batch"), "split_env": None}
        self.assertEqual(resources.sized(self.con, r, None), (2, 4, 1))
        b = self.running()
        with tx(self.con):
            self.con.execute("INSERT INTO resource_peak (at, batch_id, rule, pid, mem_gb, gpu_gb, share) VALUES"
                             " ('2026-11-04T00:00:00', ?, 'geo', 1, 9.0, 5.0, 1)", (b,))
        mem, gpu, n = resources.sized(self.con, r, None)
        self.assertEqual((mem, gpu, n), (9.9, 5.5, 1))                     # measured x 1.1 beats the declaration

    def test_split_env_shares_the_gpu(self):
        r = {**rule("geo", gpu=12, match="batch"), "split_env": "E2E_GPU_PROCS"}
        with mock.patch.object(resources, "_env_of", return_value="3"):
            self.assertEqual(resources.sized(self.con, r, 42), (0, 4.0, 3))
        b = self.running()
        with tx(self.con):                                                  # a shard of 3 measured 4.5 GB: 13.5 for the rule
            self.con.execute("INSERT INTO resource_peak (at, batch_id, rule, pid, mem_gb, gpu_gb, share) VALUES"
                             " ('2026-11-04T00:00:00', ?, 'geo', 1, 1.0, 4.5, 3)", (b,))
        with mock.patch.object(resources, "_env_of", return_value="1"):
            self.assertEqual(resources.sized(self.con, r, 42)[1], round(13.5 * 1.1, 2))   # alone: the whole step

    def test_production_goes_before_a_waiting_experiment(self):
        hog, exp, prod = self.running(), self.running(), self.running()
        self.con.execute("UPDATE batch SET type='production' WHERE batch_id=?", (prod,))
        big = rule("big", gpu=6)
        h = resources.request(self.con, hog, "work", big)
        self.assertTrue(resources.try_grant(self.con, h, self.cap)[0])
        e = resources.request(self.con, exp, "work", rule("e", gpu=4))
        p = resources.request(self.con, prod, "work", rule("p", gpu=4))
        self.assertFalse(resources.try_grant(self.con, e, self.cap)[0])
        ok, why = resources.try_grant(self.con, p, self.cap)
        self.assertFalse(ok)
        self.assertNotIn("behind", why)                                     # waits for room, not for the experiment
        resources.release(self.con, h)
        ok, why = resources.try_grant(self.con, e, self.cap)
        self.assertFalse(ok)
        self.assertIn("behind", why)                                        # the experiment lets production go first
        self.assertTrue(resources.try_grant(self.con, p, self.cap)[0])
        self.assertTrue(resources.try_grant(self.con, e, self.cap)[0])      # then the experiment, beside it (4 + 4 = 8)

    def test_release_without_resources_uses_the_main_checkout(self):
        rel = Path(tempfile.mkdtemp(prefix="reins-rel-"))
        (rel / "reins.toml").write_text('project = "proj"\n')
        main = [rule("m", gpu=3, match="x")]
        with mock.patch.object(resources, "rules", side_effect=lambda cfg: main if cfg.get("resources") or cfg.get("main") else []), \
                mock.patch("reins.projects.all_", return_value=[(Path("/main"), {"project": "proj", "main": True})]):
            self.assertEqual(resources.project_rules(rel, "proj"), main)
            self.assertEqual(resources.project_rules(rel), [])              # no project named: the checkout's own

    def test_peak_recorded_when_the_process_ends(self):
        b = self.running()
        m = resources.Monitor(b, "work", os.getpid(), [{**rule("geo", gpu=1, match="nothing-matches"), "split_env": None}],
                              on_kill=lambda *a: None)
        m.claims[999999] = resources.request(self.con, b, "work", rule("geo", gpu=1))
        m.peaks[999999] = ["geo", 3.2, 1.5, 1, 0.0]
        m._admit(self.con, {})                                              # pid gone -> claim released, peak written
        lr = resources.learned(self.con, "geo")
        self.assertEqual((lr["mem"], lr["gpu"], lr["n"]), (3.2, 1.5, 1))


if __name__ == "__main__":
    unittest.main()
