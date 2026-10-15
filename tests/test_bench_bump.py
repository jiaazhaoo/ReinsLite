"""reins bench bump: symlinks survive with identical targets; the frozen version stays read-only (2026-10-15,
bench-sheffield-wp3-359 v10 -> v11 lost 133 symlinks and made every v10 file writable)."""
from __future__ import annotations

import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path

from reins import bench
from reins.store import connect


def writable(p: Path) -> bool:
    return bool(p.stat().st_mode & 0o222)


class Bump(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-bump-"))
        os.environ["REINS_HOME"] = str(self.tmp)
        os.environ["REINS_SESSION"] = "sess-A"
        (self.tmp / "config.toml").write_text(f'bench_root = "{self.tmp / "benchmarks"}"\ngateway_port = 9\n')
        self.con = connect(self.tmp)
        cases = self.tmp / "cases.txt"
        cases.write_text("\n".join(str(100 + i) for i in range(4)) + "\n")
        self.v1 = Path(bench.init(self.con, "b", "e2e", cases, "four cases", holdout_fraction=0)["path"])
        (self.v1 / "inputs" / "records.json").write_text("{}")
        self.base = self.v1.parent / "base"                       # what the links in v1 point at, like ../v1/inputs
        (self.base / "inputs").mkdir(parents=True)
        (self.base / "inputs" / "records.json").write_text('{"101": "proposal text"}')
        (self.base / "stageA" / "101").mkdir(parents=True)
        (self.base / "stageA" / "101" / "out.json").write_text("{}")
        (self.v1 / "linked").mkdir()
        os.symlink("../../base/inputs", self.v1 / "linked" / "inputs")          # dir symlink at depth 1
        (self.v1 / "stageA").mkdir()
        os.symlink("../../base/stageA/101", self.v1 / "stageA" / "101")         # dir symlink at depth 2
        os.symlink("../base/inputs/records.json", self.v1 / "records-link.json")  # file symlink
        os.symlink("/no/such/target", self.v1 / "dangling")                    # copied as it is, reported
        (self.v1 / "stages" / "judge").mkdir()
        (self.v1 / "stages" / "judge" / "verdicts.jsonl").write_text("{}\n")
        bench.freeze(self.con, "b", "v1")

    def tearDown(self):
        for d, dirs, files in os.walk(self.tmp):
            os.chmod(d, 0o755)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def links(self, root: Path) -> dict[str, str]:
        return {str((Path(d) / e).relative_to(root)): os.readlink(Path(d) / e)
                for d, dirs, files in os.walk(root) for e in dirs + files if (Path(d) / e).is_symlink()}

    def test_symlinks_kept_with_identical_targets(self):
        r = bench.bump(self.con, "b", "v2")
        v2 = Path(r["path"])
        want = self.links(self.v1)
        self.assertEqual(len(want), 4)
        self.assertEqual(self.links(v2), want)
        self.assertEqual(r["symlinks"], 4)
        self.assertEqual(r["dangling_symlinks"], ["dangling"])
        self.assertEqual((v2 / "linked" / "inputs" / "records.json").read_text(), '{"101": "proposal text"}')
        self.assertTrue((v2 / "stageA" / "101" / "out.json").is_file())
        self.assertTrue((v2 / "records-link.json").is_symlink())   # not materialised into a hardlink of the target
        self.assertEqual(r["files"], 6)            # regular files only: manifest, cases, labels, changelog, 2 data
        self.assertTrue(bench.verify(self.con, "b", 1)["ok"])

    def test_previous_version_stays_read_only(self):
        before = {p: p.stat().st_mode for p in self.v1.rglob("*") if p.is_file() and not p.is_symlink()}
        self.assertTrue(before and not any(m & 0o222 for m in before.values()))
        r = bench.bump(self.con, "b", "v2")
        v2 = Path(r["path"])
        after = {p: p.stat().st_mode for p in before}
        self.assertEqual(after, before)                          # same modes: nothing in v1 became writable
        self.assertEqual(r["reprotected"], 0)
        shared = v2 / "stages" / "judge" / "verdicts.jsonl"
        self.assertEqual(shared.stat().st_ino, (self.v1 / "stages" / "judge" / "verdicts.jsonl").stat().st_ino)
        self.assertFalse(writable(shared))                       # in-place write refused by the mode
        for f in ("labels.jsonl", "cases.csv", "CHANGELOG.md", "MANIFEST.json"):   # bump's own files: own inode
            self.assertNotEqual((v2 / f).stat().st_ino, (self.v1 / f).stat().st_ino)
            self.assertTrue(writable(v2 / f))
        shared.unlink(); shared.write_text("changed\n")          # the documented way to change a file in v2
        self.assertEqual((self.v1 / "stages" / "judge" / "verdicts.jsonl").read_text(), "{}\n")
        self.assertTrue(bench.verify(self.con, "b", 1)["ok"])

    def test_writable_previous_version_is_reprotected(self):
        p = self.v1 / "stages" / "judge" / "verdicts.jsonl"     # an adopted vN may never have been made read-only
        os.chmod(p, 0o644)
        r = bench.bump(self.con, "b", "v2")
        self.assertEqual(r["reprotected"], 1)
        self.assertFalse(writable(p))
        self.assertFalse(stat.S_IMODE(p.stat().st_mode) & 0o222)

    def test_add_input_follows_links(self):
        """add-input copies from outside the benchmark: links are followed, so nothing dangles and nothing is dropped."""
        cases = self.tmp / "cases.txt"
        bench.init(self.con, "c", "e2e", cases, "four cases", holdout_fraction=0)
        src = self.tmp / "src"; (src / "real").mkdir(parents=True)
        (src / "real" / "a.json").write_text("{}")
        os.symlink("real", src / "dirlink"); os.symlink("real/a.json", src / "filelink.json")
        self.assertEqual(bench.add_input(self.con, "c", src), 3)
        inputs = self.tmp / "benchmarks" / "bench-c" / "v1" / "inputs"
        self.assertEqual(self.links(inputs), {})
        self.assertTrue((inputs / "dirlink" / "a.json").is_file() and (inputs / "filelink.json").is_file())


if __name__ == "__main__":
    unittest.main()
