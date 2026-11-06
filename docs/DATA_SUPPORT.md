# Ford data verification — supplied vehicle response

Checked on November 6, 2026. Evidence is the actual response supplied by the owner from the existing authenticated `/api/raw` page, plus the owner's confirmation of the approximate instrument odometer. No live Ford credential or token was provided to this workspace, and no direct live-account test was made here.

The response identified a 2025 Mustang Mach-E BEV in Glacier Gray. It contained 60 telemetry metric groups. The test fixture retains only relevant measurements and excludes VIN, vehicle IDs, location/GPS, OEM correlation IDs, schedules, and unrelated signals. Production never reads the fixture.

## Supported by the actual response

| Reading | Source | Captured result / handling |
|---|---|---|
| HV charge | `xevBatteryStateOfCharge` | 55.5%; displayed rounded as before |
| Range | `xevBatteryRange` | 206.40001 native km → 128.25 mi |
| Odometer | `odometer` | 27,612 native km → 17,157.30 mi; owner confirmed instrument shows roughly 17,158 mi |
| Reported pack capacity | `xevBatteryCapacity` | 91 kWh; dated October 15, so marked older |
| HV temperature | `xevBatteryTemperature` | 21.5 native °C → 70.7 °F; dated October 15 |
| HV pack voltage | `xevBatteryVoltage` | 363 V; dated October 15, not substituted with the fresher 12V value |
| Outside temperature | `outsideTemperature` | 18.25 native °C → 64.85 °F |
| All four pressures | `tirePressure[]`, `vehicleWheel` | About 35.1 / 34.7 / 34.8 / 34.7 PSI in FL / FR / RL / RR order |
| Cold placards | `wheelPlacardFront`, `wheelPlacardRear` inside tire records | About 39 PSI for both axles; values come from Ford |
| TPMS | `tirePressureStatus[]`, `tirePressureSystemStatus[]` | Each wheel NORMAL; system NORMAL_OPERATION; no invented low-pressure alert |
| Charger voltage/current | `xevBatteryChargerVoltageOutput`, `xevBatteryChargerCurrentOutput` | 348 V and 25 A from matching timestamps |
| Charger output power | Calculated from those two readings | 8.7 kW; explicitly calculated output, not wall-meter demand |
| Time to full | `xevBatteryTimeToFullCharge` | 204 native minutes → 3 hr 24 min, at Ford's source reading time |
| Charging/plug state | `xevBatteryChargeDisplayStatus`, `xevPlugChargerStatus` | IN_PROGRESS and CONNECTED |
| Charger/station | `xevChargeStationPowerType`, `xevChargeStationCommunicationStatus` | AC_BASIC and STATION_READY |
| HV performance | `xevBatteryPerformanceStatus` | NORMAL; this is not a numerical battery-health percentage |
| Remaining energy | `xevBatteryEnergyRemaining` | 42.6 native kWh, with its individual source timestamp |
| 12V system | `batteryVoltage`, `batteryStateOfCharge`, `batteryLoadStatus`, explicitly tagged PRIMARY_BATTERY | 14.625 V, 86%, NORMAL_OPERATION; always separate from HV |
| Locks | `doorLockStatus[]` | ALL_DOORS=LOCKED, determinationMethod=INFERRED. Individual UNKNOWN entries are withheld |
| Doors/hatch/hood | `doorStatus[]`, `hoodStatus` | Known CLOSED entries retained; no conclusion about unreported doors |
| Windows | `windowStatus[].value.doubleRange` | Four supplied zero/zero ranges indicate closed; missing bounds do not become closed |
| Alarm / ignition | `alarmStatus`, `ignitionStatus` | DISARMED / OFF |

All of the supplied numeric readings above omitted a `unit`/`uom`. Native defaults are therefore labeled, not represented as explicitly unit-tagged values. The owner checked the native-km odometer conversion against the car. The car's `displaySystemOfMeasure=IMPERIAL` does not redefine the FordConnect payload's native units.

## Withheld or conditional

| Feature | Evidence / decision |
|---|---|
| Battery-health percentage | No supported health metric was present. 91 kWh capacity is not used to invent health or degradation. Existing health parsing/display is retained for future genuine readings. |
| Target charge level | Not present in the provided telemetry. No speculative endpoint or blank target card added. |
| Charging energy, bills, session totals | `xevBatteryChargerEnergyOutput` exists with value 7.0364052734375 but no unit. A single response also cannot establish reset/session semantics. It is withheld rather than silently treated as billed kWh. Explicit compatible energy units can enable a reported-energy reading later; costs/session totals still require verification. |
| Trip efficiency | No completed trip distance plus consumed energy with explicit units, or usable unit-labeled efficiency, was returned. Zero regeneration/distance fields and `tripFuelEconomy=0` do not establish mi/kWh. Prior chart/storage/history are preserved and display when supported saved data exists. |
| Historical data predating the install | Existing recorded battery/trip history is retained. New normalized data begins after a successful v1.3 poll. No retrospective charge or mileage records are fabricated. |
| Notifications | Alerts appear on this dashboard only. No email/push service or delivery was configured. |

## Runtime verification

`GET /api/status` reports authorization, latest successful poll, errors and per-reading source timestamps. A saved access token alone cannot produce the green FORD RESPONDING badge. Each successful poll re-runs the validation against the actual returned metrics. Empty or malformed telemetry cannot replace the previous saved readings.

`GET /api/capabilities` gives an authenticated, identifier-free support report. Main-dashboard cards appear only for usable measurements; hidden fields retain a diagnostic reason. Reading age is calculated from each metric's own timestamp, not the dashboard's poll time or the top-level response timestamp.

## Public cross-checks

These explain the endpoint and help corroborate native field handling; they do not substitute for the owner's actual response:

- [Ford Developer Marketplace — APIs](https://developer.ford.com/apis): FordConnect Query uses consent-based access. Detailed API documentation requires sign-in.
- [FordConnect Query implementation by its Home Assistant integration author](https://github.com/marq24/ha-fordconnect-query/blob/main/custom_components/fordconnect_query/fordpass_handler.py): native km, Celsius and kPa handling; tagged lists for wheels/doors/windows; minute-based time-to-full handling. This is independent implementation evidence, not an official Ford schema guarantee.
- [evcc's FordConnect Query types](https://github.com/evcc-io/evcc/blob/master/vehicle/ford/query/types.go): the same telemetry endpoint's EV charging fields and structured metrics.

No third-party integration code is copied into this release. The application remains the owner's supplied server/frontend with locally implemented additions.
