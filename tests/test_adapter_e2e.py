"""e2e's glue, now kept in ReinsLite (adapters/e2e, 2026-11-19; until then tools/reins_hook.py and tools/cloud/register.py
in e2e): the stage hook names every case by the client's own oachargeid (user 2026-11-06: 三个字段组合起来全部唯一), and
a chunk reins refuses is never written as registered. The stage hook reads e2e's own run_record.py from its checkout:
skipped where e2e is not checked out."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

AD = Path(__file__).resolve().parents[1] / "adapters/e2e"
E2E = Path(os.environ.get("E2E_REPO") or "/env/code/e2e-plan-extract")
HOOK = AD / "stage_hook.py"
sys.path.insert(0, str(AD))
needs_e2e = pytest.mark.skipif(not (E2E / "tools/run_record.py").exists(), reason="no e2e checkout")
if (E2E / "tools/run_record.py").exists():
    os.environ.setdefault("E2E_REPO", str(E2E))
    import stage_hook as H  # noqa: E402


def _run(*args, prefix="MAN_"):
    env = dict(os.environ, CASE_PREFIX=prefix, E2E_REPO=str(E2E))
    env.pop("REINS_BATCH", None)
    return subprocess.run([sys.executable, str(HOOK), *args], capture_output=True, text=True, env=env)


def _w(tmp_path, recs):
    W = tmp_path / "w"
    W.mkdir()
    (W / "records.json").write_text(json.dumps(recs))
    return W


@needs_e2e
def test_numeric_ids_unchanged(tmp_path):
    W = _w(tmp_path, {"BRA_15884": {"oachargeid": "15884", "ref": "95/00261/LBC"}, "BRA_1": {}})
    assert _run("cases", str(W), prefix="BRA_").stdout.split() == ["15884", "1"]


@needs_e2e
def test_work_dir_keyed_by_another_id_gives_the_clients_id(tmp_path):
    W = _w(tmp_path, {"MAN_707374": {"oachargeid": "707374", "orig_oachargeid": "1982/0520/P", "ref": "1982/0520/P"}})
    assert H.native_ids(W) == {"MAN_707374": "1982/0520/P"}
    assert _run("cases", str(W)).stdout.split() == ["1982/0520/P"]
    cs = tmp_path / "cases.txt"
    cs.write_text("1982/0520/P\n")
    assert _run("check-cases", str(W), str(cs)).returncode == 0
    cs.write_text("707374\n")                                   # the old, non-native case set no longer passes
    assert _run("check-cases", str(W), str(cs)).returncode == 1


@needs_e2e
def test_two_records_one_charge_refused(tmp_path):
    W = _w(tmp_path, {"MAN_1": {"oachargeid": "1", "orig_oachargeid": "1982/0001/P"},
                      "MAN_2": {"oachargeid": "2", "orig_oachargeid": "1982/0001/P"}})
    r = _run("cases", str(W))
    assert r.returncode != 0 and "both name oachargeid" in r.stderr


def fake_reins(tmp_path, fail_on):
    f = tmp_path / "reins"
    f.write_text(f"""#!/bin/bash
echo "$@" >> {tmp_path}/reins_calls.txt
[ "$1" = "{fail_on}" ] && {{ echo "  FAIL file_links  file_link missing: 1 pipeline charges with an empty link"; echo "preflight FAIL"; exit 1; }}
[ "$1" = batch ] && [ "$2" = status ] && echo "$3  done"
exit 0
""")
    f.chmod(0o755)
    return str(f)


def test_register_refusal_not_written_as_registered(tmp_path):
    """2026-11-09: reins refused the preflight of 7 chunks, register.py swallowed it and wrote them as registered. Now a
    refusal is printed, logged once in status.log, kept in register_failed.json, and the chunk is retried."""
    import cloud_register as register
    state = tmp_path / "state"; (state / "results").mkdir(parents=True)
    W = tmp_path / "w"; (W / "lab/georef/out").mkdir(parents=True)
    (W / "cases.json").write_text(json.dumps(["BRA_1"])); (W / "records.json").write_text(json.dumps({"BRA_1": {"oachargeid": "1"}}))
    (W / "lab/georef/out/results_0of1.json").write_text(json.dumps([{"case": "BRA_1", "accepted": True}]))
    (state / "status.json").write_text(json.dumps([{"batch": "b1", "work_dir": str(W)}]))
    (state / "results/b1.tar.gz.unpacked").touch()
    old = register.REINS
    try:
        register.REINS = fake_reins(tmp_path, "preflight")
        ok, msg = register.register(state, "b1", W, ["georef"], "BRA_")
        assert not ok and "NOT REGISTERED: reins preflight failed: FAIL file_links" in msg
        for _ in range(2):
            sys.argv = ["cloud_register.py", str(state)]
            assert register.main() == 1
        assert not (state / "registered.txt").exists()
        assert (state / "status.log").read_text().count("NOT REGISTERED") == 1          # once per reason
        assert "b1" in json.loads((state / "register_failed.json").read_text())
        register.REINS = fake_reins(tmp_path, "nothing")
        assert register.main() == 0
        assert (state / "registered.txt").read_text().split() == ["b1"]
        assert json.loads((state / "register_failed.json").read_text()) == {}
    finally:
        register.REINS = old




def test_register_marks(tmp_path):
    import cloud_register as register
    W = tmp_path / "w"; (W / "lab/georef/out").mkdir(parents=True)
    (W / "cases.json").write_text(json.dumps(["MAN_1", "MAN_2", "MAN_3"]))
    (W / "records.json").write_text(json.dumps({"MAN_1": {"oachargeid": "1", "orig_oachargeid": "1990/0001/P"},
                                                "MAN_2": {"oachargeid": "2", "orig_oachargeid": "1990/0002/P"},
                                                "MAN_3": {"oachargeid": "3"}}))
    (W / "lab/georef/out/results_0of1.json").write_text(json.dumps([
        {"case": "MAN_1", "accepted": True}, {"case": "MAN_1", "accepted": False}, {"case": "MAN_2", "accepted": False}]))
    rows, st = register.marks(W)
    assert rows == [("1990/0001/P", "done", "1 plan(s) placed on RunPod"), ("1990/0002/P", "done", "georef ran on RunPod, no plan placed"),
                    ("3", "failed", "no georef row")]
    assert st == {"plans": 3, "placed": 1, "cases": 3, "cases_placed": 1}


def test_runctl_open_for_a_managed_project_runs_from_its_checkout(tmp_path, monkeypatch):
    """tools/cloud/prepare.sh opens its chunk runs as project e2e-plan-extract: reins must see e2e's rules."""
    fake = tmp_path / "reins"
    fake.write_text(f'#!/bin/bash\necho "$PWD $@" >> {tmp_path}/calls\necho "opened mansfield-wp1-rework-20261119-4"\n')
    fake.chmod(0o755)
    r = subprocess.run([sys.executable, str(AD / "runctl.py"), "open", "--project", "e2e-plan-extract", "--scope",
                        "mansfield-wp1", "--kind", "rework", "--cases", "c.txt"], capture_output=True, text=True,
                       env={**os.environ, "REINS_BIN": str(fake)})
    assert r.stdout.strip() == "mansfield-wp1-rework-20261119-4", r.stderr
    call = (tmp_path / "calls").read_text().strip()
    if E2E.is_dir():
        assert call.startswith(f"{E2E} batch open --scope mansfield-wp1 --type rework")
