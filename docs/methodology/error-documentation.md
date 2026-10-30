# Error Documentation

> Memory Bank file. Non-trivial errors found during development: symptom, root cause, fix and how to prevent it. API error codes exposed to clients are in `docs/api.md` §4.

| Phase | Symptom | Root cause | Fix | Prevention |
| --- | --- | --- | --- | --- |
| F0 | Ruff in the container: `Failed to initialize cache at /app/.ruff_cache: Permission denied` | `WORKDIR /app` is created by root; the non-root user could not write caches | `chown app:app /app` in the Dockerfile base stage | Run the image as non-root from the first build |
| F0 | CI lint and test jobs failed before running: `unable to find version v10` | `astral-sh/setup-uv` publishes no floating major tag | Pin the exact tag `v10.3.3` | Pin actions to exact versions |
| F1 | DEP-2 would break as soon as `core` imported any service | import-linter `forbidden` contracts check indirect imports (`core → app.services → app.models`) | `allow_indirect_imports = true` on DEP-2 | A deliberate violation in a probe file confirms the contract still catches direct imports |
| F1 | `make format` changed nothing on the host | The app service has no bind mount; ruff formatted the copy inside the image | Mount `./backend` and run as the host user for `format` only | Check generated files on the host after tooling targets |
| F2 | Merge conflicts when integrating `main` into F2 | F2 was branched from the F1 head before F1 was squash-merged; squash commits are not ancestors | Kept F2's version (a superset) and checked the merge introduced no diff | Branch each phase from `main` only after the previous phase is merged |
| F4 | `make up`: API container exited with `Error: Unable to configure root logger` | gunicorn merges `logconfig_dict` over its defaults; the default root logger referenced a `console` handler that was replaced | Redefine `root` (and gunicorn loggers) in `gunicorn.conf.py` | `make test` never starts gunicorn: always run `make up` + walkthrough at the gate (now also in CI) |
