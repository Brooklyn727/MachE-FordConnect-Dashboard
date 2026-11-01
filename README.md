# Community Mach-E Dashboard — Synology / Docker setup

A private, **read-only** web dashboard for Ford's FordConnect Query API. Tested by one 2025 Mustang Mach-E GT owner in North America using a Synology NAS and Portainer. Other vehicles, regions, accounts, firmware and API responses may behave differently. This project is independent and is **not affiliated with or supported by Ford**.

**What it can show (when Ford returns data):** battery state of charge (SOC), estimated range, odometer, nominal/reported battery capacity, battery/outside temperature, plug/charge status, tire pressures (PSI), tire-pressure status and placard pressure, 12V voltage, high-voltage battery performance status, door/lock/hood/ignition status, and some onboard charging details. It saves historical polling snapshots in SQLite and shows the age of several readings.

**What it cannot yet show:** a *verified high-voltage battery State of Health (SOH) percentage* on the tested 2025 Mach-E. The API returned `xevBatteryCapacity: 91` kWh, but that is **not proof of 100% battery health**. This app intentionally leaves the SOH card blank rather than inventing a value.

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
4. At first the cards will be blank and the top-right status will say **NOT CONNECTED**.

## Step 6 — Authorize with Ford

1. Scroll down to **Ford API Connection**.
2. Click **Start Ford authorization**.
3. On Ford's official page, sign in with the **regular Ford app/FordPass account that owns or has access to the vehicle**—**not** the Ford Developer login (unless they're the same account).
4. Select your vehicle, review the requested data-sharing permissions, and click **Authorize** if you agree.
5. Your browser will probably show `This site can't be reached` at `http://localhost:8080/callback?state=...&code=...`. **This is expected for this manual flow.**
6. Copy the **entire address-bar URL** with Ctrl+A and Ctrl+C. **Do not post it anywhere.**
7. Return to your dashboard tab, paste the URL into the large input under **Ford API Connection / step 3**, and click **Exchange code for token** promptly (codes are single-use and expire).
8. If successful, the upper-right badge will say **FORD CONNECTED**. Click **Fetch latest telemetry** to request readings. The background service also polls periodically.

## Step 7 — Reading your results

- Ford's telemetry distances are often in **kilometers with no unit label**. The frontend assumes km unless Ford explicitly says miles, and converts to miles. Temperatures similarly default to degrees Celsius, converted to °F. Those assumptions were checked against one US 2025 Mach-E; verify if your results look wrong.
- Tire pressures are treated as **kPa when the API supplies no units** and displayed as PSI. Ford may report delayed values. A sensor status of `NORMAL` does not mean pressure equals the recommended cold setting.
- The **Battery Capacity** card displays kWh. It is **not a battery health percentage**.
- **Battery Health** remains blank if Ford doesn't return an explicit SOH metric. Do not interpret the factory 91 kWh figure as 100% battery health.
- Sensor **Last Reported** ages may differ; a dashboard sync doesn't imply every ECU sensor was refreshed.
- The graph stores snapshots in a local SQLite database. It may take multiple polls to show meaningful trends, and repeated values can form a flat line.
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
