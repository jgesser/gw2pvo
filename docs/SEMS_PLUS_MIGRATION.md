# SEMS Portal → SEMS+ migration notes

GoodWe is shutting down the legacy SEMS Portal API (`www.semsportal.com`) in
favor of SEMS+ (`semsplus.goodwe.com`). The announced dates keep slipping
(May 2026, then Aug 30 2026 per HA issue #206), and the legacy API is being
dismantled piece by piece instead. This repo depends on the legacy REST API
(`gw2pvo/gw_api.py`) and has broken twice so far:

- 2026-09-24: some endpoints discontinued (see "Current state" below).
- 2026-10-26: `www.semsportal.com` returns nginx 502 for everything (see
  below).

## Incident 2026-10-26: `www.semsportal.com` returns 502

`www.semsportal.com` and `us.semsportal.com` answer every request with an
nginx `502 Bad Gateway`. The regional hosts `eu.`, `au.` and
`hk.semsportal.com` still accept the legacy `v2/Common/CrossLogin`, and any of
them answers with the account's regional API in `api` (for this account:
`https://hk.semsportal.com/api/` — the legacy region is **hk**, even though
the SEMS+ gateway is `us-gateway`).

Fix: `GoodWeApi.login()` tries a list of login hosts in order (`www`, `eu`,
`au`, `hk`) and moves on to the next one on a network/HTTP error. Data calls
always go to the host returned in `api`, never to the login host — a token
issued by `eu` is rejected by `eu`'s own data endpoints (code 100002).

Found along the way:

- A wrong password is answered with code `100005` **and** a non-empty `api`
  field, so the old `'api' not in data` check never detected it (it ended in
  "too many retries"). `login()` now checks `code == 0`.
- Code `100002` (token expired) now triggers a new login, like `100001`.
- The legacy history is still up to date: `GetInverterDataByColumn` for
  2026-10-26 had readings until 18:45 and 48.5 kWh, while SEMS+ said 47.7 kWh.
  So no data loss on the legacy side yet.

What the other integrations did:

- **Home Assistant** (`TimSoethout/goodwe-sems-home-assistant` 11.13.0,
  2026-10-22) reads all data from the SEMS+ Web API. The legacy API is only
  used for inverter power control, with `https://eu.semsportal.com/api` as
  its fallback host. Login modes tried in order: SEMS+ "new" (plain
  cross-login), legacy, SEMS+ "web" (cross-login with browser headers +
  `X-Signature`). For this account the plain SEMS+ cross-login returns code
  `100004`, and only the "web" mode works — which is the one
  `sems_plus_api.py.reference` already uses.
- **openHAB** (`semsportal` binding in `openhab-addons`, no code change
  since 2023) is broken for three reasons: hardcoded `www.semsportal.com`,
  ignores the `api` field from the login, and uses the dead
  `GetMonitorDetailByPowerstationId` (empty data → NPE, openhab-addons issue
  #21786). Nobody has fixed it.

## Current state (fixed, on the legacy API)

- Login goes through the first available host among `www.`, `eu.`, `au.` and
  `hk.semsportal.com`. Data calls go to the regional host returned by the
  login (**not** `semsplus.goodwe.com` — that domain has no working REST v2
  endpoints, confirmed via direct testing).
- `getCurrentReadings()` uses `v3/PowerStation/GetInverterAllPoint` (the old
  `GetMonitorDetailByPowerstationId` is dead, always returns `{}`).
- `getDayPac()` reuses `GetInverterDataByColumn` with `column=Pac` (the old
  `GetPowerStationPacByDayForApp` is dead, always returns an empty `pacs`
  list).
- `getLocation()` (lat/long) has no working replacement — left commented out
  in `gw_api.py`, returns `{}`. Not used by anything today (DarkSky/Netatmo
  temperature lookup isn't configured), so low priority.
- The `HomeAutomationServer` repo's `images/gw2pvo/server.py` `/importPVO`
  endpoint originally returned HTTP 500 on ANY stderr output, including
  benign warnings — this was reverted back to its original behavior rather
  than fixed, since removing the dead `getLocation()` call already stopped
  the warning at the source.

**Docker rebuild gotcha:** the Dockerfile does
`RUN git clone https://github.com/jgesser/gw2pvo /app` — this layer gets
cached by Docker even across `--build`, since the clone command text itself
doesn't change. Use `docker compose build --no-cache gw2pvo` to force
re-cloning after pushing changes to this repo.

## Investigated for the eventual SEMS+ migration

There is no official public API/docs for SEMS+. Official API access requires
an NDA through a GoodWe sales rep (per community.goodwe.com).

- `github.com/TimSoethout/goodwe-sems-home-assistant` issue #184 documents
  MQTT/WebSocket endpoints
  (`wss://netty-wss-<region>.iot.goodwe-power.com:8885/mqtt`) but no working
  implementation was found there.
- `github.com/ashwhall/ha-sems-plus-addon` is a Playwright-based
  headless-browser scraper (logs into the SEMS+ web UI, reads values from the
  DOM via CSS selectors). Fragile (breaks on GoodWe frontend redeploys), but
  functional as a last resort.
- `github.com/aroundmyroom/gw2pvo` (fork, updated Aug 2026) has a
  `sems_plus_api.py` that hits the **real SEMS+ REST API** (not scraping),
  reverse-engineered from browser traffic, credited to
  `github.com/timvanderHorst/goodwe-semsplus`. This is the best lead — see
  below.

## A working SEMS+ REST client exists (built, tested, then reverted)

On 2026-09-25 the fork's `sems_plus_api.py` was ported into this repo and two
bugs in it were fixed:

1. **Hardcoded EU gateway.** The fork hardcodes `eu-gateway.semsportal.com`,
   which fails for non-EU accounts (`code: C0602`,
   `account_login_abnormal`). The login response's `data.api` field gives the
   correct regional gateway (e.g. `us-gateway.semsportal.com` for a Brazil
   account) — must be read dynamically and stored, not hardcoded.
2. **Eager URL construction.** The fork builds request URLs as plain strings
   before the first `authenticate()` call. Since the region is only known
   *after* login, and a mid-call re-auth (on expired token, code `C0602`)
   reruns `authenticate()` but reuses the already-built URL string, calls kept
   silently retrying against the wrong/default gateway forever. Fix: pass a
   callable (`path_builder`) into the request helper, evaluated fresh on every
   retry attempt, not a pre-formatted string.
3. **Broken grid-voltage extraction.** The fork checks
   `code.endswith(':Vac')`, but the real telemetry factor code is the bare
   string `'Vac'` (no `MPPT-n:` prefix, since it's a whole-inverter value, not
   per-string) — so grid voltage always came back `0`. Also added per-MPPT
   power extraction from `MPPT-<n>:Ppv` telemetry factors (already in kW,
   converted to W) into the `powers` result field — more accurate than the
   legacy client's manual V×A calculation.

The fixed, tested version is saved as [`sems_plus_api.py.reference`](sems_plus_api.py.reference)
in this same `docs/` folder. It was verified end-to-end on 2026-09-25 against
the real SEMS+ API with real credentials: login, current readings, 3-MPPT
power breakdown, grid voltage, and lat/long all came back correct.

### Key SEMS+ REST protocol details

Login happens on `semsplus.goodwe.com`; every other call goes to the regional
gateway returned by the login response.

- **Login**: `POST https://semsplus.goodwe.com/web/sems/sems-user/api/v1/auth/cross-login`
  Body: `{account, pwd: base64(md5hex(password)), agreement: 1, isChinese: false, isLocal: false}`
  Response `data.api` gives the regional gateway base URL to use for
  everything else (e.g. `https://us-gateway.semsportal.com/web/sems`).
- **Every other call** needs:
  - Header `token`: the JSON blob from the login response's `data`.
  - Header `X-Signature`: `base64( sha256(f"{timestamp_ms}@{uid}@{token}") + "@" + timestamp_ms )`
- `GET  {gateway}/sems-plant/api/stations/flow?stationId=<id>` — current
  status/power (`pAc` in kW, `status` as `"online"`/`"offline"`/numeric code).
- `POST {gateway}/sems-plant/api/portal/stations/basic/info?stationId=<id>` —
  lat/long, address, install date.
- `POST {gateway}/sems-plant/api/stations/production` with
  `{stationId, items: ["proSystemTotalStats"], dimension, startTime, endTime}`
  — cumulative energy (today: `dimension: "day"` + today's date range;
  lifetime: `dimension: "year"` + epoch-to-now range).
- `GET  {gateway}/sems-plant/api/stations/device/all-status?stationId=<id>` —
  lists inverter serial numbers.
- `GET  {gateway}/sems-plant/api/equipments/<sn>/telemetry?deviceType=INVERTER&pwId=<stationId>` —
  per-inverter detail: `Vac` (grid voltage), `MPPT-<n>:Vpv`/`:Ipv`/`:Ppv`
  (per-string voltage/current/power), temperature, etc.
- `POST {gateway}/sems-plant/api/stations/statistics` with
  `{stationId, items: ["proSystemTotalStats"], dimension: "day", startTime, endTime}`
  — **daily totals per date**, past days included
  (`dataList[].statisticsList[] = {date, val}`, in kWh). Found in the HA
  integration. Checked 2026-10-26: 2026-10-25 → 14.1 kWh, same as the legacy
  API.
- **No intraday (5-minute) history endpoint was found.** Neither this client
  nor the HA integration has one: HA only uses the daily totals above.
  `--date` import is NOT supported by this client — it deliberately raises an
  exception explaining to use the legacy client instead.

### Known gaps (confirmed 2026-09-25, not worth fixing yet for a single-inverter setup)

1. `getDayReadings()` / `--date` raises on purpose (see above). `/importPVO`
   and `copy()` in `__main__.py` only work with the legacy client.
2. Multi-MPPT power works correctly (tested with this station's 3 MPPTs:
   `[1188.3, 992.3, 476.4, 2657.0]` W, matching `pgrid_w` closely).
   Multi-*inverter* (multiple physical units, not multiple MPPTs) is **not**
   validated — `_addVoltagesFromTelemetry` sums MPPT power by index across
   *all* inverters into one shared dict, which would silently merge readings
   from different physical inverters if there were more than one connected.
   This is the same limitation the legacy `gw_api.py` client has, so not a
   regression — just never exercised against real multi-inverter hardware.

### Re-verified 2026-10-26

`sems_plus_api.py.reference` still works unchanged against the live API:
login (gateway `https://us-gateway.semsportal.com/web/sems`), today's energy,
grid voltage, PV voltage and lat/long all came back. Two new gaps showed up,
at night (inverter off):

3. `status` came back as `Unknown` instead of `Offline`. `statusText()`
   doesn't know the value `stations/flow` returns for an offline inverter.
   This matters: `--skip-offline` and the CSV export compare against
   `'Offline'`. Log the raw `status` at night and add it to the mapping.
4. `etotal_kwh` doesn't match: SEMS+ said 49001.1 kWh, the legacy API
   47579.8 kWh (~1400 kWh, about a month of production). Not uploaded to
   PVOutput, so harmless for now, but it's worth finding out which one is
   right (HA issue #248 reports wrong SEMS+ lifetime totals with the `year`
   dimension).

## Status: reverted, kept for reference

As of 2026-09-25 this integration work was **not** carried forward into the
live code — the owner has a single inverter, still needs `--date` historical
import (only supported by the legacy client), and the legacy `sems` client
still works fine day-to-day. The finished, tested `sems_plus_api.py` is kept
in this repo only as [`sems_plus_api.py.reference`](sems_plus_api.py.reference)
for future reuse, not wired into `gw2pvo`.

### How to resume this work

1. Copy `docs/sems_plus_api.py.reference` to `gw2pvo/sems_plus_api.py`.
2. In `gw2pvo/__main__.py`:
   - Add `from gw2pvo import sems_plus_api`.
   - Add a `get_gw_api(settings)` function:
     ```python
     def get_gw_api(settings):
         if getattr(settings, 'gw_api', 'sems') == 'semsplus':
             return sems_plus_api.SemsPlusApi(settings.gw_station_id, settings.gw_inverter_id, settings.gw_account, settings.gw_password)
         return gw_api.GoodWeApi(settings.gw_station_id, settings.gw_inverter_id, settings.gw_account, settings.gw_password)
     ```
   - Replace the `gw_api.GoodWeApi(...)` construction in both `run_once()` and
     `copy()` with `get_gw_api(settings)`.
   - Add the CLI flag:
     `parser.add_argument("--gw-api", help="GoodWe portal to use (default sems)", choices=['sems', 'semsplus'], default='sems')`
3. Re-verify against the live API before trusting it again — GoodWe could
   change the SEMS+ protocol again by the time this is revisited.
4. Fix the two night-time gaps found on 2026-10-26 (`Unknown` status,
   `etotal_kwh` mismatch).
5. Plan `--date` / `copy()` on SEMS+. Options, best first:
   - Upload only the day's total with PVOutput `addoutput.jsp`, taken from
     `stations/statistics` (daily totals, past days included). Loses the
     5-minute curve, but keeps the daily energy in PVOutput correct.
   - Look for an intraday history endpoint in the SEMS+ web UI's browser
     traffic (the station's power chart has to come from somewhere).
   - Playwright scraping per `ashwhall/ha-sems-plus-addon`, as a last
     resort.

### When to switch the default to `semsplus`

Run both clients side by side before that becomes urgent. Switch when any of
these happens:

- `login()` fails on every legacy host (`Failed to login on GoodWe API (no
  login host available)` in the logs).
- The legacy data stops updating, i.e. `getCurrentReadings()` /
  `GetInverterDataByColumn` lag behind what SEMS+ shows for the same day.
- Another legacy endpoint we depend on (`GetInverterAllPoint`,
  `GetInverterDataByColumn`, `GetPowerStationPowerAndIncomeByDay`) starts
  returning empty data.
