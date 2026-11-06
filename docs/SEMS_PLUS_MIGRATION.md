# SEMS Portal → SEMS+ migration notes

GoodWe is replacing the SEMS Portal (`www.semsportal.com`) with SEMS+
(`semsplus.goodwe.com`). Neither has a public API; both are reverse engineered.
The announced shutdown dates keep slipping (May 2026, then Aug 30 2026 per HA
issue #206): the legacy API is being dismantled piece by piece instead.

## Current state (2026-10-30)

| What | Client | API |
|---|---|---|
| Live readings, every `--pvo-interval` (`run_once`) | `sems_plus_api.SemsPlusApi` | SEMS+ |
| History, `--date` (`copy`, and `/importPVO` of HomeAutomationServer) | `gw_api.GoodWeApi` | legacy |

History stays on the legacy API because no SEMS+ endpoint gives the 5 minute
power curve (see "Open points").

The API for live readings is a setting: `--gw-api semsplus` (default) or
`--gw-api sems`, or `gw_api = sems` in the `[Defaults]` of the config file.
The SEMS+ client type is `--gw-client` (default `semsPlusWeb`) or `gw_client` in
the config file; see "Sessions" below for why the server sets `web`.
Both clients have the same `getCurrentReadings()`. Going back to `sems` only
makes sense if SEMS+ breaks while the legacy real-time data works again: today
it reports the inverter as Offline.

The SEMS+ client is created once and kept for the whole run: it logs in on the
first call and again only when the server says the session expired. Per
interval it makes:

1. `device/all-status`: serial number and status of each inverter.
2. `telecounting` per inverter: energy today and energy total.
3. `telemetry` per **online** inverter: grid voltage, power and voltage per MPPT.

`SemsPlusApi.getCurrentReadings()` returns the same dict as the legacy client,
so `run_once`, the CSV and PVOutput code did not change. Latitude and longitude
are `None` (only DarkSky/Netatmo use them, and they are not configured).

## What went wrong, in order

- **2026-09-23/24**: the legacy endpoints `GetMonitorDetailByPowerstationId`
  (current readings, location) and `GetPowerStationPacByDayForApp` (power
  curve) started answering `code 0` with empty data. The legacy client moved to
  `v3/PowerStation/GetInverterAllPoint` and `GetInverterDataByColumn`.
- **2026-10-26**: `www.semsportal.com` and `us.semsportal.com` answered every
  request with an nginx `502`. The regional hosts `eu.`, `au.` and `hk.` still
  accepted the legacy `v2/Common/CrossLogin` and answer with the account's
  regional API in `api`. The legacy client now tries `www`, `eu`, `au`, `hk` in
  order, and sends every other call to the `api` host (a token issued by `eu` is
  rejected by `eu`'s own data endpoints: code 100002). A wrong password also
  comes with an `api` field (code 100005), so success must be checked with
  `code == 0`. `www` answered again on 2026-10-30.
- **2026-10-28 ~19:00**: the legacy *real-time* data froze: `GetInverterAllPoint`
  reports `status -1`, 0 W and a `last_refresh_time` of 10/28 18:57, while SEMS+
  shows the inverter Normal and producing. The legacy *history*
  (`GetInverterDataByColumn`) keeps updating, about an hour behind. Result: the
  live loop saw "Offline" and uploaded nothing (`skip_offline`), and the openHAB
  binding showed "Station not operational". PVOutput only got data through
  `/importPVO`. This is why live readings moved to SEMS+.

The legacy `hk` region is a stale replica for this account (last refresh in
August, total 47579.8 kWh against the real 49150 kWh). If `www` goes down again,
the `eu` fallback logs in but lands there.

## SEMS+ protocol (what the client uses)

Login is on `semsplus.goodwe.com`. Every other call goes to the regional gateway
from the login response (`data.api`, here `https://us-gateway.semsportal.com/web/sems`).

- **Login**: `POST https://semsplus.goodwe.com/web/sems/sems-user/api/v1/auth/cross-login`
  with `{account, pwd: base64(md5hex(password)), agreement: 1, isChinese: false, isLocal: false}`.
  Browser-like headers (`User-Agent`, `Origin`, `Referer`) are required: without
  them this account gets code `100004`.
- **Every call** (login included) has the headers `token` (the JSON from the
  login `data`, with `client` set to `semsPlusWeb`) and `X-Signature` =
  `base64(sha256hex("<ms>@<uid>@<token>") + "@" + <ms>)`. Login signs with an
  empty token.
- Success is code `00000`. An expired or replaced token is `C0602` (SEMS+) or
  `100002`; the client logs in again and repeats the call once.
- `GET  /sems-plant/api/stations/device/all-status?stationId=` →
  `deviceDetailList[].statusDetailList[]` with `status` and `snList`. Status
  codes (from the HA integration): -1 Offline, 0 Waiting, 1 Normal, 2 Fault,
  3 Waiting, 5 Normal. Unknown codes are logged as a warning.
- `GET  /sems-plant/api/equipments/<sn>/telecounting?deviceType=INVERTER&pwId=` →
  factors `proPvStatsToday` and `proPvStatsTotal` (kWh), also `pAc` (kW).
- `GET  /sems-plant/api/equipments/<sn>/telemetry?deviceType=INVERTER&pwId=` →
  factors `pAc` (kW), `Vac`, `MPPT-<n>:Ppv` (kW), `MPPT-<n>:Vpv`, `MPPT-<n>:Ipv`.
  Each factor is `{code, data, unit}`; `data` can be missing or null.
- Other endpoints seen in the HA integration and checked on 2026-10-26 and 30:
  `stations/flow` (station power and status, kW), `stations/production` and
  `stations/statistics` (`proSystemTotalStats`, per day/year; today's value and
  the lifetime total match telecounting).
- **Sessions** (tested 2026-10-30): a new login invalidates the previous token
  of the same API **and the same client type** only.
  - Legacy and SEMS+ logins do not affect each other.
  - The client type is the `client` of the `token` header of the login. The server
    only accepts `semsPlusWeb`, `web`, `ios` and `android` (case sensitive). Any other value
    is refused: `gw2pvo`, `openhab`, `foo`, `h5`, `pc`, empty and `semsPlusApp` give code
    `100004`; `Web`, `IOS` and `semsplusweb` give `100000`. There may be other valid values.
  - A `web` session stayed valid for 6 minutes of polling every 15 s while a second program
    logged in as `semsPlusWeb` (twice: mine, then the production gw2pvo), while the two
    `semsPlusWeb` sessions replaced each other. Tokens of `web`, `ios` and `android` read all the
    endpoints above with the same gateway and values as `semsPlusWeb`.
  - So two programs using one account need different client types. `ios` and `android` are the
    types of the phone apps: logging in with them logs the app out. `semsPlusWeb` is the type of
    the SEMS+ web app, also used by the Home Assistant integration (fixed there, not configurable):
    opening semsplus.goodwe.com in a browser and logging in logs out a program using it, and the
    reverse. `web` is the only one without an obvious app, so gw2pvo uses it here.

## Other integrations

- **Home Assistant** (`TimSoethout/goodwe-sems-home-assistant`, 11.13.0
  2026-10-22) reads everything from SEMS+; the legacy API is only used for
  inverter control. It also has rate limit handling (`GY0429`), which this
  client does not need at one poll per 5 minutes.
- **`timvanderHorst/goodwe-semsplus`**: small "pure requests" SEMS+ client, the
  best reference for the login and signature.
- **openHAB** `semsportal` binding: reads SEMS+ since it was merged into
  openhab-addons (PR #22184, 2026-11-06). Its endpoints, channel mapping and the
  legacy API notes are in `SEMS_API_NOTES.md` (same folder).
- `ashwhall/ha-sems-plus-addon`: Playwright scraper of the SEMS+ web UI. Fragile,
  a last resort.

## Open points

- **`--date` / `/importPVO` still use the legacy API.** If its history stops,
  options in order: upload the day's total from `stations/statistics`
  (`dimension: day`, past days included) with PVOutput `addoutput.jsp`, losing
  the curve; look for an intraday endpoint in the SEMS+ web UI's browser
  traffic; Playwright scraping.
- The night-time status code of SEMS+ is not verified (all tests ran while the
  inverter was producing). If a status other than the table above shows up, the
  warning `Unknown SEMS+ inverter status` names it.
- **Dawn**: around 05:15 the inverter is `Waiting` (0 W) for 20 to 30 minutes before it produces. The
  classic API called that phase `Offline`, so `skip_offline` skipped it; with SEMS+ points with 0 W and 0 Wh
  and 0 V are uploaded from 05:15 (seen on PVOutput 2026-10-31 and 2026-11-01). Accepted as is. Skipping
  `Waiting` too would restore the old curve start. After sunset SEMS+ keeps reporting the last voltage
  (220.2 V grid, 388.5 V PV seen at 19:40 on 2026-10-26, with 0 W), which is why telemetry is only read
  while the inverter is `Normal`.
- Several inverters: power, energy and totals are summed, but voltage and
  MPPT powers are those of the first online inverter. Never tested with more
  than one inverter.
- `docs/sems_plus_api.py.reference` is the earlier client (from the
  `aroundmyroom/gw2pvo` fork). It is superseded by `gw2pvo/sems_plus_api.py`
  and can be deleted.

## Operations

- **Docker rebuild**: the HomeAutomationServer Dockerfile does
  `RUN git clone https://github.com/jgesser/gw2pvo /app`, and Docker caches that
  layer. After pushing here, use `docker compose build --no-cache gw2pvo`.
- **`/importPVO` returns HTTP 500 on any stderr output**, even when the import
  worked, and gw2pvo logs at `info` to stderr. So the legacy client logs a
  failing login host at `debug` only.
- Tests: `python -m unittest tests.test_sems_plus_api` from the repository
  root (no network, no extra packages).
