# Race-day jobs and integration

`python -m jra_engine.race_jobs lock TARGET --workdir PRIVATE_WORK --data-dir PRIVATE_DB`

`python -m jra_engine.race_jobs result TARGET --workdir PRIVATE_WORK --data-dir PRIVATE_DB`

The private work folder contains `races.json` with target_key, cname and timezone-aware start_at. HTML captures, results and logs remain under that folder and are not committed. `JRA_JOB_WORKDIR` and `JRA_DATA_DIR` can supply the paths. The scheduler is preview-only by default; `python -m jra_engine.race_scheduler apply` registers tasks only when the operator explicitly invokes it. Keep the PC running. Existing operator task registrations are not modified by this PR.

The shared lock is `PRIVATE_DB/claude-jobs.lock`. All operators must acquire the same exclusive lock before writing. An old lock is never automatically deleted: inspect the owner/process and resolve it explicitly. DuckDB's own file locking is an additional protection. Result URLs, dates, venue/meeting/day/race number and every horse ID/number are checked before writing any result. Unsupported or missing results fail closed. A 403 is reported after one attempt without bypass or retry.

Results and a model/uniform/odds baseline comparison are saved atomically. Only odds from the frozen prediction are used. Withdrawals skip the probability comparison. Missing result links are an explicit pending state; parsing live official final-result pages still needs race-day verification. Synthetic tests do not establish that the live result-link format is supported.

This PR is stacked on `feature/weekend-local-e2e` (PR #13) and carries the existing batch/readiness/UI work plus the reviewed Claude adapters. It does not modify prediction coefficients or simulation logic, merge main or PR #16, register scheduled tasks, or include databases/raw captures/runtime outputs. The baseline helper already on main is included as a runtime dependency, with nonfinite odds treated as unavailable.
