# Changelog

## 1.3.0 — 2026-11-06

- Restored all four tire pressures in PSI, Ford TPMS status and front/rear cold placards.
- Centralized unit validation/conversion on the server; preserved miles/°F across cards/history/CSV and retained labeled native defaults for unitless Ford data.
- Added per-reading timestamps and age indicators, exposing the supplied response's older October 15 HV values.
- Added verified charger voltage/current, calculated output kW, time-to-full, station/type, and separate 12V voltage/SOC/status.
- Added reported doors, windows, inferred lock status, hood, alarm, ignition, remaining energy and HV performance status.
- Preserved both chart implementations and prior history; added 30/90/365-day ranges and normalized readings/trip CSV exports.
- Added conditional rendering and an authenticated data-support report. Unsupported health/target/efficiency fields do not create empty metric cards. Unverified charger-energy units and cost/session totals remain withheld.
- Added on-dashboard warnings, honest API connectivity state, responsive layouts and Glacier Grey GT illustration/icons.
- Added an additive normalized-history table, persisted poll-success time, bounded CSV streaming, poll serialization and empty-response retention.
- Added offline regression coverage and a tagged repository history from the uploaded baseline.

## 1.2-uploaded — preserved source baseline

- Uploaded server.py and three web files recorded unchanged before the update.
- Existing miles/°F frontend conversion, HV battery voltage, SOC history and trip-efficiency chart code retained as the baseline for review/rollback.
