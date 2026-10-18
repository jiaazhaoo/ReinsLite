"""The guard judges what a command executes, not the words in it; forks can take over; sessions get told.

Regression for 2026-10-09..18: 141 blocks, of which 67 of the 80 recovered commands were misparses (a `>` inside a
quoted string or a here-doc body read as a redirection) and the commands after a here-doc were invisible."""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

from reins import batches, inbox, issues, sessions
from reins.sessions import redirect_targets, segments
from reins.store import ReinsError, connect, now, tx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hooks"))


class Parse(unittest.TestCase):
    def test_quotes_and_heredocs_are_text(self):
        cases = [
            ('reins issue open --batch B --symptom "iou -> a.union(b)) crashes" 2>&1 | tail -n 1', []),
            ("python3 - <<'PY'\nif x>80 and y<3: print(1)\nPY\necho done > out.txt", ["out.txt"]),
            ("python3 -c 'print(1 if 3>2 else 0)' > res.txt", ["res.txt"]),
            ("cat < in.txt > out.txt 2>/dev/null", ["out.txt"]),
            ('cmd &> both.log; cmd2 >> "my file.txt"', ["both.log", "my file.txt"]),
            ('grep x <<< "a>b"', []),
            ("cat <<-EOF > f.txt\n\tline > not_a_file\n\tEOF\necho ok", ["f.txt"]),
            ('echo "<<EOF"; echo hi > g', ["g"]),
            ("ls 2>&1 >&2 | head", []),
        ]
        for cmd, want in cases:
            self.assertEqual(redirect_targets(cmd), want, cmd)

    def test_commands_after_a_heredoc_are_seen(self):
        cmd = "cat > notes.md <<'EOF'\ngit merge main is mentioned here\nEOF\ngit merge feature"
        argvs = [s["argv"] for s in segments(cmd)]
        self.assertEqual(argvs, [["cat"], ["git", "merge", "feature"]])          # the body's words are not a command


class GuardPaths(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("REINS_HOME", "REINS_CODE_ROOT", "REINS_SESSION")}
        self._cwd = os.getcwd()
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-gp-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_CODE_ROOT"] = str(self.tmp / "code")
        os.environ["REINS_SESSION"] = "sess-A"
        (self.tmp / "code").mkdir()
        self.con = connect(self.tmp / "home")
        self.bench = self.tmp / "bench" / "bench-x" / "v1"; self.bench.mkdir(parents=True)
        with tx(self.con):
            self.con.execute("INSERT INTO benchmark (name, version, project, path, status, n_cases, n_dev, n_holdout, manifest_sha,"
                             " created, frozen) VALUES ('bench-x', 1, 'p', ?, 'frozen', 1, 1, 0, 'x', ?, ?)", (str(self.bench), now(), now()))
        import guard
        importlib.reload(guard)
        self.g = guard
        self.rel = self.tmp / "code" / "proj-rel-qa-20261011-1"; self.rel.mkdir()

    def tearDown(self):
        os.chdir(self._cwd)
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_misparses_pass_real_writes_are_explained(self):
        os.chdir(self.rel)
        self.assertIsNone(self.g.check_bash('reins issue open --batch B --symptom "a -> b(c))" --cases 1'))
        self.assertIsNone(self.g.check_bash("python3 - <<'PY'\nprint(3>2)\nPY"))
        msg = self.g.check_bash("echo x > batch_cases.txt")
        self.assertIn("release tree", msg); self.assertIn("work dir", msg)
        self.assertIn("bench bump", self.g.check_bash(f"echo x > {self.bench}/cases.csv"))
        scratch = self.tmp / "bench" / "tmp"; scratch.mkdir()
        self.assertIsNone(self.g.check_bash(f"echo x > {scratch}/cases.csv"))    # scratch under the bench root is fine

    def test_variables(self):
        os.chdir(self.tmp)
        self.assertIsNotNone(self.g.check_bash(f"W={self.bench}; echo x > $W/labels.jsonl"))   # expanded from the command
        self.assertIsNone(self.g.check_bash("echo x > $UNKNOWN_DIR/out.json"))             # unknown: not guessed


class InheritAndInbox(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-ih-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_SESSION"] = "parent"
        self.con = connect(self.tmp / "home")
        old = "2026-01-01T00:00:00"
        with tx(self.con):
            for sid, parent, seen in (("parent", None, old), ("child", "parent", now()), ("stranger", None, now())):
                self.con.execute("INSERT INTO session (id, started, last_seen, status, parent) VALUES (?,?,?,?,?)",
                                 (sid, old, seen, "idle", parent))
        (self.tmp / "cases.txt").write_text("101\n102\n")
        self.b = batches.open_(self.con, project="p", scope="x-wp1", type_="experiment", purpose="inherit test",
                               case_file=self.tmp / "cases.txt", stages=[{"stage": "s", "module_version": None, "paid": 0,
                                                                          "spend_cap": None, "time_limit_s": None}])

    def test_fork_takes_over_parent_only(self):
        with self.assertRaises(ReinsError):
            sessions.inherit(self.con, "stranger", "parent")                 # not an ancestor, no reason
        r = sessions.inherit(self.con, "child")
        self.assertEqual(r["from"], "parent"); self.assertEqual(r["batches"], [self.b])
        self.assertEqual(inbox.owner_of(self.con, self.b), "child")
        with tx(self.con):
            self.con.execute("UPDATE session SET last_seen=? WHERE id='child'", (now(),))
            self.con.execute("UPDATE session SET parent='child' WHERE id='stranger'")
        with self.assertRaises(ReinsError):
            sessions.inherit(self.con, "stranger")                           # the source is active: refused

    def test_messages_reach_the_owner(self):
        os.environ["REINS_SESSION"] = "stranger"
        iid = issues.open_(self.con, self.b, ["101"], "dates read as the signature date")
        got = inbox.unread(self.con, "parent")
        self.assertEqual(len(got), 1); self.assertIn(f"issue #{iid}", got[0]["title"])
        self.assertEqual(inbox.unread(self.con, "parent"), [])               # delivered once
        self.assertIn("issue", inbox.render(got))


if __name__ == "__main__":
    unittest.main()
