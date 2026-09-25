# SEMS Portal → SEMS+ migration notes

GoodWe is shutting down the legacy SEMS Portal API (`www.semsportal.com`) in
favor of SEMS+ (`semsplus.goodwe.com`), reportedly around May 2026. This repo
depends on the legacy REST API (`gw2pvo/gw_api.py`) and broke once some of its
endpoints were discontinued (incident: 2026-09-24).

## Current state (fixed, on the legacy API)

- Base URL is `https://www.semsportal.com/api/` (**not** `semsplus.goodwe.com`
  — that domain has no working REST v2 endpoints, confirmed via direct
  testing).
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
- **No historical per-day breakdown endpoint was found.** `--date` import is
  NOT supported by this client — it deliberately raises an exception
  explaining to use the legacy client instead.

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
4. Reconsider switching the default to `semsplus` once the legacy portal is
   fully shut down (~May 2026) or starts flaking further. At that point,
   `--date` historical import will need either a newly-discovered SEMS+
   endpoint, or falling back to Playwright scraping per
   `ashwhall/ha-sems-plus-addon`.
