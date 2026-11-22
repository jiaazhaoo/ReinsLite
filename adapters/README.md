# adapters/

One folder per project that ReinsLite manages from outside: the project's repo carries no reins file, its settings
(`reins.toml` with `repo = "/path/of/the/checkout"`, beside it `rules.toml` / `review.toml`) and the glue that drives
it (stage hooks, gate wrappers, run control) live here. reins finds a project's settings by the repo path from its main
checkout, a candidate worktree or a release tree (`reins/projects.py`); an adapter wins over a reins.toml still inside
the repo. Projects without an adapter keep their reins.toml in the repo, as before.

- `e2e/` -- e2e-plan-extract (since 2026-11-19); `e2e/AGENTS.md` says how to work on it here.
- `readymap/` -- ReadyMap (qa/ + dashboard/; was the web-app project), `s3viewer/`, `labelmap/` (was modeltraining/):
  the repos split out of RMSI-UK/web-app on 2026-11-22 (decision #131). web-app itself is archived.
