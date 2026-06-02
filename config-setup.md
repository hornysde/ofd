# Create `config.json` — agent skill (Chrome DevTools MCP)

Produce a flat JSON file at repo root matching `config.json.tpl`. Secret-sensitive: git-ignored, don't commit or print the values.

## Fields

- `cookie` (req) — must contain `auth_id` and `sess`, e.g. `auth_id=123; sess=abc`
- `x_bc` (req) — the `x-bc` request header
- `user_agent` (req) — the browser UA; must match the session that produced the cookie
- `client_id_path`, `private_key_path` (opt) — Widevine L3 device files for DRM video; `""` to skip

All three required values ride on every `https://onlyfans.com/api2/v2/…` request — read one request and you have them all.

## Flow (chrome-devtools MCP)

> If another session already holds chrome-devtools' default profile, `new_page` fails with `browser is already running for …/chrome-profile`. Fix: register a dedicated server with its own profile (local scope keeps it out of the repo), then restart so it loads (tools become `mcp__chrome-devtools-ofd__*`):
> ```
> claude mcp add chrome-devtools-ofd --scope local -- npx chrome-devtools-mcp@latest --user-data-dir=/ABS/PATH/chrome-profile-ofd
> ```

1. `new_page` → `https://onlyfans.com/`.
2. `take_snapshot`. If it shows the login form, ask the user to log in in that browser window and wait for confirmation. (A fresh profile starts logged-out; the login persists in the profile for future runs.)
3. `navigate_page` type=`reload` (fires the authenticated `api2/v2` calls).
4. `list_network_requests` with resourceTypes=`["xhr","fetch"]`; pick `…/api2/v2/users/me` (status 200).
5. `get_network_request` with that reqid. From **Request Headers** take `x-bc`, `user-agent`, and pull `auth_id` + `sess` out of `cookie`.
6. Write `config.json`. Cookie can be minimal: `auth_id=…; sess=…`. Preserve existing `client_id_path` / `private_key_path` if the file already has them.
7. Verify (no downloads):
   ```bash
   .venv/bin/python -c "
   import asyncio
   from ofd import OnlyFans, AuthInfo, SignInfo
   async def check():
       auth = AuthInfo.model_validate_json(open('config.json').read())
       sign = await SignInfo.from_download()
       async with OnlyFans(auth=auth, sign=sign) as api:
           print(f'OK - logged in as {api.me.name}')
   asyncio.run(check())
   "
   ```
   Auth error (401/403 or validation error on `me`) → values stale/mismatched, redo from step 3. `ModuleNotFoundError: ofd` → install first (see `readme.md`).

Manual fallback (no MCP): DevTools → Network → copy `cookie` / `x-bc` / `user-agent` from any `api2/v2` request.
