"""C17 weights: ML model files versioned by content, training record harvested, pinned by modules, checked before a batch."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from reins import artifacts
from reins.store import ReinsError, connect


class Weights(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-w-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_SESSION"] = "sess-A"
        self.con = connect(self.tmp / "home")
        run = self.tmp / "runs" / "panel_r2"; (run / "weights").mkdir(parents=True)
        (run / "weights" / "best.pt").write_bytes(b"W" * 5000)
        (run / "args.yaml").write_text("task: detect\nmodel: /x/yolo26n.pt\ndata: /data/x/data.yaml\nepochs: 60\nimgsz: 1536\nseed: 0\n")
        (run / "results.csv").write_text("epoch,metrics/precision(B),metrics/mAP50(B),metrics/mAP50-95(B)\n1,0.5,0.6,0.4\n2,0.83,0.84,0.70\n")
        self.yolo = run / "weights" / "best.pt"
        cnx = self.tmp / "convnext"; cnx.mkdir()
        (cnx / "best_model.pt").write_bytes(b"C" * 7000)
        (cnx / "history.json").write_text(json.dumps([{"epoch": 1, "accuracy": 0.9}, {"epoch": 2, "accuracy": 0.97, "f1_text": 0.95}]))
        (cnx / "train_manifest.csv").write_text("path,label\n" + "a,b\n" * 120)
        self.cnx = cnx / "best_model.pt"

    def test_content_versions_and_training_record(self):
        r1 = artifacts.register_weights(self.con, "panel_yolo", {"file": str(self.yolo)}, "panel detector")
        self.assertEqual((r1["name"], r1["new"]), ("weights-panel_yolo-v1", True))
        self.assertFalse(artifacts.register_weights(self.con, "panel_yolo", {"file": str(self.yolo)})["new"])   # same bytes
        body = json.loads(artifacts.get(self.con, "weights-panel_yolo-v1")["body"])
        self.assertEqual(body["training"]["framework"], "ultralytics")
        self.assertEqual(body["training"]["train"]["epochs"], "60")
        self.assertEqual(body["training"]["metrics"]["metrics/mAP50(B)"], "0.84")
        self.assertEqual(body["training"]["epochs_run"], 2)
        self.yolo.write_bytes(b"W" * 5001)                                   # retrained: new content, new version
        self.assertEqual(artifacts.register_weights(self.con, "panel_yolo", {"file": str(self.yolo)})["name"], "weights-panel_yolo-v2")
        r = artifacts.register_weights(self.con, "txtdraw", {"file": str(self.cnx)})
        b = json.loads(artifacts.get(self.con, r["name"])["body"])
        self.assertEqual((b["training"]["framework"], b["training"]["epochs_run"], b["training"]["metrics"]["accuracy"],
                          b["training"]["dataset"]["rows"]), ("json-history", 2, 0.97, 120))

    def test_hash_cache_and_symlink(self):
        link = self.tmp / "link.pt"; link.symlink_to(self.yolo)
        a, _ = artifacts.file_sha(self.con, link)
        b, _ = artifacts.file_sha(self.con, self.yolo)
        self.assertEqual(a, b)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM file_sha").fetchone()[0], 1)

    def test_check_before_a_batch(self):
        cfg = {"weights": {"panel_yolo": {"file": str(self.yolo)}, "missing": {"file": str(self.tmp / "nope.pt")}}}
        res = {n: (ok, d) for n, ok, d in artifacts.check_weights(self.con, cfg)}
        self.assertFalse(res["weights_panel_yolo"][0]); self.assertIn("not a registered version", res["weights_panel_yolo"][1])
        self.assertFalse(res["weights_missing"][0])
        artifacts.scan(self.con, self.tmp, {"weights": {"panel_yolo": {"file": str(self.yolo)}}})
        self.assertTrue(artifacts.check_weights(self.con, {"weights": {"panel_yolo": {"file": str(self.yolo)}}})[0][1])
        self.yolo.write_bytes(b"swapped")                                    # the silent-fallback incident, caught
        ok, d = artifacts.check_weights(self.con, {"weights": {"panel_yolo": {"file": str(self.yolo)}}})[0][1:]
        self.assertFalse(ok); self.assertIn("weights-panel_yolo-v1", d)

    def test_dir_weights(self):
        d = self.tmp / "qwen"; d.mkdir()
        (d / "model-00001.safetensors").write_bytes(b"Q" * 100); (d / "config.json").write_text("{}"); (d / "README.md").write_text("x")
        r = artifacts.register_weights(self.con, "qwen_vl", {"dir": str(d)})
        body = json.loads(artifacts.get(self.con, r["name"])["body"])
        self.assertEqual(sorted(body["files"]), ["config.json", "model-00001.safetensors"])      # README is not a model file
        with self.assertRaises(ReinsError):
            artifacts.register_weights(self.con, "empty", {"dir": str(self.tmp / "nothing")})


if __name__ == "__main__":
    unittest.main()
