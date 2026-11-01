// Mach-E telemetry display — US units with explicit assumptions for FordConnect API.
// Ford's telemetry commonly reports distances in km and temperatures in °C,
// even when individual metric objects omit the `unit` field.
const E = (id) => document.getElementById(id);
const MILES_PER_KM = 0.621371192237334;
const fmt = (n, decimals = 0) => n.toLocaleString('en-US', {
  minimumFractionDigits: 0,
  maximumFractionDigits: decimals,
});
function hasValue(item) {
  return !!item && item.value !== null && item.value !== undefined && item.value !== '';
}
function metricNumber(item) {
  if (!hasValue(item)) return null;
  const n = Number(item.value);
  return Number.isFinite(n) ? n : null;
}
function metricUnit(item) {
  return String(item?.unit || '').trim().toLowerCase();
}
function distance(id, item, unitId) {
  const n = metricNumber(item);
  if (n === null) {
    E(id).textContent = '—';
    E(unitId).textContent = 'Not reported';
    return;
  }
  const unit = metricUnit(item);
  // FordConnect supplies km, including some responses which omit a unit label.
  const isMiles = ['mi', 'mile', 'miles'].includes(unit);
  E(id).textContent = fmt(isMiles ? n : n * MILES_PER_KM);
  E(unitId).textContent = 'miles';
}
function temperature(id, item) {
  const n = metricNumber(item);
  if (n === null) {
    E(id).textContent = '—';
    return;
  }
  const unit = metricUnit(item);
  // Unless Ford explicitly reports Fahrenheit, assume its standard °C readings.
  const isFahrenheit = ['f', '°f', 'fahrenheit', 'degf'].includes(unit);
  const valueF = isFahrenheit ? n : (n * 9 / 5 + 32);
  E(id).textContent = `${fmt(valueF, 1)} °F`;
}
function batteryCapacity(item) {
  const n = metricNumber(item);
  if (n === null) {
    E('capacity').textContent = '—';
    E('capacity-unit').textContent = 'Not reported';
    return;
  }
  const unit = metricUnit(item);
  // Some Ford variants expose Wh; others expose kWh or omit the unit.
  const capacityKwh = unit === 'wh' || (unit === '' && n > 1000) ? n / 1000 : n;
  E('capacity').textContent = fmt(capacityKwh, 1);
  E('capacity-unit').textContent = 'kWh (capacity, not battery health)';
}
function batteryHealth(item) {
  const n = metricNumber(item);
  if (n === null) {
    E('health').textContent = '—';
    return;
  }
  E('health').textContent = `${fmt(n, 1)}%`;
}
function chart(history) {
  const canvas = E('chart');
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.clientWidth || 900;
  const height = 230;
  canvas.width = width * dpr;
  canvas.height = height * dpr;
  const c = canvas.getContext('2d');
  c.scale(dpr, dpr);
  c.clearRect(0, 0, width, height);
  const vals = history.filter((x) => x.soc !== null && Number.isFinite(+x.soc));
  c.strokeStyle = '#2e3946';
  c.lineWidth = 1;
  c.font = '11px system-ui';
  c.fillStyle = '#8ea0b0';
  for (let i = 0; i <= 4; i++) {
    const y = 25 + i * 40;
    c.beginPath();
    c.moveTo(30, y);
    c.lineTo(width - 15, y);
    c.stroke();
    c.fillText(String(100 - i * 25), 0, y + 4);
  }
  if (vals.length < 2) {
    c.fillStyle = '#8096a5';
    c.fillText('SOC history will appear after multiple Ford telemetry snapshots', 45, 110);
    return;
  }
  c.strokeStyle = '#79dfc3';
  c.lineWidth = 3;
  c.beginPath();
  vals.forEach((p, i) => {
    const x = 32 + i * (width - 55) / (vals.length - 1);
    const y = 25 + (100 - Math.max(0, Math.min(100, +p.soc))) * 1.6;
    if (i === 0) c.moveTo(x, y);
    else c.lineTo(x, y);
  });
  c.stroke();
}

// Diagnostics: do not silently present older OEM data as live.
const KPA_TO_PSI = 0.1450377377;
function ageLabel(iso) {
  if (!iso) return 'Reading time unavailable';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return 'Reading time unavailable';
  const minutes = Math.max(0, Math.floor((Date.now() - d.getTime()) / 60000));
  if (minutes < 2) return 'Updated just now';
  if (minutes < 60) return `Updated ${minutes} min ago`;
  if (minutes < 48 * 60) return `Updated ${Math.floor(minutes / 60)}h ago`;
  return `Updated ${d.toLocaleDateString('en-US', {month:'short',day:'numeric'})} at ${d.toLocaleTimeString('en-US', {hour:'numeric',minute:'2-digit'})}`;
}
function readValue(item, suffix = '', digits = 0) {
  const value = metricNumber(item);
  return value === null ? '—' : `${fmt(value, digits)}${suffix}`;
}
function setDiagnostic(id, item, suffix='', digits=0, asText=false) {
  if (!E(id)) return;
  E(id).textContent = asText ? (item?.value ?? '—').toString().replaceAll('_',' ') : readValue(item,suffix,digits);
  const age = E(`${id}-age`);
  if (age) age.textContent = ageLabel(item?.updated_at);
}
function tirePsi(item) {
  const n = metricNumber(item);
  if (n === null) return null;
  const unit = String(item?.unit || '').trim().toLowerCase();
  return ['psi', 'lb/in2'].includes(unit) ? n : n * KPA_TO_PSI;
}
function tireData(tires) {
  const labels = [
    ['FRONT_LEFT','tire-fl'],['FRONT_RIGHT','tire-fr'],
    ['REAR_LEFT','tire-rl'],['REAR_RIGHT','tire-rr'],
  ];
  const times = [];
  let placard = null;
  for (const [key,id] of labels) {
    const item = tires?.[key];
    const psi = tirePsi(item);
    E(id).textContent = psi === null ? '—' : `${fmt(psi, 1)}`;
    E(`${id}-age`).textContent = item?.status ? item.status.replaceAll('_',' ') : 'Not reported';
    E(`${id}`).classList.toggle('tire-notice', item?.status && !['NORMAL','NORMAL_OPERATION'].includes(item.status));
    if (item?.updated_at) times.push(item.updated_at);
    if (item?.placard && placard === null) placard = Number(item.placard) * KPA_TO_PSI;
  }
  E('tire-placard').textContent = placard === null ? 'Placard: not reported' : `Ford placard: ${fmt(placard,0)} PSI cold`;
  E('tire-timestamp').textContent = times.length ? ageLabel(times.sort().at(-1)) : 'No tire data';
}
function updateDiagnostics(summary) {
  if (!summary) return;
  tireData(summary.tires);
  setDiagnostic('battery-12v', summary.battery_12v, ' V', 2);
  setDiagnostic('battery-performance', summary.battery_performance, '', 0, true);
  setDiagnostic('door-lock', summary.door_lock, '', 0, true);
  setDiagnostic('doors', summary.doors, '', 0, true);
  setDiagnostic('hood', summary.hood, '', 0, true);
  setDiagnostic('ignition', summary.ignition, '', 0, true);
  setDiagnostic('charger-type', summary.charger_type, '', 0, true);
  setDiagnostic('charger-voltage', summary.charger_voltage, ' V', 1);
  setDiagnostic('charger-current', summary.charger_current, ' A', 1);
  E('soc-age').textContent = `Battery SOC: ${ageLabel(summary.soc?.updated_at)}`;
}

async function update() {
  try {
    const res = await fetch('/api/status', { cache: 'no-store' });
    if (!res.ok) return;
    const data = await res.json();
    const s = data.summary;
    E('connection').textContent = data.connected ? 'FORD CONNECTED' : 'NOT CONNECTED';
    E('connection').className = data.connected ? 'online connected' : 'online';
    const soc = metricNumber(s.soc);
    E('soc').textContent = soc !== null ? Math.round(soc) : '—';
    E('soc-bar').style.width = (soc !== null ? Math.min(100, Math.max(0, soc)) : 0) + '%';
    distance('range', s.range, 'range-unit');
    distance('odo', s.odometer, 'odo-unit');
    batteryCapacity(s.capacity);
    batteryHealth(s.health);
    temperature('battery-temp', s.battery_temp);
    temperature('outside-temp', s.outdoor_temp);
    E('plug-status').textContent = s.plug_status?.value ?? '—';
    E('charge-status').textContent = s.charge_status?.value ?? '—';
    E('charge').textContent = s.charge_status?.value ?? 'Charge status unavailable';
    E('vin').textContent = s.vin_suffix ? 'VIN ···· ' + s.vin_suffix : 'VIN · —';
    E('updated').textContent = data.last_poll ? new Date(data.last_poll).toLocaleString() : 'Not yet synced';
    E('diag').textContent = data.last_error || (data.connected ? 'Ford OAuth token saved. Fetching telemetry.' : 'Not connected: verify callback URI and authorize using the form above.');
    E('diag').className = data.last_error ? 'diag error' : 'diag';
    chart(data.history || []);
    updateDiagnostics(s);
  } catch (err) {
    E('diag').textContent = 'Dashboard network error; check container status.';
  }
}
update();
setInterval(update, 30000);
