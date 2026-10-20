"""Weights kept off this machine: trained versions go to the project's store with a sha256 manifest; a new machine
pulls them back with no registry and refuses changed bytes.

Regression for 2026-10-20: the seven self-trained models of e2e-plan-extract existed only on one disk (92% full)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from reins import artifacts, weights_store
from reins.store import ReinsError, connect, tx


class WeightsStore(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-ws-"))
        self._env = {k: os.environ.get(k) for k in ("REINS_HOME", "REINS_SESSION")}
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_SESSION"] = "sess-A"
        self.con = connect(self.tmp / "home")
        m = self.tmp / "models"; (m / "qwen").mkdir(parents=True)
        (m / "det.pt").write_bytes(b"D" * 3000)
        (m / "qwen" / "model.safetensors").write_bytes(b"Q" * 4000)
        self.det = m / "det.pt"
        self.cfg = {"project": "p", "weights_store": {"kind": "dir", "path": str(self.tmp / "store"), "prefix": "p"},
                    "weights": {"det": {"file": str(self.det), "origin": "trained"},
                                "qwen": {"dir": str(m / "qwen"), "origin": "pretrained"}}}
        for base, spec in self.cfg["weights"].items():
            artifacts.register_weights(self.con, base, spec, base)
        with tx(self.con):
            self.con.execute("UPDATE artifact SET status='active' WHERE kind='weights'")

    def tearDown(self):
        for k, v in self._env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def test_push_uploads_trained_only_once_then_pull_installs_without_a_registry(self):
        r = weights_store.push(self.con, self.cfg)
        self.assertEqual([u["name"] for u in r["uploaded"]], ["weights-det-v1"])       # pretrained stays public
        store = self.tmp / "store" / "p"
        self.assertEqual((store / "weights-det-v1" / "det.pt").read_bytes(), b"D" * 3000)
        man = json.loads((store / "weights-det-v1" / "manifest.json").read_text())
        self.assertEqual(man["files"]["det.pt"]["size"], 3000)
        self.assertEqual(json.loads((store / "index.json").read_text())["active"], {"det": "weights-det-v1"})
        self.assertEqual(weights_store.push(self.con, self.cfg)["uploaded"], [])         # already up: nothing again
        # a new machine: only the store
        got = weights_store.pull(self.cfg, self.tmp / "newbox")
        self.assertEqual([g["name"] for g in got], ["weights-det-v1"])
        self.assertEqual((self.tmp / "newbox" / "det" / "det.pt").read_bytes(), b"D" * 3000)
        # a damaged copy in the store is refused, not installed
        (store / "weights-det-v1" / "det.pt").write_bytes(b"X" * 3000)
        with self.assertRaises(ReinsError) as e:
            weights_store.pull(self.cfg, self.tmp / "box2")
        self.assertIn("sha256", str(e.exception))
        self.assertFalse((self.tmp / "box2" / "det" / "det.pt").exists())

    def test_a_retrained_file_is_a_new_version_and_a_changed_unregistered_file_is_a_notice(self):
        weights_store.push(self.con, self.cfg)
        self.det.write_bytes(b"D" * 3001)                                      # changed in place, not registered
        artifacts.register_weights(self.con, "det", self.cfg["weights"]["det"], "det retrained")   # -> v2, uploaded
        self.det.write_bytes(b"D" * 3002)                                      # and changed again before the push
        r = weights_store.push(self.con, self.cfg)
        self.assertEqual(r["uploaded"], [])
        self.assertIn("changed since it was registered", r["failed"][0])
        n = self.con.execute("SELECT title FROM notification WHERE key='weights_push:p'").fetchall()
        self.assertEqual(len(n), 1)

    def test_named_push_uploads_undeclared_weights(self):
        exp = self.tmp / "exp.pt"; exp.write_bytes(b"E" * 900)
        artifacts.register_weights(self.con, "plot_seg", {"file": str(exp)}, "experiment")
        self.assertEqual([u["name"] for u in weights_store.push(self.con, self.cfg)["uploaded"]], ["weights-det-v1"])
        r = weights_store.push(self.con, self.cfg, names=["weights-plot_seg-v1"])
        self.assertEqual([u["name"] for u in r["uploaded"]], ["weights-plot_seg-v1"])
        got = weights_store.pull(self.cfg, self.tmp / "nb", ["weights-plot_seg-v1"])
        self.assertEqual((self.tmp / "nb" / "plot_seg" / "exp.pt").read_bytes(), b"E" * 900)
        self.assertEqual(json.loads((self.tmp / "store" / "p" / "index.json").read_text())["active"], {"det": "weights-det-v1"})

    def test_no_store_is_reported_not_guessed(self):
        cfg = {k: v for k, v in self.cfg.items() if k != "weights_store"}
        self.assertIsNone(weights_store.push(self.con, cfg)["store"])
        with self.assertRaises(ReinsError):
            weights_store.Store({"kind": "s3"})


if __name__ == "__main__":
    unittest.main()
