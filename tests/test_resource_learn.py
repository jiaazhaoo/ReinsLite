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

    def test_subcommand_signature(self):
        r = rule("yp", gpu=4, match=r"s2_extract/yolo_plans\.py")
        self.assertEqual(resources.sig_of(r, "/env/venv/yolo/bin/python e2e/s2_extract/yolo_plans.py fallback --out-root X"), "fallback")
        self.assertEqual(resources.sig_of(r, "python e2e/s2_extract/yolo_plans.py yolo --cases c.json"), "yolo")
        self.assertEqual(resources.sig_of(r, "python e2e/s2_extract/yolo_plans.py --out-root X"), "")
        self.assertEqual(resources.sig_of(r, "python other.py"), "")

    def test_peaks_learned_per_subcommand(self):
        """2026-11-05: a 9.3 GB Qwen fallback peak sized every YOLO pass of the same script at 10.2 GB."""
        b = self.running()
        with tx(self.con):
            for sig, gpu in (("fallback", 9.3), ("yolo", 1.4), ("yolo", 1.8)):
                self.con.execute("INSERT INTO resource_peak (at, batch_id, rule, pid, mem_gb, gpu_gb, share, sig) VALUES"
                                 " ('2026-11-05T00:00:00', ?, 'yp', 1, 1.0, ?, 1, ?)", (b, gpu, sig))
        r = {**rule("yp", gpu=4, match=r"yolo_plans\.py"), "split_env": None}
        self.assertEqual(resources.sized(self.con, r, None, "yolo")[1], 4)            # declared 4 beats 1.8 x 1.1
        self.assertEqual(resources.sized(self.con, r, None, "fallback")[1], round(9.3 * 1.1, 2))
        self.assertEqual(resources.sized(self.con, r, None, "score")[1], round(9.3 * 1.1, 2))  # unseen: the rule's
        c = resources.request(self.con, b, "work", r, cmd="python yolo_plans.py yolo --cases x")
        row = self.con.execute("SELECT gpu_gb, sig FROM resource_claim WHERE id=?", (c,)).fetchone()
        self.assertEqual((row["gpu_gb"], row["sig"]), (4, "yolo"))

    def test_claim_follows_its_process(self):
        b = self.running()
        big = resources.request(self.con, b, "work", rule("yp", gpu=10.24, mem=2))
        self.assertTrue(resources.try_grant(self.con, big, self.cap)[0])
        self.assertEqual(resources.resize(self.con, big, 1.0, 1.2, 2, 4, age_s=30), {})          # not settled yet
        self.assertEqual(resources.resize(self.con, big, 1.0, 1.2, 2, 4, age_s=200), {"gpu": 4})  # down to the declared need
        row = self.con.execute("SELECT gpu_gb, mem_gb FROM resource_claim WHERE id=?", (big,)).fetchone()
        self.assertEqual((row["gpu_gb"], row["mem_gb"]), (4, 2))
        w = resources.request(self.con, b, "work", rule("geo", gpu=self.cap["gpu"] - 4))
        self.assertTrue(resources.try_grant(self.con, w, self.cap)[0])                          # the room is real now
        self.assertEqual(resources.resize(self.con, big, 1.0, 6.0, 2, 4, age_s=10), {"gpu": 6.6})  # grew: raised at once
        self.assertEqual(resources.resize(self.con, big, 1.0, 6.0, 2, 4, age_s=500), {"gpu": 7.5} if 7.5 < 6.6 * 0.9 else {})
        ev = self.con.execute("SELECT detail FROM batch_event WHERE batch_id=? AND event='resource_resized'", (b,)).fetchall()
        self.assertEqual(len(ev), 2)
        self.assertIn("gpu 10.24 -> 4 GB", ev[0]["detail"])

    def test_peak_records_the_subcommand(self):
        b = self.running()
        m = resources.Monitor(b, "work", os.getpid(), [{**rule("yp", gpu=1, match="nothing-matches"), "split_env": None}],
                              on_kill=lambda *a: None)
        m.claims[999998] = resources.request(self.con, b, "work", rule("yp", gpu=1))
        m.peaks[999998] = ["yp", 1.0, 9.3, 1, 0.0, "fallback", 0.0, 1.0]
        m._admit(self.con, {})
        self.assertEqual(resources.learned(self.con, "yp", "fallback")["gpu"], 9.3)
        self.assertEqual(resources.learned(self.con, "yp", "yolo")["gpu"], 9.3)               # no yolo peak yet: the rule's

    def test_watchdog_rebalances_any_granted_claim(self):
        """A claim granted by an older supervisor (no need / peak stored) is lowered by the watchdog pass to the rule's
        declared need once its process has settled; its live peak is kept on the claim."""
        b = self.running()
        c = resources.request(self.con, b, "work", rule("yp", gpu=10.24, mem=2))
        self.assertTrue(resources.try_grant(self.con, c, self.cap)[0])
        with tx(self.con):
            self.con.execute("UPDATE resource_claim SET pid=?, need_mem=NULL, need_gpu=NULL, granted='2026-11-05T00:00:00' WHERE id=?",
                             (os.getpid(), c))
        r = {**rule("yp", gpu=4, mem=2, match="x"), "split_env": None}
        with mock.patch.object(resources, "gpu_by_pid", return_value={os.getpid(): 1.2}), \
                mock.patch.object(resources, "project_rules", return_value=[r]):
            out = resources.rebalance(self.con, self.cap)
        row = self.con.execute("SELECT gpu_gb, peak_gpu FROM resource_claim WHERE id=?", (c,)).fetchone()
        self.assertEqual(row["gpu_gb"], 4)
        self.assertAlmostEqual(row["peak_gpu"], 1.2)
        self.assertTrue(any("resized" in o for o in out))

    def test_own_history_is_a_resize_floor(self):
        b = self.running()
        with tx(self.con):
            self.con.execute("INSERT INTO resource_peak (at, batch_id, rule, pid, mem_gb, gpu_gb, share, sig) VALUES"
                             " ('2026-11-05T00:00:00', ?, 'yp', 1, 1.0, 9.0, 1, 'fallback')", (b,))
        self.assertEqual(resources.sig_peak(self.con, "yp", "fallback"), (1.0, 9.0))
        self.assertEqual(resources.sig_peak(self.con, "yp", "yolo"), (1.0, 9.0))      # no own peak yet: the rule's
        self.assertEqual(resources.sig_peak(self.con, "other", "yolo"), (0.0, 0.0))
        c = resources.request(self.con, b, "work", {**rule("yp", gpu=4, match=r"yolo_plans\.py"), "split_env": None},
                              pid=os.getpid(), cmd="python yolo_plans.py fallback --x")
        self.assertTrue(resources.try_grant(self.con, c, self.cap)[0])
        with tx(self.con):
            self.con.execute("UPDATE resource_claim SET granted='2026-11-05T00:00:00' WHERE id=?", (c,))
        with mock.patch.object(resources, "gpu_by_pid", return_value={os.getpid(): 0.5}):
            resources.rebalance(self.con, self.cap)                   # loading: 0.5 GB now, but it peaked at 9 before
        self.assertEqual(self.con.execute("SELECT gpu_gb FROM resource_claim WHERE id=?", (c,)).fetchone()[0], round(9.0 * 1.1, 2))

    def test_restart_knobs_keep_run_settings_not_secrets(self):
        from reins import runner
        k = runner.knobs({"START_STEP": "5", "CASE_PREFIX": "BRA_", "END_STEP": "10", "PATH": "/usr/bin", "HOME": "/h",
                          "DEEPSEEK_API_KEY": "sk-x", "OS_API_KEY": "y", "CLAUDE_CODE_SESSION_ID": "s", "REINS_BATCH": "b",
                          "lower": "z", "GITHUB_TOKEN": "t"})
        self.assertEqual(k, {"START_STEP": "5", "CASE_PREFIX": "BRA_", "END_STEP": "10"})


if __name__ == "__main__":
    unittest.main()
