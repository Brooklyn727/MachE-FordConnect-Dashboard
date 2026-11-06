# v1.3.0 validation

Validation completed November 6, 2026 against the owner's supplied Ford response. This is captured-response verification, not a direct live-account call or an installation on the owner's NAS.

## Passed

- 26 Python unittest regressions: captured conversions, meter/km/mi variants, Celsius/Fahrenheit/Kelvin, PSI/kPa/Pa/bar, four wheel positions and placards, independent HV/12V fields, timestamp preservation, matching charger readings, zero/invalid/missing data, health/unit withholding, multi-vehicle isolation, native-unit overrides, additive SQLite initialization, token preservation, empty-response retention, connection-state honesty, normalized-history deduplication, streamed CSV batch boundaries, trip validation, old-trip dates, history ranges and authenticated HTTP/CSRF routes.
- JavaScript syntax check with Node.
- Browser smoke test using real local HTTP endpoints and Chromium, with all Ford network calls mocked: password login, rendered captured readings, four tire cards, 8.7 kW charger output, 204-minute estimate, hidden unsupported fields, 365-day selector, manual refresh, zero SOC, sparse/empty data and offline indication.
- Desktop rendering at 1440 px and phone widths of 390/375 px. No horizontal overflow and no browser JavaScript errors.
- Illustration, favicon/home-screen icons, CSS, JavaScript and manifest routes all returned HTTP 200 in authenticated route checks.
- Git whitespace/diff check.

## Preserved in the upgrade path

Tests create an existing history database before loading the new server and verify that its rows survive. Tests confirm that polling leaves stored tokens in place and keeps the original history table's raw-unit semantics. All additional normalized history is additive. The archive intentionally contains no config.json, data directory, tokens, user VIN, location or actual credentials.

## Not performed here

- Connecting directly to the owner's Ford account with a live token.
- Accessing or modifying the owner's Synology/Portainer deployment.
- Verifying charger-energy units/counter resets across an actual charging session.
- Estimating battery health, target charge or billing totals from missing data.

After installation, Fetch latest telemetry performs the actual live API check on the NAS using its existing saved Ford token. The connection indicator and data-support disclosure expose the result.
