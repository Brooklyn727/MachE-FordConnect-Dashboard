"""Offline regression checks. All credentials and Ford HTTP responses are mocked."""
import copy
import csv
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from urllib.request import Request, build_opener, HTTPCookieProcessor
from urllib.error import HTTPError
from http.cookiejar import CookieJar

PROJECT = Path(__file__).resolve().parents[1]
CAPTURE = json.loads((PROJECT / 'tests/fixtures/captured-metrics.json').read_text())


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='mache-test-')
        self.root = Path(self.tmp.name)
        self.config = {'client_id': 'offline-client', 'client_secret': 'offline-secret',
                       'dashboard_password': 'offline-password-only', 'redirect_uri': 'http://localhost:8765/callback'}
        (self.root / 'config.json').write_text(json.dumps(self.config))
        # A preexisting v1.2 database must survive additive initialization.
        (self.root / 'data').mkdir()
        with sqlite3.connect(self.root / 'data/history.db') as con:
            con.execute('CREATE TABLE history (ts TEXT PRIMARY KEY, soc REAL, range_value REAL, odometer REAL, capacity REAL, health REAL)')
            legacy_time=(datetime.now(timezone.utc)-timedelta(days=20)).isoformat(timespec='seconds')
            con.execute('INSERT INTO history VALUES (?,?,?,?,?,?)', (legacy_time, 65, 200, 10000, 91, None))
        with patch.dict(os.environ, {'MACHE_ROOT': str(self.root)}):
            spec = importlib.util.spec_from_file_location('dashboard_test', PROJECT / 'server.py')
            self.app = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.app)
        self.capture = copy.deepcopy(CAPTURE)

    def tearDown(self):
        self.tmp.cleanup()

    def metric(self, value, unit=None):
        return {'value': value, 'unit': unit, 'updateTime': '2026-11-06T05:00:50Z'}

    def test_owner_confirmed_odometer_and_range(self):
        s = self.app._summarize(self.capture)
        self.assertAlmostEqual(s['odometer']['value'], 27612 / 1.609344, places=5)
        self.assertEqual(s['odometer']['unit'], 'mi')
        self.assertEqual(s['odometer']['assumed_unit'], 'km')
        self.assertAlmostEqual(s['range']['value'], 206.40001 / 1.609344, places=5)

    def test_distance_unit_variants_including_meters(self):
        for unit, value in [('m',1609.344),('meters',1609.344),('metres',1609.344),('km',1.609344),('KILOMETERS',1.609344),('mi',1),('miles',1)]:
            with self.subTest(unit=unit):
                self.assertAlmostEqual(self.app._convert(self.metric(value,unit),'distance')['value'], 1)

    def test_temperature_values_and_native_defaults(self):
        s = self.app._summarize(self.capture)
        self.assertEqual(s['battery_temp']['value'],70.7)
        self.assertAlmostEqual(s['outdoor_temp']['value'],64.85)
        for value,unit in [(0,'°C'),(32,'°F'),(273.15,'K')]:
            with self.subTest(unit=unit):
                self.assertAlmostEqual(self.app._convert(self.metric(value,unit),'temperature')['value'],32)

    def test_four_tires_and_native_placards(self):
        s = self.app._summarize(self.capture)
        self.assertEqual(len(s['tires']),4)
        self.assertAlmostEqual(s['tires']['FRONT_LEFT']['value'],35.0991,places=3)
        for tire in s['tires'].values():
            self.assertEqual(tire['unit'],'PSI')
            self.assertAlmostEqual(tire['placard']['value'],39,places=3)
            self.assertEqual(tire['status']['value'],'NORMAL')

    def test_pressure_unit_variants(self):
        for unit,value in [('PSI',1),('kPa',6.894757293),('Pa',6894.757293),('bar',0.06894757293)]:
            self.assertAlmostEqual(self.app._convert(self.metric(value,unit),'pressure')['value'],1,places=6)

    def test_hv_and_12v_are_kept_separate(self):
        s = self.app._summarize(self.capture)
        self.assertEqual(s['battery_voltage']['value'],363)
        self.assertEqual(s['aux_voltage']['value'],14.625)
        del self.capture['metrics']['xevBatteryVoltage']
        self.assertFalse(self.app._summarize(self.capture)['battery_voltage']['available'])
        self.capture['metrics']['batteryVoltage']['vehicleBattery']='HIGH_VOLTAGE'
        self.assertFalse(self.app._summarize(self.capture)['aux_voltage']['available'])

    def test_generic_auxiliary_health_cannot_become_hv_health(self):
        self.capture['metrics']['batteryStateOfHealth']={'value':95,'vehicleBattery':'PRIMARY_BATTERY'}
        self.assertFalse(self.app._summarize(self.capture)['health']['available'])
        self.capture['metrics']['batteryStateOfHealth']['vehicleBattery']='HIGH_VOLTAGE'
        self.assertEqual(self.app._summarize(self.capture)['health']['value'],95)

    def test_timestamps_remain_october_15_after_november_response(self):
        s = self.app._summarize(self.capture)
        self.assertTrue(s['battery_temp']['updated'].startswith('2026-10-15'))
        self.assertTrue(s['battery_voltage']['updated'].startswith('2026-10-15'))
        self.assertTrue(s['vehicle_updated'].startswith('2026-11-06'))

    def test_power_calculation_uses_matching_charger_readings(self):
        s=self.app._summarize(self.capture)
        self.assertEqual(s['charging_power']['value'],8.7)
        self.assertTrue(s['charging_power']['calculated'])
        self.capture['metrics']['xevBatteryChargerCurrentOutput']['updateTime']='2026-11-05T00:00:00Z'
        self.assertFalse(self.app._summarize(self.capture)['charging_power']['available'])

    def test_zero_is_a_real_value(self):
        for kind,unit in [('percent','%'),('distance','mi'),('pressure','psi'),('voltage','V'),('temperature','F')]:
            self.assertEqual(self.app._convert(self.metric(0,unit),kind)['value'],0)

    def test_invalid_numbers_and_unknown_units_are_withheld(self):
        for value in (True, '', 'bad', float('nan'), float('inf'), -1):
            self.assertFalse(self.app._convert(self.metric(value,'mi'),'distance')['available'])
        self.assertFalse(self.app._convert(self.metric(22,'parsecs'),'distance')['available'])
        self.assertFalse(self.app._convert(self.metric(101,'%'),'percent')['available'])

    def test_health_and_energy_are_not_invented(self):
        s = self.app._summarize(self.capture)
        self.assertFalse(s['health']['available'])
        self.assertFalse(s['charger_energy']['available'])
        self.capture['metrics']['xevBatteryChargerEnergyOutput']['unit']='Wh'
        self.assertAlmostEqual(self.app._summarize(self.capture)['charger_energy']['value'],0.0070364053)
        with sqlite3.connect(self.app.HISTORY_DB) as con:
            self.app._store_efficiency(con,self.capture)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM efficiency_trips').fetchone()[0],0)

    def test_status_lists_filter_unknown_values(self):
        s = self.app._summarize(self.capture)
        self.assertEqual(len([r for r in s['locks'] if r['available']]),2)
        self.assertEqual(len([r for r in s['windows'] if r['value']=='CLOSED']),4)
        self.assertEqual(len(s['doors']),6)
        raw={'metrics':{'windowStatus':[{'value':{'doubleRange':{}},'vehicleWindow':'FRONT_LEFT'}]}}
        self.assertFalse(self.app._summarize(raw)['windows'][0]['available'])

    def test_latest_record_is_selected(self):
        raw={'metrics':{'xevBatteryStateOfCharge':[
            {'value':10,'updateTime':'2026-11-05T00:00:00Z'},
            {'value':20,'updateTime':'2026-11-06T00:00:00Z'}]}}
        self.assertEqual(self.app._summarize(raw)['soc']['value'],20)

    def test_ambiguous_vehicles_are_not_combined(self):
        with self.assertRaises(ValueError):self.app._summarize([self.capture,self.capture])
        self.app.CFG['vehicle_vin']='EXAMPLE'
        car=copy.deepcopy(self.capture);car['vin']='EXAMPLE'
        self.assertEqual(self.app._summarize([{},car])['soc']['value'],55.5)

    def test_units_can_be_calibrated_without_ignoring_explicit_units(self):
        self.app.CFG['native_units']={'odometer':'mi'}
        self.assertEqual(self.app._summarize(self.capture)['odometer']['value'],27612)
        self.capture['metrics']['odometer']['unit']='km'
        self.assertAlmostEqual(self.app._summarize(self.capture)['odometer']['value'],17157.30136006)

    def test_additive_schema_preserves_existing_data(self):
        self.app._initialize_db()
        with sqlite3.connect(self.app.HISTORY_DB) as con:
            self.assertEqual(con.execute('SELECT odometer FROM history').fetchone()[0],10000)
            names={x[0] for x in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({'history','efficiency_trips','normalized_history'}<=names)

    def test_poll_keeps_tokens_and_raw_legacy_units(self):
        tokens={'access_token':'offline-token','refresh_token':'offline-refresh','obtained_at':time.time(),'expires_in':9999}
        self.app._write_private(self.app.TOKEN_PATH,tokens)
        def response(method,url,**kwargs):
            return {'modelName':'Mach-E'} if url.endswith('/garage') else self.capture
        with patch.object(self.app,'_request',side_effect=response):self.app.poll()
        self.assertEqual(json.loads(self.app.TOKEN_PATH.read_text()),tokens)
        self.assertTrue(self.app._status()['connected'])
        with sqlite3.connect(self.app.HISTORY_DB) as con:
            self.assertEqual(con.execute('SELECT odometer FROM history ORDER BY ts DESC LIMIT 1').fetchone()[0],27612)
            saved=json.loads(con.execute('SELECT summary_json FROM normalized_history').fetchone()[0])
            self.assertEqual(saved['odometer']['unit'],'mi')
            self.assertNotIn('vin_suffix',saved)
        self.assertEqual(json.loads(self.app.POLL_STATE_PATH.read_text())['last_success'],self.app.LAST_POLL)

    def test_empty_ford_response_keeps_previous_readings(self):
        self.app._write_private(self.app.TELEMETRY_PATH,self.capture)
        with patch.object(self.app,'_token',return_value='offline-token'),patch.object(self.app,'_request',return_value={}):
            with self.assertRaises(self.app.FordError):self.app.poll()
        self.assertEqual(json.loads(self.app.TELEMETRY_PATH.read_text()),self.capture)
        self.assertFalse(self.app.POLL_LOCK.locked())

    def test_saved_token_alone_does_not_claim_a_working_api(self):
        self.app._write_private(self.app.TOKEN_PATH,{'access_token':'offline-token'})
        self.app._write_private(self.app.TELEMETRY_PATH,self.capture)
        self.assertFalse(self.app._status()['connected'])
        self.app.LAST_POLL=self.app.datetime.now(self.app.timezone.utc).isoformat()
        self.assertTrue(self.app._status()['connected'])
        self.app.LAST_ERROR='HTTP 403'
        self.assertFalse(self.app._status()['connected'])

    def test_snapshot_dedup_and_csv_have_no_identifiers(self):
        s=self.app._summarize(self.capture);s['vin_suffix']='ZZZZ'
        now=self.app.datetime.now(self.app.timezone.utc).isoformat()
        with sqlite3.connect(self.app.HISTORY_DB) as con:
            self.app._save_normalized(con,s,now);self.app._save_normalized(con,s,now)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM normalized_history').fetchone()[0],1)
        text=self.app._history_csv().decode('utf-8-sig')
        self.assertIn(',mi,',text);self.assertIn(',°F,',text);self.assertIn(',PSI,',text)
        self.assertNotIn('ZZZZ',text);self.assertNotIn('offline-token',text);self.assertNotIn('location',text)
        report=self.app._capabilities(self.capture)
        costs=next(x for x in report['features'] if x['feature']=='charging_costs')
        self.assertFalse(costs['usable'])

    def test_trip_efficiency_requires_completed_unit_labeled_data(self):
        raw={'metrics':{'tripDistanceTraveled':self.metric(10,'mi'),'tripEnergyConsumed':self.metric(4,'kWh')}}
        with sqlite3.connect(self.app.HISTORY_DB) as con:
            self.app._store_efficiency(con,raw);self.app._store_efficiency(con,raw)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM efficiency_trips').fetchone()[0],1)
            self.assertEqual(con.execute('SELECT distance_mi/energy_kwh FROM efficiency_trips').fetchone()[0],2.5)
            raw['metrics']['tripEnergyConsumed'].update(value=5,tripProgress='TRIP_IN_PROGRESS')
            self.app._store_efficiency(con,raw)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM efficiency_trips').fetchone()[0],1)

    def test_old_trip_is_not_reassigned_to_today(self):
        raw={'metrics':{'tripDistanceTraveled':{'value':10,'unit':'mi','updateTime':'2020-01-01T12:00:00Z'},'tripEnergyConsumed':{'value':4,'unit':'kWh','updateTime':'2020-01-01T12:00:00Z'}}}
        with sqlite3.connect(self.app.HISTORY_DB) as con:
            self.app._store_efficiency(con,raw)
            self.assertEqual(con.execute('SELECT date_local FROM efficiency_trips').fetchone()[0],'2020-01-01')

    def test_csv_stream_batch_boundary_does_not_drop_tied_timestamps(self):
        now=self.app.datetime.now(self.app.timezone.utc)-self.app.timedelta(minutes=1)
        s=self.app._summarize(self.capture)
        with sqlite3.connect(self.app.HISTORY_DB) as con:
            for i in range(251):
                s['vehicle_updated']=(now-self.app.timedelta(seconds=i)).isoformat()
                self.app._save_normalized(con,s,now.isoformat())
        rows=list(csv.DictReader(io.StringIO(self.app._history_csv().decode('utf-8-sig'))))
        self.assertEqual(len([row for row in rows if row['metric']=='soc']),251)

    def test_history_ranges_and_source_time_dedup(self):
        now=self.app.datetime.now(self.app.timezone.utc)
        s=self.app._summarize(self.capture)
        # Use a recent source timestamp to keep this test independent of run date.
        s['soc']['updated']=(now-self.app.timedelta(hours=2)).isoformat()
        for i in range(2):
            s['vehicle_updated']=(now+self.app.timedelta(seconds=i)).isoformat()
            with sqlite3.connect(self.app.HISTORY_DB) as con:self.app._save_normalized(con,s,now.isoformat())
        for days in (7,30,90,365):
            charts=self.app._seven_day_charts(days)
            self.assertEqual(len(charts['efficiency']),days)
            vehicle_points=[p for p in charts['battery'] if p['time_basis']=='vehicle']
            self.assertEqual(len(vehicle_points),1)
            self.assertEqual(vehicle_points[0]['ts'],s['soc']['updated'])
            if days==365:self.assertTrue(any(p['time_basis']=='legacy observation' for p in charts['battery']))

    def test_http_auth_routes_exports_and_csrf(self):
        from http.server import ThreadingHTTPServer
        self.app._write_private(self.app.TELEMETRY_PATH,self.capture)
        (self.root / 'web').symlink_to(PROJECT / 'web',target_is_directory=True)
        server=ThreadingHTTPServer(('127.0.0.1',0),self.app.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port)
        client=build_opener(HTTPCookieProcessor(CookieJar()))
        try:
            self.assertTrue(client.open(base+'/api/status').geturl().endswith('/login'))
            req=Request(base+'/login',data=b'password=offline-password-only',method='POST')
            html=client.open(req).read().decode();self.assertIn('TIRE PRESSURE',html)
            status=json.load(client.open(base+'/api/status'));self.assertEqual(status['summary']['charging_power']['value'],8.7)
            for days in (7,30,90,365):self.assertEqual(json.load(client.open(base+'/api/charts?days='+str(days)))['days'],days)
            self.assertIn('features',json.load(client.open(base+'/api/capabilities')))
            self.assertIn(b'observed_at_utc',client.open(base+'/api/export.csv').read())
            for path in ('/web/app.js','/web/style.css','/web/mache-gt.png','/web/icon-192.png','/web/icon-512.png','/manifest.webmanifest'):
                self.assertEqual(client.open(base+path).status,200)
            with self.assertRaises(HTTPError) as denied:client.open(Request(base+'/api/poll',data=b'csrf=invalid',method='POST'))
            self.assertEqual(denied.exception.code,403)
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
