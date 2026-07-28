# ofd — scheduled agent

You are running `ofd` unattended to pull down everything new from the user's active subscriptions.
The account and session are the user's own, and the job is a personal offline backup of subscriptions
the user pays for. `ofd` reads its settings from `config.json` itself and assumes nothing about the
machine you're on. Your job is to run it once and report the outcome back — new-file count and any
failure — as the result a digest folds in.

**`config.json` is off-limits.** It holds secrets — never open, `cat`, read, or print the file. `ofd`
loads it internally, so you never need its contents. The only field you may need is `output` (for the
volume check below); print *that field alone*, never the whole file:

```
python3 -c "import json; print(json.load(open('config.json'))['output'])"
```

## Clear these while the user is present

An unattended run can only get stuck on preconditions a person has to fix, so confirm both before you
start:

- **Auth** — the user's exported browser session expires over time. Run the no-download verify in
  [`config-setup.md`](config-setup.md) (the snippet that prints `OK - logged in as …`). If it errors
  (401/403, or a validation error on `me`), follow `config-setup.md` to walk the user through
  refreshing `config.json` before you loop.
- **Output** — if `config.json`'s `output` is an external or removable volume, confirm it's mounted
  and writable. ofd creates the output dir if it's missing, so an unmounted target won't error — it
  silently writes to the boot disk and reports success. An empty `output` writes to the repo's
  `downloads/`, which always exists.

## Each run

From the repo root, with the project venv (e.g. `.venv/bin/ofd`):

1. **Run** `ofd`. It reads `config.json` (pass `--output <dir>` only to override) and skips anything
   already downloaded, so you can re-run it safely. If `output` is on a volume, confirm that volume
   is actually mounted before you run — ofd won't, it'll just recreate the path on the boot disk —
   and if it isn't, report `ofd skipped — output volume not mounted` instead of running. ofd already
   retries transient network errors internally (5× per request); if the whole run still crashes on a
   non-auth error (auth failures, defined in step 3, are never retried), re-run it — up to 3 attempts
   total, each resuming where the last left off, and stop as soon as one succeeds.
2. **Read the result.** There's no summary block: a good run opens with `Logged in as <name>`, then
   per creator prints `====== <name>` and `new | existing   N | M` lines per section. What you're
   looking for is whether login succeeded, the **sum of the `new` column** across those lines, and
   any traceback.
3. **Report the outcome back**
   - **Auth failed** — the run crashes before `Logged in as` with a 401/403 or a validation error on
     `me`. The session is stale: report `ofd: refresh config.json — session expired` and stop without
     retrying. Flag it as needing attention so your orchestrator can surface it at the next attended
     startup, where the user re-auths via `config-setup.md`.
   - **Other failure** that survives all 3 attempts — report a one-line summary (with the new count
     so far, if any); the next scheduled run retries cleanly, since downloads are idempotent.
   - **Success** — report `ofd: N new file(s)`, or that nothing was new.

That's it — one run, then stop.
