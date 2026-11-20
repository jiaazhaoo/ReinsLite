# Working on e2e-plan-extract on this machine (ReinsLite, from outside)

e2e-plan-extract (`/env/code/e2e-plan-extract`, GitHub RMSI-UK/e2e-plan-extract) is business code that anyone may
clone and run; it carries no reins file and names no reins variable (user 2026-11-19). On this machine ReinsLite
manages it from here: its settings are `reins.toml` / `rules.toml` / `review.toml` in this folder, found by the repo
path. There is no other way to change its code or run its pipeline here (user, 2026-10-07). Board: http://127.0.0.1:8791.

**Develop** (reins controls versions, conflicts, gates):
1. `reins dev start MODULE SUFFIX --about 'one line'` (`--from-batch B --cases ID,...` when a run showed the problem);
   a worktree made earlier: `reins dev adopt WORKTREE MODULE SUFFIX --about ...`. Edits outside your own candidate
   worktree are refused by the hook.
2. Reach main only through the gate: `reins dev finish VERSION` (vocab check + `benchmark/judge_hmlr/gate.py`, run by
   `gate.py` here with `--out $REINS_GATE_OUT`). Files in `[dev.upstream]` need the pipeline tier on an idle GPU, or
   `--skip-tier upstream --why '...'`.
3. Releases: `reins dev release --note '...'` (tags `qa-YYYYMMDD-N`, read-only tree `e2e-plan-extract-rel-qa-...`).
4. A change to these settings or to the glue here is a ReinsLite change: `reins dev start core SUFFIX` in /env/code/ReinsLite.

**Run** (reins controls cost, time, benchmark, the case ledger):
5. `reins batch open --council C --wp W --type production|rework|experiment|pilot|eval --purpose '...' --cases CASES
   --work-dir /data/councils/C/runs/{batch} --release qa-... --stage prepare --stage plan_crop=V --stage georef=V
   --stage trace=V --stage check=V:paid --time-budget HOURS`, `reins preflight B`, ask the user for the cap
   (`reins batch approve B --cap USD`), then from a release tree `reins run B --self-staged -- tools/run_local_qa.sh EVAL_DIR`.
   The pipeline programs (`[run] programs`) are refused by the hook any other way.
6. What a reins run gives e2e ([run.env]): `E2E_RUN_ID`, `E2E_WORK_DIR`, `E2E_STAGE_VERSIONS`, `E2E_STAGE_HOOK`
   (stage_hook.py: stage boundaries + per-case outcomes), `E2E_CONTAINER_HOOK`, `E2E_RUN_CONTROL`; paid calls go
   through the gateway as plain `*_BASE_URL` / `*_API_KEY`.
7. Intake (`python3 -m e2e_plan_extract.intake ...`) opens its runs through `$E2E_RUN_CONTROL` = `runctl.py` here
   (reins batches of project `intake`); the timers get it from the systemd drop-in (`install_timers.sh`). Cloud chunks
   are registered with `python3 cloud_register.py STATE ...` in the session that owns the batches.
