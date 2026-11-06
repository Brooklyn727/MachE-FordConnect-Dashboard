import importlib.util,json,os,tempfile,threading,sqlite3,sys,shutil
from pathlib import Path
from http.server import ThreadingHTTPServer
from playwright.sync_api import sync_playwright, expect

project=Path(__file__).resolve().parents[1]
fixture=json.loads((project/'tests/fixtures/captured-metrics.json').read_text())
artifacts=Path(os.environ.get('MACHE_QA_OUTPUT',str(project/'work/qa')))
artifacts.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='mache-browser-') as tmp:
 root=Path(tmp)
 (root/'config.json').write_text(json.dumps({'client_id':'offline-id','client_secret':'offline-secret','dashboard_password':'offline-browser-password','redirect_uri':'http://localhost:8765/callback'}))
 (root/'web').symlink_to(project/'web',target_is_directory=True)
 os.environ['MACHE_ROOT']=str(root)
 spec=importlib.util.spec_from_file_location('preview',project/'server.py');app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)
 app._write_private(app.TELEMETRY_PATH,fixture)
 app._write_private(app.TOKEN_PATH,{'access_token':'offline-test-token'})
 with sqlite3.connect(app.HISTORY_DB) as con:app._save_normalized(con,app._summarize(fixture),app.datetime.now(app.timezone.utc).isoformat())
 # Network is never sent to Ford. The browser exercises the real local routes.
 app._token=lambda:'offline-test-token'
 app._request=lambda method,url,**kwargs: {} if url.endswith('/garage') else fixture
 server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 base='http://127.0.0.1:'+str(server.server_port)
 try:
  with sync_playwright() as p:
   browser=p.chromium.launch(**({'executable_path':shutil.which('chromium')} if shutil.which('chromium') else {}),headless=True,args=['--no-sandbox'])
   context=browser.new_context(viewport={'width':1440,'height':1100},device_scale_factor=1)
   page=context.new_page();errors=[];page.on('pageerror',lambda err:errors.append(str(err)))
   page.goto(base);page.locator('input[name=password]').fill('offline-browser-password');page.get_by_role('button',name='Unlock dashboard').click()
   expect(page.locator('#range')).to_have_text('128')
   assert page.locator('#odo').inner_text()=='17,157'
   assert page.locator('#health-tile').is_hidden()
   assert page.locator('#efficiency-panel').is_hidden()
   assert page.locator('.tire-card:visible').count()==4
   assert page.locator('#charging-grid').inner_text().find('8.7 kW')>=0
   assert page.locator('#charging-grid').inner_text().find('3 hr 24 min')>=0
   assert '70.7 °F' in page.locator('#environment-grid').inner_text()
   assert 'Oct 15' in page.locator('#environment-grid').inner_text()
   assert '363 V' in page.locator('#environment-grid').inner_text()
   assert '14.6 V' in page.locator('#aux-grid').inner_text()
   assert page.locator('#connection').inner_text()=='SAVED FORD DATA'
   assert not errors,errors
   page.screenshot(path=str(artifacts/'desktop-preview.png'),full_page=True)
   for width in (390,375):
    page.set_viewport_size({'width':width,'height':844});page.wait_for_timeout(100)
    dimensions=page.evaluate('({scroll:document.documentElement.scrollWidth,width:window.innerWidth})')
    assert dimensions['scroll']<=dimensions['width'],dimensions
    assert page.locator('.tire-card:visible').count()==4
   page.screenshot(path=str(artifacts/'mobile-preview.png'),full_page=True)
   page.locator('#history-days').select_option('365');expect(page.locator('#history-label')).to_have_text('LAST 365 DAYS')
   assert 'days=365' in page.locator('#export-history').get_attribute('href')
   page.locator('#poll-button').click();expect(page.locator('#connection')).to_have_text('FORD RESPONDING')
   assert page.locator('.tire-card:visible').count()==4
   # Missing fields disappear instead of remaining blank/zero or retaining old values.
   app._write_private(app.TELEMETRY_PATH,{'metrics':{'xevBatteryStateOfCharge':{'value':0,'updateTime':'2026-11-06T05:00:50Z'}}})
   page.reload();expect(page.locator('#soc')).to_have_text('0')
   assert page.locator('#tires-panel').is_hidden()
   assert page.locator('#charging-panel').is_hidden()
   assert page.locator('#aux-panel').is_hidden()
   assert page.locator('#vehicle-panel').is_hidden()
   assert page.locator('#range-tile').is_hidden()
   # Empty API responses produce a useful state, not blank metric cards.
   app._write_private(app.TELEMETRY_PATH,{})
   page.reload();expect(page.locator('#no-data')).to_be_visible()
   assert page.locator('#hero').is_hidden()
   # A lost dashboard connection never leaves a green connection badge.
   page.route('**/api/status',lambda route:route.abort())
   page.evaluate('() => update()');expect(page.locator('#connection')).to_have_text('DASHBOARD OFFLINE')
   assert not errors,errors
   print('PASS: real local login/API routes, captured Ford values, hidden unsupported fields, 365-day history, manual polling, desktop/mobile (390/375px), zero SOC, sparse/empty/offline states; no browser errors')
   browser.close()
 finally:
  server.shutdown();server.server_close();thread.join()
