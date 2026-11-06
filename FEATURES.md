# Features future builds must preserve

## Existing behavior

- Local password/session login, logout, CSRF protection and authenticated data routes.
- Ford OAuth authorization, manual callback, token refresh and existing private files.
- Existing polling interval (minimum 120 seconds), manual fetch and raw-data view.
- SOC, range, odometer, reported capacity and reported battery health when supplied.
- Fahrenheit temperature handling and HV battery voltage sourced only from `xevBatteryVoltage`.
- Dark/mint theme, 7-day battery chart and real-trip mi/kWh chart/storage.
- Original SQLite tables, rows and raw-unit semantics; no destructive migration.

## v1.3.0 guarantees

- Four wheel positions, PSI pressure, per-wheel TPMS and Ford cold placards.
- One server-side unit conversion shared by cards/history/CSV; explicit units win.
- Unitless odometer uses native km, checked against the owner's approximate instrument reading.
- Unknown units/statuses and invalid/nonfinite numbers do not become zero or bogus cards.
- Missing measurements have visible diagnostic reasons and hidden main-dashboard cards.
- Per-metric reading ages; a successful poll never refreshes an old vehicle timestamp.
- Separate 12V, HV pack and charger voltage/current roles.
- Charger-output power only from compatible units and matching charger timestamps.
- Time to full is labeled as Ford's estimate at its reading time, never a fresh countdown from an old record.
- No health %, charging costs, session kWh, charge target or trip efficiency inferred from SOC alone.
- Unknown individual door locks never become locked/secure. Ford's inferred status is labeled.
- 7/30/90/365-day views, normalized CSV and retained trip export.
- A saved token alone does not establish that Ford's API is responding.
- Desktop/mobile layouts, illustration/icons and accessible keyboard controls.
- On-dashboard alerts only; no external messages or vehicle-control commands.

A feature hidden because Ford omitted a reading is not deleted: maintain its parser,
renderer, diagnostics and tests. If semantics or units are uncertain, withhold the
value and explain why in diagnostics. Never fill a gap with invented telemetry.
