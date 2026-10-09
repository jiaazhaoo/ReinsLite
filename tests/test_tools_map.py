"""C17 tools + the board's pipeline map: code/api/data tools are versioned like weights; the workflow version carries
stage titles, mascots and the whole toolbox; the board reads the newest workflow."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from reins import artifacts, board
from reins.store import ReinsError, connect


class Tools(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-t-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_SESSION"] = "sess-T"
        self.con = connect(self.tmp / "home")
        self.repo = self.tmp / "repo"; (self.repo / "pkg").mkdir(parents=True)
        (self.repo / "pkg" / "trace.py").write_text("def trace(): return 1\n")
        (self.repo / "pkg" / "util.py").write_text("X = 1\n")
        self.data = self.tmp / "basemap"; self.data.mkdir()
        (self.data / "tile_1.gpkg").write_bytes(b"G" * 300)

    def cfg(self):
        return {"project": "demo",
                "tools": {"tracer": {"kind": "code", "files": ["pkg/*.py"], "about": "red-line tracer", "title": "描线算法"},
                          "geocode": {"kind": "api", "endpoint": "https://maps.example/geocode", "params": {"region": "uk"}},
                          "basemap": {"kind": "data", "path": str(self.data)}},
                "weights": {"panel": {"file": str(self.data / "tile_1.gpkg"), "origin": "trained"}},
                "prompts": {}, "models": {"judge": {"provider": "openrouter", "id": "x/y"}},
                "workflows": {"qa": {"about": "demo flow", "stages": [
                    {"name": "prepare", "title": "准备输入", "steps": "0", "mascot": "box"},
                    {"name": "trace", "title": "描红线", "steps": "11", "mascot": "pencil", "tools": ["tracer", "basemap"],
                     "weights": ["panel"]},
                    {"name": "check", "title": "质检分道", "models": ["judge"], "tools": ["geocode"], "paid": True}]}}}

    def test_code_api_data_versions(self):
        r = artifacts.scan(self.con, self.repo, self.cfg())
        self.assertEqual((r["tool:tracer"], r["tool:geocode"], r["tool:basemap"]), ("tool-tracer-v1", "tool-geocode-v1", "tool-basemap-v1"))
        self.assertEqual(artifacts.scan(self.con, self.repo, self.cfg())["tool:tracer"], "tool-tracer-v1")       # unchanged
        (self.repo / "pkg" / "util.py").write_text("X = 2\n")
        self.assertEqual(artifacts.scan(self.con, self.repo, self.cfg())["tool:tracer"], "tool-tracer-v2")
        self.assertTrue(all(ok for _, ok, _ in artifacts.check_tools(self.con, self.repo, self.cfg())))
        (self.data / "tile_2.gpkg").write_bytes(b"H")                                            # basemap changed under us
        res = artifacts.check_tools(self.con, self.repo, self.cfg())
        self.assertFalse(res[0][1]); self.assertIn("changed", res[0][2])
        with self.assertRaises(ReinsError):
            artifacts.register_tool(self.con, self.repo, "bad", {"kind": "code", "files": ["nope/*.py"]})

    def test_workflow_carries_toolbox_and_board_map(self):
        r = artifacts.freeze_workflow(self.con, self.repo, self.cfg(), "qa")
        body = json.loads(artifacts.get(self.con, r["name"])["body"])
        self.assertEqual(body["project"], "demo")
        self.assertEqual(body["stages"][1]["title"], "描红线")
        self.assertEqual(body["toolbox"]["tool-tracer-v1"], {"group": "code", "title": "描线算法"})
        self.assertEqual(body["toolbox"]["weights-panel-v1"]["group"], "trained")
        self.assertEqual(body["toolbox"]["model-judge-v1"]["group"], "paid")
        m = board.pipeline_map(self.con, [{"stage": "trace", "title": "b1", "pct": 40, "status": "running", "type": "pilot"}], [])
        self.assertEqual(len(m), 1)
        st = {s["name"]: s for s in m[0]["stages"]}
        self.assertEqual(st["trace"]["mascot"], "pencil")
        self.assertEqual(st["trace"]["code"], [])
        self.assertEqual(st["trace"]["batches"][0]["title"], "b1")
        self.assertEqual([t["group"] for t in st["trace"]["tools"]], ["trained", "code", "data"])
        self.assertEqual({t["title"] for t in m[0]["toolbox"]}, {"描线算法", "basemap", "geocode", "judge", "panel"})
        (self.repo / "pkg" / "trace.py").write_text("def trace(): return 2\n")                  # new tool version -> new workflow
        r2 = artifacts.freeze_workflow(self.con, self.repo, self.cfg(), "qa")
        self.assertEqual(r2["name"], "workflow-qa-v2")
        self.assertEqual(board.pipeline_map(self.con, [], [])[0]["workflow"], "workflow-qa-v2")


if __name__ == "__main__":
    unittest.main()


class BoardScript(unittest.TestCase):
    def test_served_script_parses(self):
        """A syntax error in the front end blanks the whole board (happened twice when it lived in a Python string)."""
        import shutil, subprocess
        if not shutil.which("node"):
            self.skipTest("node not installed")
        r = subprocess.run(["node", "--check", str(board.UI / "app.js")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


class ConfigCheck(unittest.TestCase):
    def test_template_is_consistent_and_errors_are_found(self):
        import tomllib
        from reins import projects
        root = Path(__file__).resolve().parents[1]
        cfg = tomllib.loads((root / "examples" / "reins.toml").read_text())
        self.assertEqual(projects.check_config(cfg), [])
        cfg["workflows"]["main"]["stages"][2]["tools"].append("nope")
        cfg["modules"]["extract"]["prompts"].append("ghost")
        probs = projects.check_config(cfg)
        self.assertEqual(len(probs), 2, probs)
