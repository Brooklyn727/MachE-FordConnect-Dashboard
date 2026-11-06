#!/usr/bin/env python3
"""Mach-E local dashboard / FordConnect Query client (Python stdlib only).

Designed for an isolated LAN-only Docker container. Not affiliated with Ford.
Validated against the owner's supplied Ford response; no live-account network
test was performed from the development workspace. See docs/DATA_SUPPORT.md.
"""
from __future__ import annotations

import base64
import csv
import hmac
import hashlib
import html
import json
import logging
import io
import math
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

VERSION = "1.3.0"
ROOT = Path(os.environ.get("MACHE_ROOT", str(Path(__file__).resolve().parent)))
DATA = ROOT / "data"
CFG_FILE = ROOT / "config.json"
AUTH_URL = "https://api.vehicle.ford.com/fcon-public/v1/auth/init"
TOKEN_URL = "https://api.vehicle.ford.com/dah2vb2cprod.onmicrosoft.com/oauth2/v2.0/token?p=B2C_1A_FCON_AUTHORIZE"
API_URL = "https://api.vehicle.ford.com/fcon-query/v1"
PORT = 8765
LOCAL_ZONE = ZoneInfo("America/New_York")

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
POLL_STATE_PATH = DATA / "dashboard-state.json"
LOCK = threading.RLock()
POLL_LOCK = threading.Lock()
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

LAST_POLL = _load(POLL_STATE_PATH, {}).get("last_success")

def _initialize_db():
    with sqlite3.connect(HISTORY_DB) as con:
        con.execute("CREATE TABLE IF NOT EXISTS history (ts TEXT PRIMARY KEY, soc REAL, range_value REAL, odometer REAL, capacity REAL, health REAL)")
        # Separate additive table: existing Ford history and tokens remain untouched.
        con.execute("CREATE TABLE IF NOT EXISTS efficiency_trips (trip_id TEXT PRIMARY KEY, date_local TEXT NOT NULL, distance_mi REAL, energy_kwh REAL, direct_mi_kwh REAL)")
        con.execute("CREATE TABLE IF NOT EXISTS normalized_history (fingerprint TEXT PRIMARY KEY, observed_at TEXT NOT NULL, vehicle_at TEXT, summary_json TEXT NOT NULL)")
        con.execute("CREATE INDEX IF NOT EXISTS normalized_history_time ON normalized_history(observed_at)")

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

def _number(value):
    try:
        n = float(value) if value is not None and not isinstance(value, bool) and str(value).strip() else None
        return n if n is not None and math.isfinite(n) else None
    except (ValueError, TypeError):
        return None

def _parsed_time(value):
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            d = datetime.fromtimestamp(value / (1000 if value > 100_000_000_000 else 1), tz=timezone.utc)
        elif isinstance(value, str):
            d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            return None
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        return None

def _source(raw):
    if isinstance(raw, dict) and "telemetry" in raw:
        raw = raw["telemetry"]
    if isinstance(raw, list):
        items = [x for x in raw if isinstance(x, dict) and isinstance(x.get("metrics"), dict)]
        selected = CFG.get("vehicle_vin")
        if selected:
            return next((x for x in items if x.get("vin") == selected), {})
        if len(items) > 1:
            raise ValueError("Multiple vehicles returned; set vehicle_vin in private config.json to avoid mixing vehicles")
        return items[0] if items else {}
    return raw if isinstance(raw, dict) and isinstance(raw.get("metrics"), dict) else {}

def _records(source, name):
    recs = (source.get("metrics") or {}).get(name)
    if recs is None:
        return []
    if not isinstance(recs, list):
        recs = [recs]
    return [dict(r) if isinstance(r, dict) else {"value": r} for r in recs]

def _latest(records):
    return max(records, key=lambda r: (_parsed_time(r.get("updateTime") or r.get("timestamp") or r.get("updatedAt")) or datetime.min.replace(tzinfo=timezone.utc)), default=None)

def _native_unit(unit):
    return str(unit or "").strip().lower().replace("°", "").replace(" ", "").replace("_", "").replace("-", "")

# Only native defaults corroborated by the captured FordConnect response and
# existing FordConnect integrations are used. Display-system IMPERIAL is NOT an
# API unit. Defaults remain explicit in metadata and can be calibrated per field.
CONVERSIONS = {
    "distance": ("mi", {"mi": (1, 0), "mile": (1, 0), "miles": (1, 0), "km": (1 / 1.609344, 0), "kilometer": (1 / 1.609344, 0), "kilometers": (1 / 1.609344, 0), "kilometre": (1 / 1.609344, 0), "kilometres": (1 / 1.609344, 0), "m": (1 / 1609.344, 0), "meter": (1 / 1609.344, 0), "meters": (1 / 1609.344, 0), "metre": (1 / 1609.344, 0), "metres": (1 / 1609.344, 0)}),
    "temperature": ("°F", {"c": (1.8, 32), "celsius": (1.8, 32), "centigrade": (1.8, 32), "degreec": (1.8, 32), "degreesc": (1.8, 32), "f": (1, 0), "fahrenheit": (1, 0), "degreef": (1, 0), "degreesf": (1, 0), "k": (1.8, -459.67), "kelvin": (1.8, -459.67)}),
    "pressure": ("PSI", {"psi": (1, 0), "kpa": (1 / 6.894757293, 0), "pa": (1 / 6894.757293, 0), "bar": (14.503773773, 0), "mbar": (0.014503773773, 0)}),
    "voltage": ("V", {"v": (1, 0), "volt": (1, 0), "volts": (1, 0), "mv": (0.001, 0), "kv": (1000, 0)}),
    "current": ("A", {"a": (1, 0), "amp": (1, 0), "amps": (1, 0), "ampere": (1, 0), "amperes": (1, 0), "ma": (0.001, 0)}),
    "energy": ("kWh", {"kwh": (1, 0), "kw.h": (1, 0), "wh": (0.001, 0), "kilowatthour": (1, 0), "kilowatthours": (1, 0)}),
    "percent": ("%", {"%": (1, 0), "percent": (1, 0), "percentage": (1, 0), "pct": (1, 0)}),
    "duration": ("min", {"min": (1, 0), "minute": (1, 0), "minutes": (1, 0), "s": (1 / 60, 0), "second": (1 / 60, 0), "seconds": (1 / 60, 0), "h": (60, 0), "hour": (60, 0), "hours": (60, 0)}),
}

# key: (source fields, conversion, unlabeled native unit)
METRICS = {
    "soc": (("xevBatteryStateOfCharge", "xevBatteryActualStateOfCharge"), "percent", "%"),
    "range": (("xevBatteryRange",), "distance", "km"),
    "odometer": (("odometer",), "distance", "km"),
    "capacity": (("xevBatteryCapacity",), "energy", "kWh"),
    "health": (("xevBatteryStateOfHealth", "xevBatteryHealth", "batteryStateOfHealth"), "percent", "%"),
    "battery_temp": (("xevBatteryTemperature",), "temperature", "C"),
    "battery_voltage": (("xevBatteryVoltage",), "voltage", "V"),
    "outdoor_temp": (("outsideTemperature",), "temperature", "C"),
    "charge_status": (("xevBatteryChargeDisplayStatus",), "status", None),
    "plug_status": (("xevPlugChargerStatus",), "status", None),
    "charger_voltage": (("xevBatteryChargerVoltageOutput",), "voltage", "V"),
    "charger_current": (("xevBatteryChargerCurrentOutput",), "current", "A"),
    "time_to_full": (("xevBatteryTimeToFullCharge",), "duration", "min"),
    "charger_type": (("xevChargeStationPowerType",), "status", None),
    "station_status": (("xevChargeStationCommunicationStatus",), "status", None),
    # No default: a unitless charger-energy field cannot establish billed/session energy.
    "charger_energy": (("xevBatteryChargerEnergyOutput",), "energy", None),
    "energy_remaining": (("xevBatteryEnergyRemaining",), "energy", "kWh"),
    "battery_performance": (("xevBatteryPerformanceStatus",), "status", None),
    "aux_voltage": (("batteryVoltage",), "voltage", "V"),
    "aux_soc": (("batteryStateOfCharge",), "percent", "%"),
    "aux_status": (("batteryLoadStatus",), "status", None),
    "alarm": (("alarmStatus",), "status", None),
    "ignition": (("ignitionStatus",), "status", None),
    "hood": (("hoodStatus",), "status", None),
    "tire_system": (("tirePressureSystemStatus",), "status", None),
}

def _convert(record, kind, native=None, field=""):
    rec = record or {}
    unit = rec.get("unit") or rec.get("uom")
    dt = _parsed_time(rec.get("updateTime") or rec.get("timestamp") or rec.get("updatedAt"))
    result = {"value": None, "unit": "", "source_field": field, "source_unit": unit,
              "updated": dt.isoformat(timespec="seconds") if dt else None,
              "assumed_unit": None, "available": False, "reason": "Not supplied in the latest response"}
    value = rec.get("value")
    if value is None or value == "":
        return result
    if kind == "status":
        if isinstance(value, str) and value.strip().upper() not in {"UNKNOWN", "UNDEFINED", "UNSUPPORTED", "INVALID", "NOT_AVAILABLE", "N/A"}:
            result.update(value=value.strip(), available=True, reason="")
        else:
            result["reason"] = "Ford returned an unknown or unsupported status"
        return result
    n = _number(value)
    if n is None:
        result["reason"] = "Ford returned a non-numeric value"
        return result
    effective = unit
    if not effective:
        overrides = CFG.get("native_units", {})
        effective = overrides.get(field, native) if isinstance(overrides, dict) else native
        result["assumed_unit"] = effective
    output_unit, rules = CONVERSIONS[kind]
    result["unit"] = output_unit
    rule = rules.get(_native_unit(effective))
    if rule is None:
        result["reason"] = "Unit not supplied; conversion requires verification" if not effective else "Unrecognized Ford unit; conversion withheld"
        return result
    converted = n * rule[0] + rule[1]
    if (kind != "temperature" and converted < 0) or (kind == "percent" and converted > 100) or (kind == "temperature" and converted < -459.67):
        result["reason"] = "Ford returned an out-of-range value"
        return result
    result.update(value=round(converted, 8), available=True, reason="")
    return result

def _summary_item(source, names, kind, native):
    for field in names:
        records = _records(source, field)
        if field in {"batteryVoltage", "batteryStateOfCharge", "batteryLoadStatus"}:
            records = [r for r in records if r.get("vehicleBattery") == "PRIMARY_BATTERY"]
        if field == "batteryStateOfHealth":
            records = [r for r in records if r.get("vehicleBattery") in {"HIGH_VOLTAGE", "TRACTION_BATTERY"} or r.get("xevBatteryType") == "HIGH_VOLTAGE"]
        record = _latest([r for r in records if r.get("value") is not None])
        if record is not None:
            return _convert(record, kind, native, field)
    return _convert(None, kind, native, names[0])

def _summarize(raw):
    source = _source(raw)
    summary = {key: _summary_item(source, *spec) for key, spec in METRICS.items()}
    summary["vin_suffix"] = str(source.get("vin") or "")[-4:]
    summary["vehicle_updated"] = (_parsed_time(source.get("updateTime")) or None)
    if summary["vehicle_updated"]:
        summary["vehicle_updated"] = summary["vehicle_updated"].isoformat(timespec="seconds")
    tires = {}
    for wheel in ("FRONT_LEFT", "FRONT_RIGHT", "REAR_LEFT", "REAR_RIGHT"):
        records = [r for r in _records(source, "tirePressure") if r.get("vehicleWheel") == wheel]
        rec = _latest(records)
        if rec is None:
            continue
        tire = _convert(rec, "pressure", "kPa", "tirePressure")
        placard_field = "wheelPlacardFront" if wheel.startswith("FRONT") else "wheelPlacardRear"
        tire["placard"] = _convert({"value": rec.get(placard_field), "unit": rec.get("wheelPlacardUnit"), "updateTime": rec.get("updateTime")}, "pressure", "kPa", placard_field)
        tire["status"] = _convert(_latest([r for r in _records(source, "tirePressureStatus") if r.get("vehicleWheel") == wheel]), "status", field="tirePressureStatus")
        tires[wheel] = tire
    summary["tires"] = tires
    for output, field, tag in (("locks", "doorLockStatus", "vehicleDoor"), ("doors", "doorStatus", "vehicleDoor"), ("windows", "windowStatus", "vehicleWindow")):
        items = []
        for rec in _records(source, field):
            if output == "windows" and isinstance(rec.get("value"), dict):
                bounds = rec["value"].get("doubleRange") or {}
                lo, hi = _number(bounds.get("lowerBound")), _number(bounds.get("upperBound"))
                rec["value"] = "CLOSED" if lo == hi == 0 else ("OPEN" if lo is not None and hi is not None and 0 <= lo <= hi else None)
            item = _convert(rec, "status", field=field)
            item.update(position=rec.get(tag, ""), side=rec.get("vehicleSide", ""), determination=rec.get("determinationMethod"))
            items.append(item)
        summary[output] = items
    # Charger OUTPUT power: never combine 12V/HV pack current with charger voltage.
    v, a = summary["charger_voltage"], summary["charger_current"]
    power = _convert(None, "energy", field="charger_voltage × charger_current / 1000")
    power.update(unit="kW", reason="Requires charger voltage/current from matching Ford timestamps")
    vt, at = _parsed_time(v["updated"]), _parsed_time(a["updated"])
    if v["available"] and a["available"] and vt and at and abs((vt - at).total_seconds()) <= 120:
        power.update(value=round(v["value"] * a["value"] / 1000, 3), available=True, updated=min(vt, at).isoformat(timespec="seconds"), reason="", calculated=True,
                     assumed_unit=", ".join(x for x in (v["assumed_unit"], a["assumed_unit"]) if x) or None)
    summary["charging_power"] = power
    return summary


def _metric_record(raw, candidates):
    """Find a Ford metric with its actual unit and its source update timestamp."""
    source = _source(raw)
    for key in candidates:
        rec = _latest([r for r in _records(source, key) if r.get("value") is not None and r.get("tripProgress") != "TRIP_IN_PROGRESS"])
        if rec:
            return {"key": key, "value": rec["value"], "unit": str(rec.get("unit") or rec.get("uom") or ""),
                    "updated": rec.get("updateTime") or rec.get("timestamp") or rec.get("updatedAt")}
    return None

def _unit(unit):
    return str(unit or "").strip().lower().replace(" ", "").replace("_", "").replace("-", "")

def _miles(record):
    if not record:
        return None
    value = _number(record["value"])
    unit = _unit(record["unit"])
    if value is None or value <= 0:
        return None
    if unit in {"mi", "mile", "miles"}:
        return value
    if unit in {"km", "kilometer", "kilometers", "kilometre", "kilometres"}:
        return value * 0.6213711922
    if unit in {"m", "meter", "meters"}:
        return value / 1609.344
    return None

def _kilowatt_hours(record):
    if not record:
        return None
    value = _number(record["value"])
    unit = _unit(record["unit"])
    if value is None or value <= 0:
        return None
    if unit in {"kwh", "kw.h", "kilowatthour", "kilowatthours"}:
        return value
    if unit in {"wh", "watthour", "watthours"}:
        return value / 1000
    # kW is power, NOT energy; intentionally never silently convert it.
    return None

def _direct_mi_per_kwh(record):
    if not record:
        return None
    value = _number(record["value"])
    unit = _unit(record["unit"])
    if value is None or value <= 0:
        return None
    if unit in {"mi/kwh", "mile/kwh", "miles/kwh", "mpkwh"}:
        return value
    if unit in {"km/kwh", "kilometer/kwh", "kilometre/kwh"}:
        return value * 0.6213711922
    if unit in {"kwh/100km", "kwh/100kilometers"}:
        return 62.13711922 / value
    if unit in {"kwh/100mi", "kwh/100miles"}:
        return 100 / value
    if unit in {"wh/mi", "wh/mile"}:
        return 1000 / value
    if unit in {"wh/km"}:
        return 1000 / value * 0.6213711922
    return None

def _reading_time(*records):
    """Use Ford's timestamp if available, otherwise first observation time."""
    for rec in records:
        value = rec.get("updated") if rec else None
        try:
            if isinstance(value, (int, float)):
                parsed = datetime.fromtimestamp(value / (1000 if value > 100_000_000_000 else 1), tz=timezone.utc)
            elif isinstance(value, str) and value.strip():
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if not parsed.tzinfo:
                    parsed = parsed.replace(tzinfo=timezone.utc)
            else:
                continue
            now = datetime.now(timezone.utc)
            if (now - timedelta(days=365)) <= parsed <= (now + timedelta(days=2)):
                return parsed.astimezone(LOCAL_ZONE)
        except (ValueError, OverflowError, OSError, TypeError):
            pass
    return datetime.now(LOCAL_ZONE)

def _trip_measurement(raw):
    """Return a completed, unit-labeled trip; don't infer efficiency from SOC."""
    distance_rec = _metric_record(raw, ("tripDistanceTraveled", "xevTripDistanceTraveled", "tripDistance"))
    energy_rec = _metric_record(raw, ("tripEnergyConsumed", "xevTripEnergyConsumed", "tripElectricalEnergyConsumed"))
    mi = _miles(distance_rec)
    kwh = _kilowatt_hours(energy_rec)
    dt, et = _parsed_time(distance_rec.get("updated")) if distance_rec else None, _parsed_time(energy_rec.get("updated")) if energy_rec else None
    if mi is not None and kwh is not None and dt and et and abs((dt-et).total_seconds()) <= 120 and 0.05 <= mi / kwh <= 15:
        reading = dt.astimezone(LOCAL_ZONE)
        identity = ("trip", distance_rec.get("updated"), energy_rec.get("updated"), round(mi, 3), round(kwh, 4))
        return identity, reading, mi, kwh, None
    # If Ford provides direct electric efficiency with an EXPLICIT compatible
    # unit, record distinct trip updates. Numeric scores and mpg are excluded.
    efficiency_rec = _metric_record(raw, ("tripEfficiency", "tripElectricalEfficiency", "xevTripEfficiency", "xevEnergyConsumption"))
    reported = _direct_mi_per_kwh(efficiency_rec)
    if reported is not None and _parsed_time(efficiency_rec.get("updated")) and 0.05 <= reported <= 15:
        reading = _parsed_time(efficiency_rec["updated"]).astimezone(LOCAL_ZONE)
        identity = ("direct", efficiency_rec.get("key"), efficiency_rec.get("updated"), round(reported, 4))
        return identity, reading, None, None, reported
    return None

def _store_efficiency(con, raw):
    reading = _trip_measurement(raw)
    if reading:
        identity, date, mi, kwh, reported = reading
        fingerprint = hashlib.sha256(repr(identity).encode("utf-8")).hexdigest()
        con.execute("INSERT OR IGNORE INTO efficiency_trips VALUES (?,?,?,?,?)",
                    (fingerprint, date.date().isoformat(), mi, kwh, reported))

def _seven_day_charts(days=7):
    days = days if days in (7, 30, 90, 365) else 7
    today = datetime.now(LOCAL_ZONE).date()
    first = today - timedelta(days=days-1)
    first_local = datetime(first.year, first.month, first.day, tzinfo=LOCAL_ZONE)
    cutoff = first_local.astimezone(timezone.utc).isoformat(timespec="seconds")
    with sqlite3.connect(HISTORY_DB) as con:
        boundary = con.execute("SELECT MIN(observed_at) FROM normalized_history").fetchone()[0]
        samples = con.execute("SELECT ts, soc FROM history WHERE ts >= ? AND (? IS NULL OR ts < ?) AND soc IS NOT NULL ORDER BY ts", (cutoff, boundary, boundary)).fetchall()
        battery = {ts: {"ts": ts, "soc": soc, "time_basis": "legacy observation"} for ts, soc in samples}
        # Decode one snapshot at a time so yearly views do not load a year of
        # full multi-metric JSON into memory.
        for observed, raw_summary in con.execute("SELECT observed_at, summary_json FROM normalized_history WHERE observed_at >= ? ORDER BY observed_at", (cutoff,)):
            s = json.loads(raw_summary)
            soc = s.get("soc", {})
            ts = soc.get("updated") or observed
            if soc.get("available") and ts >= cutoff:
                battery[ts] = {"ts": ts, "soc": soc["value"], "time_basis": "vehicle" if soc.get("updated") else "observation"}
        trip_rows = con.execute("SELECT date_local, distance_mi, energy_kwh, direct_mi_kwh FROM efficiency_trips WHERE date_local >= ? ORDER BY date_local", (first.isoformat(),)).fetchall()
    groups = {}
    for date_key, miles, kwh, direct in trip_rows:
        group = groups.setdefault(date_key, {"miles": 0.0, "kwh": 0.0, "direct": []})
        if miles is not None and kwh is not None and miles > 0 and kwh > 0:
            group["miles"] += miles
            group["kwh"] += kwh
        elif direct is not None and direct > 0:
            group["direct"].append(direct)
    daily = []
    for i in range(days):
        day = (first + timedelta(days=i)).isoformat()
        group = groups.get(day, {})
        if group.get("kwh", 0) > 0:
            efficiency = group["miles"] / group["kwh"]
            kind = "measured-trip"
        elif group.get("direct"):
            efficiency = sum(group["direct"]) / len(group["direct"])
            kind = "reported-trip"
        else:
            efficiency = None
            kind = None
        daily.append({"day": day, "mi_per_kwh": round(efficiency, 3) if efficiency is not None else None, "kind": kind})
    points = sorted(battery.values(), key=lambda x: x["ts"])
    if len(points) > 2400:
        # Preserve min/max within each bucket, including the latest observation.
        stride = math.ceil(len(points) / 1100)
        points = sorted({p["ts"]: p for i in range(0, len(points), stride) for p in (min(points[i:i+stride], key=lambda x: x["soc"]), max(points[i:i+stride], key=lambda x: x["soc"]), points[min(i+stride, len(points))-1])}.values(), key=lambda x: x["ts"])
    return {"days": days, "battery": points, "efficiency": daily,
            "battery_source": "Ford state-of-charge history", "efficiency_source": "Ford trip distance / energy or explicitly unit-labeled trip efficiency"}

def _save_normalized(con, summary, observed):
    safe = {k: v for k, v in summary.items() if k != "vin_suffix"}
    encoded = json.dumps(safe, sort_keys=True, allow_nan=False)
    fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
    con.execute("INSERT OR IGNORE INTO normalized_history VALUES (?,?,?,?)", (fingerprint, observed, summary.get("vehicle_updated"), encoded))

def _capabilities(raw, summary=None):
    source = _source(raw)
    s = summary if summary is not None else _summarize(raw)
    report = []
    for key, (names, kind, native) in METRICS.items():
        item = s[key]
        report.append({"feature": key, "field": item["source_field"], "present": any(_records(source, name) for name in names),
                       "usable": item["available"], "updated": item["updated"], "source_unit": item["source_unit"],
                       "assumed_unit": item["assumed_unit"], "display_unit": item["unit"], "reason": item["reason"]})
    for key, field in (("tires", "tirePressure"), ("locks", "doorLockStatus"), ("doors", "doorStatus"), ("windows", "windowStatus")):
        items = list(s[key].values()) if isinstance(s[key], dict) else s[key]
        usable = [x for x in items if x["available"]]
        report.append({"feature": key, "field": field, "present": bool(_records(source, field)), "usable": bool(usable),
                       "count": len(usable), "reason": "" if usable else "No usable readings in the latest response"})
    report.extend([
        {"feature": "trip_efficiency", "field": "trip distance + energy / unit-labeled trip efficiency", "present": bool(_metric_record(raw, ("tripDistanceTraveled", "xevTripDistanceTraveled", "tripDistance", "tripEfficiency", "tripElectricalEfficiency", "xevTripEfficiency", "xevEnergyConsumption"))), "usable": bool(_trip_measurement(raw)), "reason": "The trip chart uses saved completed trips with compatible explicit units; it appears when supported records exist in the selected period"},
        {"feature": "target_charge", "field": "", "present": False, "usable": False, "reason": "Not verified in the supplied vehicle response; no target-charge card added"},
        {"feature": "charging_costs", "field": "xevBatteryChargerEnergyOutput", "present": bool(_records(source, "xevBatteryChargerEnergyOutput")), "usable": False, "reason": "Session boundaries, energy units and wall-meter billing are not verified; cost totals withheld"},
    ])
    return {"metric_count": len(source.get("metrics", {})), "vehicle_updated": s["vehicle_updated"], "features": report,
            "scope": "Observed data from the latest saved Ford response; availability can change while parked, driving or charging"}

def _alerts(summary):
    alerts = []
    now = datetime.now(timezone.utc)
    soc = summary["soc"]
    if soc["available"] and soc["value"] < 20:
        alerts.append({"level": "warning", "message": "Last reported battery level is below 20%.", "updated": soc["updated"]})
    for wheel, tire in summary["tires"].items():
        status = tire["status"]
        if status["available"] and any(x in status["value"].upper() for x in ("LOW", "FAULT", "WARNING")):
            alerts.append({"level": "warning", "message": wheel.replace("_", " ").title() + ": Ford TPMS reports " + status["value"].lower().replace("_", " "), "updated": status["updated"]})
    charge = summary["charge_status"]
    if charge["available"] and any(x in charge["value"] for x in ("FAULT", "ERROR", "INTERRUPT")):
        alerts.append({"level": "warning", "message": "Ford reports a charging issue: " + charge["value"].lower().replace("_", " "), "updated": charge["updated"]})
    old = [name for name in ("battery_temp", "battery_voltage", "capacity") if summary[name]["available"] and _parsed_time(summary[name]["updated"]) and (now - _parsed_time(summary[name]["updated"])).total_seconds() > 86400]
    if old:
        alerts.append({"level": "info", "message": "Some HV battery readings are over a day old. See the timestamp under each reading.", "updated": None})
    return alerts

def _status():
    with LOCK:
        raw = _load(TELEMETRY_PATH, {})
        summary = _summarize(raw)
        tokens = _load(TOKEN_PATH, {})
        with sqlite3.connect(HISTORY_DB) as con:
            recent_rows = con.execute("SELECT observed_at, summary_json FROM normalized_history ORDER BY observed_at DESC LIMIT 100").fetchall()
    has_data = bool(_source(raw).get("metrics"))
    success = _parsed_time(LAST_POLL)
    recent = bool(success and (datetime.now(timezone.utc) - success).total_seconds() < max(600, int(CFG.get("poll_seconds", 180)) * 3))
    connected = bool(tokens.get("access_token") and recent and not LAST_ERROR and has_data)
    history = [{"ts": ts, **{out: json.loads(encoded).get(key, {}).get("value") for out, key in (("soc", "soc"), ("range", "range"), ("odo", "odometer"), ("capacity", "capacity"), ("health", "health"))}, "distance_unit": "mi"} for ts, encoded in reversed(recent_rows)]
    return {"version": VERSION, "connected": connected, "authorized": bool(tokens.get("access_token")), "has_data": has_data, "history": history,
            "summary": summary, "last_poll": LAST_POLL, "last_error": LAST_ERROR,
            "capabilities": _capabilities(raw, summary), "alerts": _alerts(summary), "callback_uri": CFG["redirect_uri"]}

def _history_csv(days=365):
    return b"".join(_history_csv_chunks(days))

def _history_csv_chunks(days=365):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    end = datetime.now(timezone.utc).isoformat()
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["observed_at_utc", "ford_vehicle_at_utc", "metric", "position", "value", "unit", "ford_metric_at_utc", "source_field", "source_unit", "assumed_native_unit"])
    yield b"\xef\xbb\xbf" + out.getvalue().encode("utf-8")
    out.seek(0); out.truncate(0)
    after, fingerprint = cutoff, ""
    while True:
        # Bounded batches let polling continue while a year of readings downloads.
        with LOCK, sqlite3.connect(HISTORY_DB) as con:
            batch = con.execute("SELECT observed_at, fingerprint, vehicle_at, summary_json FROM normalized_history WHERE observed_at >= ? AND observed_at <= ? AND (observed_at > ? OR (observed_at = ? AND fingerprint > ?)) ORDER BY observed_at, fingerprint LIMIT 250", (cutoff, end, after, after, fingerprint)).fetchall()
        if not batch:
            break
        for observed, fingerprint, vehicle_at, encoded in batch:
            summary = json.loads(encoded)
            for key, item in summary.items():
                if key == "tires":
                    items = list(item.items())
                elif isinstance(item, list):
                    items = [(x.get("position", ""), x) for x in item]
                elif isinstance(item, dict):
                    items = [("", item)]
                else:
                    continue
                for position, reading in items:
                    if reading.get("available"):
                        # Only normalized, allowlisted data is exported; omit VIN, GPS and secrets.
                        row = [observed, vehicle_at, key, position, reading["value"], reading["unit"], reading.get("updated"), reading.get("source_field"), reading.get("source_unit"), reading.get("assumed_unit")]
                        writer.writerow(["'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v for v in row])
            yield out.getvalue().encode("utf-8")
            out.seek(0); out.truncate(0)
        after = batch[-1][0]

def _efficiency_csv(days=365):
    cutoff = (datetime.now(LOCAL_ZONE).date() - timedelta(days=days-1)).isoformat()
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["date_America_New_York", "distance_mi", "energy_kwh", "reported_mi_per_kwh"])
    with sqlite3.connect(HISTORY_DB) as con:
        writer.writerows(con.execute("SELECT date_local, distance_mi, energy_kwh, direct_mi_kwh FROM efficiency_trips WHERE date_local >= ? ORDER BY date_local", (cutoff,)))
    return out.getvalue().encode("utf-8-sig")

def poll():
    global LAST_POLL, LAST_ERROR
    if not POLL_LOCK.acquire(blocking=False):
        return
    try:
        access = _token()
        if not access:
            LAST_ERROR = "Ford authorization is required before telemetry can be fetched."
            return
        garage = _request("GET", API_URL + "/garage", access_token=access)
        telemetry = _request("GET", API_URL + "/telemetry", access_token=access)
        if not _source(telemetry).get("metrics"):
            raise FordError("response", "No vehicle metrics in this Ford response. Previous readings retained.")
        summary = _summarize(telemetry)
        with LOCK:
            _write_private(GARAGE_PATH, garage)
            _write_private(TELEMETRY_PATH, telemetry)
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            # Keep the original history table's raw-value semantics for rollback.
            source = _source(telemetry)
            numeric = []
            for key in ("soc", "range", "odometer", "capacity", "health"):
                rec = _metric_record(source, METRICS[key][0])
                numeric.append(_number(rec["value"]) if rec else None)
            with sqlite3.connect(HISTORY_DB) as con:
                con.execute("INSERT OR REPLACE INTO history VALUES (?,?,?,?,?,?)", (now, *numeric))
                _save_normalized(con, summary, now)
                _store_efficiency(con, telemetry)
            LAST_POLL = now
            LAST_ERROR = ""
            _write_private(POLL_STATE_PATH, {"last_success": now})
    finally:
        POLL_LOCK.release()

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
    return json.dumps(obj, ensure_ascii=False, allow_nan=False).encode("utf-8")

def _login_page(error=""):
    banner = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return f'''<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><title>Mach-E · Unlock</title>
    <style>body{{background:#10131a;color:#eaf0f9;display:grid;place-items:center;min-height:96vh;font:16px system-ui}}main{{width:min(420px,90vw);padding:40px;background:#1d2330;border:1px solid #333d4f;border-radius:22px}}input,button{{width:100%;padding:14px;border-radius:8px;box-sizing:border-box;font:inherit;margin:8px 0}}input{{background:#111824;color:white;border:1px solid #526076}}button{{background:#7fcbbb;border:0;color:#0b1c1c;font-weight:bold;cursor:pointer}}.error{{color:#ff9797}}</style>
    <link rel="icon" href="/web/icon-192.png"><link rel="apple-touch-icon" href="/web/icon-192.png"></head><body><main><img src="/web/mache-gt.png" alt="Glacier Grey Mach-E GT" width="230" height="150" style="object-fit:contain;max-width:100%"><h1>Mach-E GT</h1><p>Private dashboard · Synology NAS</p>{banner}<form method="POST" action="/login"><input type="password" name="password" placeholder="Dashboard password" required autofocus><button>Unlock dashboard</button></form></main></body></html>'''

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        # Do not log URL query parameters. OAuth callback query contains a code.
        return

    def send(self, content, status=200, content_type="text/html; charset=utf-8", cookie=None, filename=None):
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
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def move(self, path, cookie=None):
        self.send_response(303)
        self.send_header("Location", path)
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def send_csv_stream(self, chunks, filename):
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            for part in chunks:
                self.wfile.write(part)
        except (BrokenPipeError, ConnectionResetError):
            pass

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
        public_assets = {"/web/mache-gt.png": "image/png", "/web/icon-192.png": "image/png", "/web/icon-512.png": "image/png", "/manifest.webmanifest": "application/manifest+json"}
        if path.path in public_assets:
            asset = ROOT / (path.path.lstrip("/") if path.path.startswith("/web/") else "web/manifest.webmanifest")
            if asset.is_file():
                return self.send(asset.read_bytes(), content_type=public_assets[path.path])
            return self.send("Asset not installed", status=404)
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
            return self.send(_as_json(_status()), content_type="application/json")
        if path.path == "/api/charts":
            days = self._days(path.query)
            with LOCK:
                charts = _seven_day_charts(days)
            return self.send(_as_json(charts), content_type="application/json")
        if path.path == "/api/capabilities":
            with LOCK:
                report = _capabilities(_load(TELEMETRY_PATH, {}))
            return self.send(_as_json(report), content_type="application/json", filename="ford-data-support.json")
        if path.path in ("/api/export.csv", "/api/efficiency.csv"):
            days = self._days(path.query, 365)
            name = f"mache-telemetry-{days}d.csv" if path.path == "/api/export.csv" else f"mache-efficiency-{days}d.csv"
            if path.path == "/api/export.csv":
                return self.send_csv_stream(_history_csv_chunks(days), name)
            with LOCK:
                body = _efficiency_csv(days)
            return self.send(body, content_type="text/csv; charset=utf-8", filename=name)
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

    @staticmethod
    def _days(query, default=7):
        try:
            days = int(parse_qs(query).get("days", [default])[0])
        except (ValueError, TypeError):
            return default
        return days if days in (7, 30, 90, 365) else default

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
        if path in ("/poll", "/api/poll"):
            try:
                poll()
            except (FordError, ValueError) as err:
                LAST_ERROR = scrub(err)
            except Exception as err:
                LAST_ERROR = type(err).__name__
                LOG.exception("Manual polling failed")
            if path == "/api/poll":
                return self.send(_as_json(_status()), content_type="application/json")
            return self.move("/")
        return self.send("Not found", status=404)

if __name__ == "__main__":
    LOG.info("Starting private Mach-E dashboard on port %d", PORT)
    threading.Thread(target=background, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
