# Mach-E Console

[Fresh installation](#what-you-need) · [Existing-installation update](START_HERE.txt) · [Troubleshooting](#troubleshooting) · [Development](#development-and-data-handling)

## Current release: v1.3.0

This upgrade restores all four tire pressures in PSI and keeps miles and Fahrenheit consistent through server-side conversion. It adds separate 12V/HV readings, supported charger and vehicle-status readings, per-metric timestamps, 7/30/90/365-day history, normalized CSV exports, on-dashboard alerts, and a Glacier Grey GT illustration.

Optional cards appear only when Ford returns usable values. Battery-health percentage, target charge, and cost/session totals are not invented. The trip-efficiency chart and stored history are preserved; the chart appears when supported trip records exist in the selected period.

For an existing installation, follow [START_HERE.txt](START_HERE.txt). Keep your private `config.json`, `data/`, tokens, history and current stack settings. Replace `server.py` and all seven files in `web/`, then restart the existing container and hard-refresh the browser. The database update is additive.

See [CHANGELOG.md](CHANGELOG.md), [FEATURES.md](FEATURES.md), [data support](docs/DATA_SUPPORT.md), and [validation](docs/VALIDATION.md). The release was validated against an owner-supplied Ford response; direct live-account calls were not made from the development workspace.

Developers can run `python3 -m unittest discover -s tests -v` and `node --check web/app.js`. Node is only a development check, not a dashboard runtime dependency. The fixture under `tests/fixtures/` contains redacted measurements without VIN, vehicle IDs or GPS and is never loaded by the running dashboard.

A private, **read-only** web dashboard for Ford's FordConnect Query API. Tested by one 2025 Mustang Mach-E GT owner in North America using a Synology NAS and Portainer. Other vehicles, regions, accounts, firmware and API responses may behave differently. This project is independent and is **not affiliated with or supported by Ford**.

**What it can show (when Ford returns data):** battery state of charge (SOC), estimated range, odometer, nominal/reported battery capacity, battery/outside temperature, plug/charge status, tire pressures (PSI), tire-pressure status and placard pressure, 12V voltage, high-voltage battery performance status, door/lock/hood/ignition status, and some onboard charging details. It saves historical polling snapshots in SQLite and shows the age of several readings.

**What it cannot yet show:** a *verified high-voltage battery State of Health (SOH) percentage* on the tested 2025 Mach-E. The API returned `xevBatteryCapacity: 91` kWh, but that is **not proof of 100% battery health**. The SOH card is hidden when Ford supplies no usable reading; the data-availability disclosure explains why.

## Important security rules

- Keep this on **a trusted home network only**. The server uses **HTTP**, not HTTPS. Do not port-forward TCP 8765 or expose it to the internet. Do not use an untrusted/public Wi-Fi network.
- Your Ford developer **Client Secret is a credential**: keep it private; don't upload `config.json` or your `data/` directory to GitHub or paste them on Reddit. Never share authorization-code callback URLs, tokens, or raw vehicle data, which may contain the VIN and precise GPS coordinates.
- The source package includes **no personal Ford credentials or vehicle history**. Copy the example config and enter your own values privately.
- OAuth permissions are granted via Ford's website. Check the requested sharing scope before approving. The dashboard only calls read-only FordConnect endpoints.
- This is experimental community software, not a hardened internet-facing service or guaranteed accurate real-time diagnostic instrument. TPMS readings can lag; confirm with a cold-tire gauge and the physical door-jamb label.

## What you need

1. A Ford vehicle registered in the Ford app (FordPass) with connected services enabled; telemetry availability varies.
2. A **Ford Developer** account/application approved for the `fordconnect` API, with its Client ID and Client Secret. Your Ford Developer account and your everyday Ford app login **can use different email addresses**.
3. A Synology NAS with Docker/Container Manager **and Portainer** already running, or a comparable Docker host. These steps assume the Synology shared folder is `/volume1/docker/`.
4. A computer on the same LAN with a browser and a text editor. No Home Assistant or HACS is necessary.

## Step 1 — Register the Ford callback address

1. Sign into [Ford Developer](https://developer.ford.com/) and open your approved FordConnect application.
2. Find **View OAuth URLs and Modify Redirect URIs** > **Account Linking Info** (labels may vary).
3. Add or set the application's redirect/callback URI to this **exact value**:

   `http://localhost:8080/callback`

4. Save it. If Ford refuses it, **stop**; this sample manual authorization flow cannot proceed without a Ford-accepted redirect. Don't change it to a random URL. In the tested setup Ford accepted this callback.

`localhost` refers to **the computer whose browser you'll use**, not the NAS. Later, after consent, your browser will show a **connection-refused error on purpose**. You'll copy the resulting address-bar URL back to your private dashboard, where the app validates the OAuth state and exchanges the code for tokens.

## Step 2 — Copy files to the NAS

1. Download and extract the ZIP locally.
2. In **Synology DSM > File Station**, go into the `docker` shared folder.
3. Create a folder named `mache-telemetry` and enter it.
4. Upload `server.py`, `config.example.json`, and the **entire `web` folder** from the extracted package. You may also upload `docker-compose.yml` and this README for reference.
5. In `mache-telemetry`, create an **empty** folder named `data`.

Result:

```text
/volume1/docker/mache-telemetry/
  server.py
  config.example.json
  docker-compose.yml          (optional on NAS)
  web/
    index.html
    app.js
    style.css
    mache-gt.png
    icon-192.png
    icon-512.png
    manifest.webmanifest
  data/
```

Don't accidentally nest everything inside an extra ZIP-extraction folder. `server.py` belongs **directly** in `mache-telemetry`.

## Step 3 — Create your private `config.json`

1. Make a copy of `config.example.json` in the same NAS folder and rename the **copy** to `config.json`.
2. Open `config.json` in Synology Text Editor or download/edit it in Notepad++, then upload it back, choosing **Overwrite**.
3. Replace the three example strings with your Ford developer application's actual `client_id`, its corresponding `client_secret`, and a new **unique dashboard password at least 12 characters long**. The Ford Secret should be entered as **only its value**—not the enclosing `{"secret":"..."}` wrapper sometimes shown by the portal.
4. Leave the redirect URI as `http://localhost:8080/callback` and the polling interval at `180` seconds unless you know what you're changing.

**Example structure (these are placeholders, not working credentials):**

```json
{
  "client_id": "YOUR_FORD_DEVELOPER_CLIENT_ID",
  "client_secret": "YOUR_NEW_FORD_DEVELOPER_SECRET",
  "dashboard_password": "A_UNIQUE_PASSWORD_AT_LEAST_12_CHARACTERS",
  "redirect_uri": "http://localhost:8080/callback",
  "poll_seconds": 180
}
```

**JSON is picky.** Each line except the last entry must end with a comma; every string needs a single pair of double quotes; no extra text after the value. Use a JSON validator **only with placeholders/redacted secrets**, never by submitting your real config to a website. Avoid screenshotting your complete config.

## Step 4 — Deploy with Portainer

1. Open **Portainer** > your local Docker environment > **Stacks** > **Add stack**.
2. Name the stack `mache-telemetry`.
3. Select **Web editor**.
4. Open the bundled `docker-compose.yml` in a text editor and paste its full contents into Portainer, or use the equivalent below:

```yaml
services:
  mache-telemetry:
    image: python:3.12-alpine
    container_name: mache-telemetry
    restart: unless-stopped
    working_dir: /app
    command: ["python", "/app/server.py"]
    ports:
      - "8765:8765"
    volumes:
      - /volume1/docker/mache-telemetry:/app
    environment:
      TZ: America/New_York
```

5. Click **Deploy the stack**. The container should start. The timezone is an example; change to your local IANA timezone if desired.

For other Docker hosts, **replace the left side of the volume mapping** with your real host folder path.

## Step 5 — Open the dashboard

1. Find your NAS's **local IP address** (DSM > Control Panel > Network, or wherever you normally access DSM).
2. In a browser on your home network open `http://YOUR-NAS-IP:8765` (e.g. `http://192.168.1.50:8765`). This is the **NAS's address**, not `localhost`.
3. Unlock the dashboard with the **dashboard password you entered in `config.json`**. This is neither your FordPass password nor your Ford Developer password.
4. Until readings are available, the dashboard shows the connection state and hides unsupported metric cards.

## Step 6 — Authorize with Ford

1. Scroll down to **Ford API Connection** and expand **Ford authorization settings**.
2. Click **Start Ford authorization**.
3. On Ford's official page, sign in with the **regular Ford app/FordPass account that owns or has access to the vehicle**—**not** the Ford Developer login (unless they're the same account).
4. Select your vehicle, review the requested data-sharing permissions, and click **Authorize** if you agree.
5. Your browser will probably show `This site can't be reached` at `http://localhost:8080/callback?state=...&code=...`. **This is expected for this manual flow.**
6. Copy the **entire address-bar URL** with Ctrl+A and Ctrl+C. **Do not post it anywhere.**
7. Return to your dashboard tab, paste the URL into the large input under **Ford authorization settings**, and click **Exchange code for token** promptly (codes are single-use and expire).
8. After a successful token exchange, click **Fetch latest telemetry**. The badge says **FORD RESPONDING** only after a successful recent API check; a saved token alone does not establish a working connection. The background service also polls periodically.

## Step 7 — Reading your results

- Ford's telemetry distances are often in **kilometers with no unit label**. The server uses a labeled native-km default when Ford omits a unit and converts compatible distances to miles; an explicit Ford unit takes priority. Temperatures similarly default to degrees Celsius, converted to °F. Those assumptions were checked against one US 2025 Mach-E; verify if your results look wrong.
- Tire pressures are treated as **kPa when the API supplies no units** and displayed as PSI. Ford may report delayed values. A sensor status of `NORMAL` does not mean pressure equals the recommended cold setting.
- The **Battery Capacity** card displays kWh. It is **not a battery health percentage**.
- **Battery Health** is hidden if Ford doesn't return a usable explicit SOH metric. Do not interpret the factory 91 kWh figure as 100% battery health.
- Sensor **Last Reported** ages may differ; a dashboard sync doesn't imply every ECU sensor was refreshed.
- History is stored in local SQLite. New battery points use individual Ford timestamps when available; repeated old readings do not become fresh chart points. Legacy history keeps its observation timestamps. Longer views use only collected data.
- Charging voltage/current fields come from the **vehicle's onboard charger**, not necessarily your wallbox's energy consumption.

## Troubleshooting

- **Site can't be reached at `http://YOUR-NAS-IP:8765`:** Open Portainer > Containers > `mache-telemetry` > **Logs**. The container can show Running/Restarting while the Python process is failing repeatedly. Check LAN connectivity and the `8765:8765` port mapping.
- **`JSONDecodeError: Expecting ',' delimiter: line 3`:** Your `config.json` has malformed JSON (commonly an extra `{"secret":"..."}` wrapper). Fix the formatting, upload with **Overwrite**, and restart the container. Do not post your credentials.
- **`dashboard_password must be at least 12 characters`:** Choose a longer dashboard password in `config.json`, overwrite the file on the NAS, and restart.
- **Error page at `http://localhost:8080/callback?...` *after* Ford consent:** Expected. Copy the FULL callback URL into the dashboard; don't reload it or share it.
- **OAuth/token error:** Confirm Ford has accepted the **exact** redirect URI and that Client ID and Secret come from the **same Developer application**. Share only sanitized error categories. Be mindful of Ford API account permissions and regional variations.
- **Connected but no telemetry:** Click **Fetch latest telemetry** and read the diagnostic message at the bottom. Ford might not expose all fields or might return stale values.
- **Changed code files but UI looks old:** Use Ctrl+Shift+R to bypass cached static files. A changed `server.py` requires restarting the container; static `web` updates usually only need a hard refresh.

## Sharing, updates and backups

- Only share source files such as `server.py`, `web/`, `docker-compose.yml`, `config.example.json`, and README.
- **Never share** `config.json` or anything under `data/` (it holds OAuth tokens, raw telemetry with location/VIN, and history). A `.gitignore` is included as a second line of defense—check uploads manually too.
- To update the app on a working NAS, overwrite only `server.py` and the files in `web/`, restart the container, then hard-refresh the browser. **Do not overwrite `config.json` or `data/`.**
- Ford Developer permissions, API terms, rate limits and availability can change. Respect Ford's rules and use a reasonable polling interval. This dashboard defaults to 180 seconds.
- App is provided **as-is**, experimental, LAN-only, and read-only. It is not medical/safety-grade diagnostics; the vehicle and a qualified technician remain the authority for critical checks.

## Development and data handling

### Source layout

- `server.py`: the existing OAuth client, HTTP server, polling, validation, unit conversions, local history and exports. It stays one runtime Python file to preserve existing bind mounts.
- `web/`: the dashboard, local illustration/icons and home-screen manifest.
- `tests/`: offline regression checks and a minimized, redacted version of the supplied response. This fixture is never loaded by the running dashboard.
- `docs/DATA_SUPPORT.md`: what the real response supports, which units were absent, and what remains unverified.
- `FEATURES.md`: the preservation contract for future releases.
- `CHANGELOG.md`: release history.

### Existing deployment

Keep the existing `config.json`, `data/`, Portainer service and compose file. Replace `server.py` and the seven `web/` files together, then restart the existing service. The provided handoff used host-mounted files; if your installation instead copies source into its Docker image, rebuild that existing service using its existing configuration. This release does not include or replace deployment settings.

The database receives one additive `normalized_history` table and an index. Existing `history` and `efficiency_trips` tables retain their original columns and semantics. Tokens are refreshed only by the existing OAuth flow when needed. No new scopes or endpoint permissions are requested. New poll-success state is stored in `data/dashboard-state.json`.

### Verify locally

```sh
python3 -m unittest discover -s tests -v
node --check web/app.js
```

Python 3.10+ with the same timezone support as the original application is suitable. Node is optional for development syntax checks; it is not a runtime dependency. HTTP tests use loopback networking and never call Ford. Test credentials are generated inside a temporary directory. `MACHE_ROOT` exists to point tests at an isolated installation; leave it unset for normal deployment.

Optional browser regression: with Playwright and Chromium available in your development environment, run `python3 tests/browser_smoke.py`. It checks real local HTTP routes, desktop/mobile rendering, sparse/empty data and offline behavior, using mocked Ford requests. Screenshots go to `work/qa/` or the directory specified by `MACHE_QA_OUTPUT`. Neither tool is required on the NAS.

### Data and units

Normalize once on the server: miles, °F, PSI, kWh, V/A/kW and mi/kWh. Every reading includes the Ford metric timestamp, original unit, native-unit default (if used), and availability reason. An unrecognized explicit unit is withheld, rather than displayed under the wrong unit.

The owner's Ford response omitted units. Defaults used by existing FordConnect implementations are labeled, and the owner confirmed that Ford's unitless odometer reading corresponds to about 17,158 miles on the car. The supplied raw value 27,612 km converts to 17,157.30 mi; the confirmation was approximate. `displaySystemOfMeasure=IMPERIAL` describes the vehicle display and is not used as a telemetry-unit declaration.

If a future response needs calibration, the optional private configuration entry below affects only unitless readings; explicit Ford units always take priority:

```json
"native_units": {"odometer": "km", "xevBatteryRange": "km"}
```

This setting is not needed for the supplied vehicle. Unknown charger-energy units intentionally have no default. A 91 kWh capacity value does not establish a state-of-health percentage.

### History and availability

The original seven-day battery/efficiency views are preserved. The selector also offers 30/90/365 days, using only collected data. Legacy battery records retain their dashboard observation timestamps. New battery points use Ford's individual SOC timestamp when available; repeated old readings do not become new points. Raw historical distances are not silently reinterpreted or migrated.

CSV readings contain normalized records from v1.3 onward, per-metric timestamps and unit provenance. They omit VIN, location and credentials. Large exports stream in bounded batches. Historical supported trip measurements remain available through `/api/efficiency.csv` even if the latest response has no trip data. The trip chart appears when the selected period contains supported records.

Optional cards hide if the current response has no usable reading. Reasons remain visible in the data-availability disclosure, and `/api/capabilities` exports the report. All vehicle-data endpoints, exports and the original raw view require the existing dashboard login. Only the non-sensitive illustration/icons and manifest are available on the login screen. A saved token is labeled authorized; a successful recent API check is required for FORD RESPONDING.

### Alerts and mobile

Dashboard alerts cover last-reported low SOC, Ford-reported tire warnings, reported charging errors and HV readings over a day old. Warnings retain their timestamps. Tire pressure below a cold placard does not override Ford's TPMS status or invent a warning threshold. No email, push or other external message is sent.

The dashboard is responsive and includes home-screen icons and a manifest. On an HTTP LAN address, browser installation features vary; full PWA installation generally requires HTTPS. Use the browser's Add to Home Screen/shortcut feature when offered. Vehicle readings are never cached by a service worker.

### Future updates and version history

Keep the existing GitHub history. Make future changes on a branch, run the regression checks, and review the pull request before merging. Record changes in CHANGELOG.md and keep the feature inventory in FEATURES.md current. Publish versioned releases for repeatable updates and rollback.

Private config.json, data/, tokens, local databases and credentials stay outside the repository. The public config.example.json contains placeholders for fresh installations. A Git bundle is optional recovery material for separate archives; this GitHub repository already stores the project history.
