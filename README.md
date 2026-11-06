# Mach-E Console

A private, read-only FordConnect Query dashboard for an existing Synology/Docker installation. Python standard library only; no cloud hosting, frontend build, new stack or runtime dependency added by v1.3.0.

Start with [START_HERE.txt](START_HERE.txt) for the existing-installation update and rollback instructions. This release contains source files, not your NAS credentials or history. Its API/data checks used the owner's redacted Ford response; live calls to that owner's account were not made from the development workspace.

## Source layout

- `server.py`: the existing OAuth client, HTTP server, polling, validation, unit conversions, local history and exports. It stays one runtime Python file to preserve existing bind mounts.
- `web/`: the dashboard, local illustration/icons and home-screen manifest.
- `tests/`: offline regression checks and a minimized, redacted version of the supplied response. This fixture is never loaded by the running dashboard.
- `docs/DATA_SUPPORT.md`: what the real response supports, which units were absent, and what remains unverified.
- `FEATURES.md`: the preservation contract for future releases.
- `CHANGELOG.md`: release history.

## Existing deployment

Keep the existing `config.json`, `data/`, Portainer service and compose file. Replace `server.py` and the seven `web/` files together, then restart the existing service. The provided handoff used host-mounted files; if your installation instead copies source into its Docker image, rebuild that existing service using its existing configuration. This release does not include or replace deployment settings.

The database receives one additive `normalized_history` table and an index. Existing `history` and `efficiency_trips` tables retain their original columns and semantics. Tokens are refreshed only by the existing OAuth flow when needed. No new scopes or endpoint permissions are requested. New poll-success state is stored in `data/dashboard-state.json`.

## Verify locally

```sh
python3 -m unittest discover -s tests -v
node --check web/app.js
```

Python 3.10+ with the same timezone support as the original application is suitable. Node is optional for development syntax checks; it is not a runtime dependency. HTTP tests use loopback networking and never call Ford. Test credentials are generated inside a temporary directory. `MACHE_ROOT` exists to point tests at an isolated installation; leave it unset for normal deployment.

Optional browser regression: with Playwright and Chromium available in your development environment, run `python3 tests/browser_smoke.py`. It checks real local HTTP routes, desktop/mobile rendering, sparse/empty data and offline behavior, using mocked Ford requests. Screenshots go to `work/qa/` or the directory specified by `MACHE_QA_OUTPUT`. Neither tool is required on the NAS.

## Data and units

Normalize once on the server: miles, °F, PSI, kWh, V/A/kW and mi/kWh. Every reading includes the Ford metric timestamp, original unit, native-unit default (if used), and availability reason. An unrecognized explicit unit is withheld, rather than displayed under the wrong unit.

The owner's Ford response omitted units. Defaults used by existing FordConnect implementations are labeled, and the owner confirmed that Ford's unitless odometer reading corresponds to about 17,158 miles on the car. The supplied raw value 27,612 km converts to 17,157.30 mi; the confirmation was approximate. `displaySystemOfMeasure=IMPERIAL` describes the vehicle display and is not used as a telemetry-unit declaration.

If a future response needs calibration, the optional private configuration entry below affects only unitless readings; explicit Ford units always take priority:

```json
"native_units": {"odometer": "km", "xevBatteryRange": "km"}
```

This setting is not needed for the supplied vehicle. Unknown charger-energy units intentionally have no default. A 91 kWh capacity value does not establish a state-of-health percentage.

## History and availability

The original seven-day battery/efficiency views are preserved. The selector also offers 30/90/365 days, using only collected data. Legacy battery records retain their dashboard observation timestamps. New battery points use Ford's individual SOC timestamp when available; repeated old readings do not become new points. Raw historical distances are not silently reinterpreted or migrated.

CSV readings contain normalized records from v1.3 onward, per-metric timestamps and unit provenance. They omit VIN, location and credentials. Large exports stream in bounded batches. Historical supported trip measurements remain available through `/api/efficiency.csv` even if the latest response has no trip data. The trip chart appears when the selected period contains supported records.

Optional cards hide if the current response has no usable reading. Reasons remain visible in the data-availability disclosure, and `/api/capabilities` exports the report. All vehicle-data endpoints, exports and the original raw view require the existing dashboard login. Only the non-sensitive illustration/icons and manifest are available on the login screen. A saved token is labeled authorized; a successful recent API check is required for FORD RESPONDING.

## Alerts and mobile

Dashboard alerts cover last-reported low SOC, Ford-reported tire warnings, reported charging errors and HV readings over a day old. Warnings retain their timestamps. Tire pressure below a cold placard does not override Ford's TPMS status or invent a warning threshold. No email, push or other external message is sent.

The dashboard is responsive and includes home-screen icons and a manifest. On an HTTP LAN address, browser installation features vary; full PWA installation generally requires HTTPS. Use the browser's Add to Home Screen/shortcut feature when offered. Vehicle readings are never cached by a service worker.

## Continue as a repository

The accompanying `Mach-E-GT-repository.bundle` holds the uploaded baseline, the v1.3.0 commit and version tags. It excludes all private credentials and live data. Optional Git recovery:

```sh
git clone Mach-E-GT-repository.bundle mache-telemetry-source
cd mache-telemetry-source
git log --oneline --decorate
```

Use a private remote of your choice when ready. Keep `.gitignore`, the regression suite and the feature contract in every future build. No external repository was created or published for this release.
