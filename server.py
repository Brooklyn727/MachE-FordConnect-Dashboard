#!/usr/bin/env python3
"""Mach-E local dashboard / FordConnect Query client (Python stdlib only).

Designed for an isolated LAN-only Docker container. Not affiliated with Ford.
Unofficial community prototype. Supported metric availability varies by vehicle.
"""
from __future__ import annotations

import base64
import hmac
import html
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CFG_FILE = ROOT / "config.json"
AUTH_URL = "https://api.vehicle.ford.com/fcon-public/v1/auth/init"
TOKEN_URL = "https://api.vehicle.ford.com/dah2vb2cprod.onmicrosoft.com/oauth2/v2.0/token?p=B2C_1A_FCON_AUTHORIZE"
API_URL = "https://api.vehicle.ford.com/fcon-query/v1"
PORT = 8765

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("mache")

if not CFG_FILE.exists():
    raise SystemExit("Missing config.json. Copy config.example.json to config.json and fill in your private values.")
CFG = json.loads(CFG_FILE.read_text(encoding="utf-8"))
for required in ("client_id", "client_secret", "dashboard_password", "redirect_uri"):
    if not CFG.get(required) or str(CFG[required]).startswith("YOUR_"):
        raise SystemExit(f"Missing {required} in config.json")
if len(CFG["dashboard_password"]) < 12:
    raise SystemExit("dashboard_password must be at least 12 characters")
if not CFG["redirect_uri"].startswith(("http://localhost:", "http://127.0.0.1:", "http://192.168.", "https://")):
    raise SystemExit("Choose a localhost/LAN/HTTPS callback URI in config.json")

DATA.mkdir(parents=True, exist_ok=True)
STATE_PATH = DATA / "session.json"
TOKEN_PATH = DATA / "tokens.json"
HISTORY_DB = DATA / "history.db"
GARAGE_PATH = DATA / "garage.json"
TELEMETRY_PATH = DATA / "telemetry.json"
LOCK = threading.RLock()
SESSIONS: dict[str, float] = {}
OAUTH: dict[str, float] = {}
LAST_ERROR = ""
LAST_POLL = None

def _write_private(path: Path, obj: object) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
    os.chmod(tmp, 0o600)
    tmp.replace(path)

def _load(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default

def _initialize_db():
    with sqlite3.connect(HISTORY_DB) as con:
        con.execute("CREATE TABLE IF NOT EXISTS history (ts TEXT PRIMARY KEY, soc REAL, range_value REAL, odometer REAL, capacity REAL, health REAL)")

_initialize_db()

def scrub(value: object) -> str:
    """Show only an OAuth error summary, never tokens, codes, or secrets."""
    message = str(value)[:600]
    for item in (CFG["client_secret"], CFG["client_id"]):
        message = message.replace(str(item), "[REDACTED]")
    return message

class FordError(Exception):
    def __init__(self, status, details):
        self.status = status
        self.details = scrub(details)
        super().__init__(f"Ford HTTP {status}: {self.details}")

def _request(method: str, url: str, payload=None, access_token=None):
    headers = {"Accept": "application/json", "User-Agent": "LocalMachEDashboard/0.1"}
    body = None
    if payload is not None:
        body = urlencode(payload).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if access_token:
        headers["Authorization"] = "Bearer " + access_token
    request = Request(url, method=method, headers=headers, data=body)
    try:
        with urlopen(request, timeout=25) as response:
            raw = response.read(2_000_000)
    except HTTPError as err:
        raw = err.read(20_000)
        try:
            parsed = json.loads(raw)
            allowed = ("error", "error_description", "message", "traceId", "errorCode", "status")
            detail = " | ".join(f"{k}: {scrub(parsed[k])}" for k in allowed if k in parsed)
        except (ValueError, TypeError):
            detail = "No usable JSON error body"
        raise FordError(err.code, detail or "Ford did not provide an OAuth error description") from None
    except (URLError, TimeoutError) as err:
        raise FordError("network", type(err).__name__) from None
    try:
        return json.loads(raw)
    except ValueError:
        raise FordError("response", "Non-JSON response from Ford") from None

def _refresh_token(tokens: dict):
    payload = {
        "grant_type": "refresh_token",
        "client_id": CFG["client_id"],
        "client_secret": CFG["client_secret"],
        "refresh_token": tokens["refresh_token"],
        "redirect_uri": CFG["redirect_uri"],
        "scope": f"{CFG['client_id']} offline_access openid",
    }
    fresh = _request("POST", TOKEN_URL, payload)
    if "access_token" not in fresh:
        raise FordError("response", "No access_token in refresh response")
    if "refresh_token" not in fresh:
        fresh["refresh_token"] = tokens["refresh_token"]
    fresh["obtained_at"] = time.time()
    _write_private(TOKEN_PATH, fresh)
    return fresh

def _token():
    with LOCK:
        tokens = _load(TOKEN_PATH, {})
        if not tokens:
            return None
        if time.time() >= tokens.get("obtained_at", 0) + tokens.get("expires_in", 1200) - 90:
            tokens = _refresh_token(tokens)
        return tokens.get("access_token")

def _metric(raw: dict, name: str):
    metrics = raw.get("metrics") or {}
    value = metrics.get(name)
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        return value.get("value"), value.get("unit") or value.get("uom")
    return value, None

def _number(value):
    try:
        return float(value) if value is not None and not isinstance(value, bool) else None
    except (ValueError, TypeError):
        return None

def _summarize(raw: dict):
    """Whitelisted Ford metrics; keeps private location and full VIN off the dashboard API."""
    source = raw if isinstance(raw, dict) and "metrics" in raw else (
        raw[0] if isinstance(raw, list) and raw and isinstance(raw[0], dict) else {}
    )
    if not isinstance(source, dict):
        source = {}
    metrics = source.get("metrics") or {}
    aliases = {
        "soc": ["xevBatteryStateOfCharge", "xevBatteryActualStateOfCharge"],
        "range": ["xevBatteryRange"],
        "odometer": ["odometer"],
        "capacity": ["xevBatteryCapacity"],
        "health": ["xevBatteryStateOfHealth", "xevBatteryHealth", "batteryStateOfHealth"],
        "battery_temp": ["xevBatteryTemperature"],
        "outdoor_temp": ["outsideTemperature"],
        "charge_status": ["xevBatteryChargeDisplayStatus"],
        "plug_status": ["xevPlugChargerStatus"],
        "battery_12v": ["batteryVoltage"],
        "battery_performance": ["xevBatteryPerformanceStatus"],
        "ignition": ["ignitionStatus"],
        "hood": ["hoodStatus"],
        "charger_type": ["xevChargeStationPowerType"],
        "charger_voltage": ["xevBatteryChargerVoltageOutput"],
        "charger_current": ["xevBatteryChargerCurrentOutput"],
    }
    summary = {}
    for key, possible_names in aliases.items():
        found = None
        for name in possible_names:
            reading = metrics.get(name)
            if isinstance(reading, list):
                reading = reading[0] if reading else None
            if isinstance(reading, dict):
                if reading.get("value") is not None:
                    found = {"value": reading.get("value"),
                             "unit": reading.get("unit") or reading.get("uom"),
                             "updated_at": reading.get("updateTime")}
                    break
            elif reading is not None:
                found = {"value": reading, "unit": None, "updated_at": None}
                break
        summary[key] = found if found is not None else {"value": None, "unit": None, "updated_at": None}

    # Ford tirePressure values are normally kPa (often with no unit annotation).
    # Include Ford's placard reading for context, not as an inferred safety diagnosis.
    pressures = metrics.get("tirePressure") or []
    wheel_status = {p.get("vehicleWheel"): p.get("value") for p in (metrics.get("tirePressureStatus") or [])
                    if isinstance(p, dict)}
    wheels = ("FRONT_LEFT", "FRONT_RIGHT", "REAR_LEFT", "REAR_RIGHT")
    tp = {}
    for p in pressures if isinstance(pressures, list) else []:
        if not isinstance(p, dict) or p.get("vehicleWheel") not in wheels:
            continue
        wheel = p["vehicleWheel"]
        placard_field = "wheelPlacardFront" if wheel.startswith("FRONT") else "wheelPlacardRear"
        tp[wheel] = {"value": p.get("value"), "unit": p.get("unit") or p.get("uom"),
                     "placard": p.get(placard_field), "updated_at": p.get("updateTime"),
                     "status": wheel_status.get(wheel)}
    summary["tires"] = tp

    all_locks = next((p for p in metrics.get("doorLockStatus", [])
                      if isinstance(p, dict) and p.get("vehicleDoor") == "ALL_DOORS"), None)
    summary["door_lock"] = {"value": all_locks.get("value") if all_locks else None,
                            "updated_at": all_locks.get("updateTime") if all_locks else None}
    doors = [p for p in metrics.get("doorStatus", []) if isinstance(p, dict) and p.get("value")]
    if len(doors) >= 4 and all(p["value"] == "CLOSED" for p in doors):
        door_value = "ALL CLOSED"
    elif any(p["value"] == "OPEN" for p in doors):
        door_value = "OPEN DETECTED"
    else:
        door_value = None
    summary["doors"] = {"value": door_value,
                        "updated_at": max((p.get("updateTime") or "" for p in doors), default=None)}
    summary["vin_suffix"] = str(source.get("vin") or source.get("vehicleId") or "")[-4:]
    return summary

def poll():
    global LAST_POLL, LAST_ERROR
    access = _token()
    if not access:
        return
    garage = _request("GET", API_URL + "/garage", access_token=access)
    telemetry = _request("GET", API_URL + "/telemetry", access_token=access)
    summary = _summarize(telemetry)
    with LOCK:
        _write_private(GARAGE_PATH, garage)
        _write_private(TELEMETRY_PATH, telemetry)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        numeric = [_number(summary[x]["value"]) for x in ("soc", "range", "odometer", "capacity", "health")]
        with sqlite3.connect(HISTORY_DB) as con:
            con.execute("INSERT OR REPLACE INTO history VALUES (?,?,?,?,?,?)", (now, *numeric))
        LAST_POLL = now
        LAST_ERROR = ""

def background():
    global LAST_ERROR
    time.sleep(10)
    while True:
        try:
            poll()
        except (FordError, ValueError) as err:
            LAST_ERROR = scrub(err)
            LOG.warning("Ford polling failed: %s", LAST_ERROR)
        except Exception as err:
            LAST_ERROR = type(err).__name__
            LOG.exception("Unexpected polling exception")
        time.sleep(max(120, int(CFG.get("poll_seconds", 180))))

def _as_json(obj):
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")

def _login_page(error=""):
    banner = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return f'''<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><title>Mach-E · Unlock</title>
    <style>body{{background:#10131a;color:#eaf0f9;display:grid;place-items:center;min-height:96vh;font:16px system-ui}}main{{width:min(420px,90vw);padding:40px;background:#1d2330;border:1px solid #333d4f;border-radius:22px}}input,button{{width:100%;padding:14px;border-radius:8px;box-sizing:border-box;font:inherit;margin:8px 0}}input{{background:#111824;color:white;border:1px solid #526076}}button{{background:#7fcbbb;border:0;color:#0b1c1c;font-weight:bold;cursor:pointer}}.error{{color:#ff9797}}</style>
    </head><body><main><h1>Mach-E</h1><p>Private dashboard · local server</p>{banner}<form method="POST" action="/login"><input type="password" name="password" placeholder="Dashboard password" required autofocus><button>Unlock dashboard</button></form></main></body></html>'''

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        # Do not log URL query parameters. OAuth callback query contains a code.
        return

    def send(self, content, status=200, content_type="text/html; charset=utf-8", cookie=None):
        body = content.encode("utf-8") if isinstance(content, str) else content
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def move(self, path, cookie=None):
        self.send_response(303)
        self.send_header("Location", path)
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def _session(self):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
            sid = cookies["sid"].value if "sid" in cookies else ""
        except Exception:
            return None
        if sid and SESSIONS.get(sid, 0) > time.time():
            return sid
        return None

    def _form(self):
        length = min(int(self.headers.get("Content-Length", 0) or 0), 16_384)
        return {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True).items()}

    def _csrf(self, sid, form):
        return bool(sid and hmac.compare_digest(str(form.get("csrf", "")), sid))

    def do_GET(self):
        global LAST_ERROR
        path = urlsplit(self.path)
        if path.path == "/callback":
            # A direct callback works ONLY if it matches Ford's registered redirect URI.
            return self.oauth_return(parse_qs(path.query))
        if path.path == "/login":
            return self.send(_login_page())
        sid = self._session()
        if not sid:
            return self.move("/login")
        if path.path == "/logout":
            SESSIONS.pop(sid, None)
            return self.move("/login", "sid=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/")
        if path.path == "/":
            template = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
            template = template.replace("__CSRF__", html.escape(sid)).replace("__REDIRECT__", html.escape(CFG["redirect_uri"]))
            return self.send(template)
        if path.path == "/web/app.js":
            return self.send((ROOT / "web" / "app.js").read_bytes(), content_type="text/javascript; charset=utf-8")
        if path.path == "/web/style.css":
            return self.send((ROOT / "web" / "style.css").read_bytes(), content_type="text/css; charset=utf-8")
        if path.path == "/api/status":
            with LOCK:
                tokens = _load(TOKEN_PATH, {})
                last_raw = _load(TELEMETRY_PATH, {})
                with sqlite3.connect(HISTORY_DB) as con:
                    rows = con.execute("SELECT ts,soc,range_value,odometer,capacity,health FROM history ORDER BY ts DESC LIMIT 100").fetchall()
            obj = {"connected": bool(tokens.get("access_token")), "summary": _summarize(last_raw), "last_poll": LAST_POLL,
                   "last_error": LAST_ERROR, "history": [dict(zip(["ts","soc","range","odo","capacity","health"], row)) for row in reversed(rows)],
                   "callback_uri": CFG["redirect_uri"]}
            return self.send(_as_json(obj), content_type="application/json")
        if path.path == "/api/raw":
            return self.send(_as_json({"garage": _load(GARAGE_PATH, {}), "telemetry": _load(TELEMETRY_PATH, {})}), content_type="application/json")
        if path.path == "/start":
            state = secrets.token_hex(8)
            with LOCK:
                OAUTH.clear()
                OAUTH[state] = time.time() + 900
            query = urlencode({"client_id": CFG["client_id"], "response_type": "code", "redirect_uri": CFG["redirect_uri"],
                               "scope": "openid offline_access", "state": state})
            return self.move(AUTH_URL + "?" + query)
        return self.send("Not found", status=404)

    def oauth_return(self, params):
        global LAST_ERROR
        # Authorization code must never appear in logs or error pages.
        state = params.get("state", [""])[0]
        code = params.get("code", [""])[0]
        with LOCK:
            expiry = OAUTH.pop(state, 0)
        if not (code and state and expiry > time.time()):
            LAST_ERROR = "OAuth callback missing code, or state expired/mismatched. Start a new login from the dashboard."
            return self.move("/")
        try:
            payload = {
                "grant_type": "authorization_code",
                "client_id": CFG["client_id"],
                "client_secret": CFG["client_secret"],
                "code": code,
                "redirect_uri": CFG["redirect_uri"],
                "scope": f"{CFG['client_id']} offline_access openid",
            }
            tokens = _request("POST", TOKEN_URL, payload)
            if "access_token" not in tokens:
                raise FordError("response", "Ford returned no access_token")
            tokens["obtained_at"] = time.time()
            with LOCK:
                _write_private(TOKEN_PATH, tokens)
                LAST_ERROR = ""
            # Background worker performs garage/telemetry polling.
        except FordError as err:
            LAST_ERROR = scrub(err)
        return self.move("/")

    def do_POST(self):
        global LAST_ERROR
        path = urlsplit(self.path).path
        form = self._form()
        if path == "/login":
            password = form.get("password", "")
            if hmac.compare_digest(password, CFG["dashboard_password"]):
                sid = secrets.token_urlsafe(24)
                SESSIONS[sid] = time.time() + 86400
                return self.move("/", f"sid={sid}; HttpOnly; SameSite=Strict; Path=/; Max-Age=86400")
            return self.send(_login_page("Incorrect password"), status=403)
        sid = self._session()
        if not self._csrf(sid, form):
            return self.send("Invalid session or CSRF token", status=403)
        if path == "/manual":
            url = form.get("callback_url", "").strip()
            uri = urlsplit(url)
            expected = urlsplit(CFG["redirect_uri"])
            if (uri.scheme, uri.netloc, uri.path) != (expected.scheme, expected.netloc, expected.path):
                LAST_ERROR = "Pasted redirect URL does not match the callback URI in config.json"
                return self.move("/")
            return self.oauth_return(parse_qs(uri.query))
        if path == "/poll":
            try:
                poll()
            except FordError as err:
                LAST_ERROR = scrub(err)
            return self.move("/")
        return self.send("Not found", status=404)

if __name__ == "__main__":
    LOG.info("Starting private Mach-E dashboard on port %d", PORT)
    threading.Thread(target=background, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
