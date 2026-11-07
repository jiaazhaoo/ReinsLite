"""The one QA review page (user 2026-11-03): reins builds it from cards, every mark is a signed review_event."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from reins import batches, qa_page
from reins.store import ReinsError, connect

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
import guard  # noqa: E402


class QaPage(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("REINS_HOME", "REINS_SESSION")}
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-qp-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_SESSION"] = "abcd1234-0000"
        (self.tmp / "home").mkdir()
        self.con = connect(self.tmp / "home")
        (self.tmp / "cases.txt").write_text("101\n102\n103\n")
        self.b = batches.open_(self.con, project="p", scope="x-wp1", type_="experiment", purpose="t",
                               case_file=self.tmp / "cases.txt",
                               stages=[{"stage": "s", "module_version": None, "paid": 0, "spend_cap": None, "time_limit_s": None}],
                               work_dir=str(self.tmp / "runs" / "{batch}"))
        from PIL import Image
        Image.new("RGBA", (40, 30), (0, 0, 0, 0)).save(self.tmp / "map.png")      # transparent: must come out white
        cards = [{"case": "101", "images": [{"path": str(self.tmp / "map.png"), "caption": "OS"}], "table": {"rows": [["a", "b"]]}},
                 {"case": "102", "images": [{"path": "/nowhere.png", "caption": "plan"}]}]
        self.cards = self.tmp / "cards.jsonl"
        self.cards.write_text("\n".join(json.dumps(c) for c in cards) + "\n")

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_build_copies_images_and_registers(self):
        r = qa_page.build(self.con, self.b, self.cards, "gate1", "Gate 1", "Jia")
        self.assertEqual((r["cards"], r["missing_images"]), (2, 1))
        d = json.loads((Path(r["dir"]) / "cards.json").read_text())
        self.assertEqual(d["meta"]["reviewer"], "Jia")
        self.assertEqual([g["key"] for g in d["meta"]["grades"]], ["P", "Q", "E"])
        src = d["cards"][0]["images"][0]["src"]
        from PIL import Image
        self.assertEqual(Image.open(Path(r["dir"]) / src).getpixel((5, 5)), (255, 255, 255))
        self.assertEqual(qa_page.page(self.con, self.b, "gate1")["n_cards"], 2)

    def test_layers_kept_transparent_and_sized_to_the_base(self):
        from PIL import Image
        Image.new("RGB", (400, 300), (200, 200, 200)).save(self.tmp / "base.png")
        Image.new("RGBA", (800, 600), (0, 0, 255, 0)).save(self.tmp / "machine.png")
        (self.tmp / "layers.jsonl").write_text(json.dumps({"case": "103", "images": [{
            "path": str(self.tmp / "base.png"), "caption": "map",
            "layers": [{"path": str(self.tmp / "machine.png"), "label": "machine polygon", "on": False},
                       {"path": "/nowhere.png", "label": "gone"}]}]}) + "\n")
        r = qa_page.build(self.con, self.b, self.tmp / "layers.jsonl", "lay", "Layers", "Jia")
        self.assertEqual(r["missing_images"], 1)                           # the missing overlay is counted, not fatal
        d = json.loads((Path(r["dir"]) / "cards.json").read_text())
        im = d["cards"][0]["images"][0]
        self.assertEqual([(l["label"], l["on"]) for l in im["layers"]], [("machine polygon", False)])
        with Image.open(Path(r["dir"]) / im["layers"][0]["src"]) as ly, Image.open(Path(r["dir"]) / im["src"]) as b:
            self.assertEqual((ly.mode, ly.size), ("RGBA", b.size))

    def test_foreign_case_refused(self):
        bad = self.tmp / "bad.jsonl"
        bad.write_text('{"case": "999"}\n')
        with self.assertRaises(ReinsError):
            qa_page.build(self.con, self.b, bad, "x")

    def test_marks_are_review_events_latest_wins(self):
        qa_page.build(self.con, self.b, self.cards, "gate1")
        qa_page.mark(self.con, self.b, "gate1", {"case": "101", "grade": "P", "reviewer": "Jia"})
        qa_page.mark(self.con, self.b, "gate1", {"case": "101", "grade": "E", "code": "location", "note": "far", "reviewer": "Jia"})
        qa_page.mark(self.con, self.b, "gate1", {"case": "102", "grade": "Q", "reviewer": "Jia"})
        m = qa_page.marks(self.con, self.b, "gate1")
        self.assertEqual((m["101"]["grade"], m["101"]["verdict"], m["101"]["note"]), ("E", "wrong", "far"))
        self.assertEqual((m["102"]["verdict"], m["102"]["note"]), ("unsure", ""))
        n = self.con.execute("SELECT COUNT(*) FROM review_event WHERE batch_id=? AND client='qa_page'", (self.b,)).fetchone()[0]
        self.assertEqual(n, 3)                                              # append-only
        self.assertIn("E  101", qa_page.text(self.con, self.b, "gate1"))
        with self.assertRaises(ReinsError):
            qa_page.mark(self.con, self.b, "gate1", {"case": "101", "grade": "P", "reviewer": ""})
        with self.assertRaises(ReinsError):
            qa_page.mark(self.con, self.b, "nopage", {"case": "101", "grade": "P", "reviewer": "Jia"})

    def test_marks_carry_the_card_title(self):
        """a card may name the case by the client's own fields (title); marks and the export carry it"""
        cards = self.tmp / "titled.jsonl"
        cards.write_text(json.dumps({"case": "101", "title": "mansfield · 1982/0520/P · 1982/0520/P"}) + "\n" + json.dumps({"case": "102"}) + "\n")
        qa_page.build(self.con, self.b, cards, "t1")
        qa_page.mark(self.con, self.b, "t1", {"case": "101", "grade": "P", "reviewer": "Jia"})
        qa_page.mark(self.con, self.b, "t1", {"case": "102", "grade": "E", "reviewer": "Jia"})
        m = qa_page.marks(self.con, self.b, "t1")
        self.assertEqual(m["101"]["title"], "mansfield · 1982/0520/P · 1982/0520/P")
        self.assertEqual(m["102"]["title"], "")
        self.assertIn("1982/0520/P", qa_page.text(self.con, self.b, "t1", all_=True))
        self.assertIn('"title"', (qa_page.UI / "qa.html").read_text())       # the CSV export has a title column

    def test_guard_refuses_hand_made_pages_outside_repos(self):
        page = "<label><input type='radio' name='g_1' value='P'>P</label> grade"
        self.assertTrue(guard.hand_made_review_page(str(self.tmp / "build.py"), page))
        self.assertIsNone(guard.hand_made_review_page(str(self.tmp / "report.html"), "<table>correct 3 wrong 1</table>"))
        self.assertIsNone(guard.hand_made_review_page(str(qa_page.UI / "qa.html"), page))     # the template itself


if __name__ == "__main__":
    unittest.main()
