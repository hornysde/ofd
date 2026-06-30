# ofd — scheduled agent

Runs `ofd` unattended to pull down everything new from your active subscriptions. It reads everything
from `config.json` and assumes nothing about your machine. A full download is heavy and can run long,
so dispatch each run as a background `Agent` (`run_in_background: true`) and have it report its
outcome back — new-file count and any failure — as the result a digest folds in.

## Clear these while the user is present

The unattended run can only get stuck on preconditions a person has to fix, so confirm both at startup:

- **Auth** — the borrowed OnlyFans session expires over time. Run the no-download verify in
  [`config-setup.md`](config-setup.md) (the snippet that prints `OK - logged in as …`). If it errors
  (401/403, or a validation error on `me`), follow `config-setup.md` to guide the user through
  refreshing `config.json` before looping.
- **Output** — if `config.json`'s `output` is an external or removable volume, confirm it's mounted
  and writable. ofd creates the output dir if it's missing, so an unmounted target doesn't error — it
  silently writes to the boot disk and reports success. An empty `output` writes to the repo's
  `downloads/`, which always exists.

## Each run

From the repo root, with the project venv (e.g. `.venv/bin/ofd`):

1. **Run** `ofd`. It reads `config.json` (pass `--output <dir>` only to override) and skips anything
   already downloaded, so re-runs are safe. If `output` is on a volume, confirm that volume is
   actually mounted first — ofd won't, it'll just recreate the path on the boot disk — and if it
   isn't, skip with `ofd skipped — output volume not mounted` instead of running. ofd already retries
   transient network errors internally (5× per request); if the whole run still crashes on a non-auth
   error (auth failures, defined in step 3, are never retried), re-run it — up to 3 attempts total,
   each resuming where the last left off, stopping as soon as one succeeds.
2. **Read the result.** There's no summary block: a good run opens with `Logged in as <name>`, then
   per creator prints `====== <name>` and `new | existing   N | M` lines per section. What matters is
   whether login succeeded, the **sum of the `new` column** across those lines, and any traceback.
3. **Report the outcome back**
   - **Auth failed** — the run crashes before `Logged in as` with a 401/403 or a validation error on
     `me`. The session is stale: report `ofd: refresh config.json — session expired` and stop without
     retrying. Flag it as needing attention so the orchestrator can surface it at the next attended
     startup, where the user re-auths via `config-setup.md`.
   - **Other failure** that survives all 3 attempts — report a one-line summary (with the new count
     so far, if any); the next scheduled run retries cleanly, since downloads are idempotent.
   - **Success** — report `ofd: N new file(s)`, or that nothing was new.

That's it — one run, then stop.
