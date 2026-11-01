# I built a free, self-hosted Mach-E dashboard using Ford's API — battery, range, tire pressures, charging, and more (Synology/Docker guide)

I wanted a way to monitor my Mach-E from a browser instead of constantly opening FordPass. After going down a pretty frustrating Home Assistant/OAuth rabbit hole, I ended up putting together a small standalone dashboard with ChatGPT and got it running on my Synology NAS.

**It actually works.** It connects to Ford's official FordConnect Query API and pulls real vehicle telemetry into a private dashboard on my home network. No OBD dongle, no paid subscription, and **no Home Assistant required**.

I tested it on my **2025 Mustang Mach-E GT in the US**. I can't guarantee every metric will appear for every model/year/region, but here's what mine displays:

- High-voltage battery charge percentage, estimated range, odometer, and reported battery capacity
- Four individual tire pressures in **PSI**, plus TPMS status and Ford's cold-pressure placard value
- 12V system voltage, high-voltage battery performance status, door/lock/hood/ignition status
- Battery/outside temperature, charging/plug status, and onboard charging voltage/current
- Historical charge-percentage snapshots and **how old individual sensor readings are**

**Major caveat on battery health:** I originally built this hoping to display actual high-voltage battery State of Health (SOH). Ford's response for my 2025 GT includes a 91 kWh capacity number, but **does not include a verified SOH percentage**. The app intentionally leaves that box blank rather than pretending 91 kWh means 100% health. So this is a real working telemetry dashboard, **not yet a solution for getting SOH**.

**Source files / complete README:** [ADD YOUR PUBLIC GITHUB LINK HERE]

### What you need

A Mach-E with connected services enabled and linked to your regular FordPass/Ford account; an approved Ford Developer application with FordConnect API access; a computer/server that can run Docker (I used Synology + Portainer); and a browser on your home network. The two Ford logins serve different purposes: the Developer account supplies API credentials, while your regular Ford account grants access to the vehicle.

### Step 1: Create the Ford API application

Go to https://developer.ford.com/, register/sign in, and create/request access to a **FordConnect** application. Ford's approval process and portal options can vary.

Once you have the application, save its **Client ID** and **Client Secret** somewhere private. In the portal, find **View OAuth URLs and Modify Redirect URIs** > **Account Linking Info**, and register this **exact** callback:

`http://localhost:8080/callback`

Ford accepted that address for my application. If it rejects it for yours, don't continue with this manual callback method until you have an accepted redirect. The callback has to match character-for-character.

### Step 2: Copy the app onto your server

Download/extract the files from the repo. In Synology **DSM > File Station**, create:

`/volume1/docker/mache-telemetry/`

Put `server.py`, `config.example.json`, and the entire `web/` folder **directly inside** that folder. Also create an empty folder named `data/`. It should look like this:

```text
mache-telemetry/
  server.py
  config.example.json
  web/
    index.html
    app.js
    style.css
  data/
```

Don't accidentally create an extra nested folder when unzipping.

### Step 3: Set your credentials

Make a **copy** of `config.example.json` named `config.json`, then edit that copy in Synology Text Editor or Notepad++. Put in the Client ID and Secret from the **same** Ford Developer app and a unique dashboard password of **at least 12 characters**:

```json
{
  "client_id": "YOUR_CLIENT_ID",
  "client_secret": "YOUR_CLIENT_SECRET",
  "dashboard_password": "YOUR_LONG_UNIQUE_PASSWORD",
  "redirect_uri": "http://localhost:8080/callback",
  "poll_seconds": 180
}
```

If Ford displays the secret in a JSON snippet like `{"secret":"VALUE"}`, paste **only `VALUE`** into the `client_secret` field. Don't include the extra wrapper, or Python will throw a `JSONDecodeError` (ask me how I know).

**Never post `config.json` or anything in `data/`.** Those contain private credentials and vehicle information.

### Step 4: Deploy with Portainer

In **Portainer > Stacks > Add stack > Web editor**, name the stack `mache-telemetry` and paste the supplied `docker-compose.yml` (or this):

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

Deploy the stack. If your server isn't a Synology or uses a different folder, change the **left side** of the volume path. Change the timezone if needed.

### Step 5: Open the dashboard

On a device connected to your home network, go to:

`http://YOUR-NAS-IP:8765`

Log in with the **dashboard password** you chose in `config.json` (not your Ford password). Initially the vehicle readings will be empty.

### Step 6: Connect it to Ford

Scroll down to **Ford API Connection** and click **Start Ford authorization**.

A Ford sign-in page opens. Sign in with your **regular FordPass/Ford app account**, select your Mach-E, and approve the requested vehicle-data permissions if you're comfortable with them.

**This next bit is weird but intentional:** After consenting, the browser will probably show **“This site can't be reached”** at an address beginning with:

`http://localhost:8080/callback?state=...&code=...`

That's **expected**. There isn't supposed to be a server listening on your PC at port 8080. Copy the **entire URL from your browser address bar** (Ctrl+A / Ctrl+C), switch back to the private dashboard, paste it into the large callback field, and click **Exchange code for token**.

**Do not post or screenshot that full URL.** It contains a short-lived authorization code.

When successful, the dashboard changes to **FORD CONNECTED**. Click **Fetch latest telemetry**, and the data should start showing up. The app polls periodically after that.

### Troubleshooting that would've saved me a lot of time

- **Can't reach port 8765?** Portainer > Containers > `mache-telemetry` > **Logs**. A container may be restarting because Python can't read the config, even if it briefly shows as running.
- **`JSONDecodeError`?** Check the commas/quotes in `config.json`. The Ford secret needs to be a single quoted string, not a pasted JSON object.
- **Password error in logs?** Make your dashboard password at least 12 characters.
- **Can't reach `localhost:8080` after consent?** That part is normal. Copy the URL back to the dashboard.
- **Connected but missing a sensor?** Not every reading is supported or recently updated. Check the **Last Reported** times.
- **Wrong odometer/range units?** Ford may return kilometers without an explicit unit label. The frontend converts to miles and °F using the assumptions in the README; confirm against your car if it looks off.

### A couple of important disclaimers

This is an **unofficial, experimental, read-only** app that uses Ford's cloud API. I'm not affiliated with Ford. Ford can change its API, limits, or access requirements. **Keep this dashboard on a trusted home network; do NOT forward port 8765 or expose it to the internet.** It uses plain HTTP and stores sensitive tokens/vehicle data locally. The full raw telemetry may include GPS coordinates and the VIN, so don't share it publicly. And for tire pressure, trust a cold-tire gauge and your door-jamb placard over delayed cloud readings.

I put the source, Docker file, and a more detailed beginner README together so other people don't have to repeat my OAuth/JSON debugging adventure. If anyone knows of an **actual Ford API field that gives battery SOH** on a 2025 Mach-E, I'd love to hear it — that's the one metric I'm still trying to solve.
