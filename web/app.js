'use strict';
const E = id => document.getElementById(id);
const valid = item => Boolean(item?.available && item.value !== null && item.value !== undefined);
const number = (value, decimals=1) => Number(value).toLocaleString('en-US',{maximumFractionDigits:decimals});
const zone = 'America/New_York';
let historyDays = 7, updating = false, chartRequest = 0;
const labels = {
  soc:'HV battery charge',range:'Estimated range',odometer:'Odometer',capacity:'Reported battery capacity',health:'Battery health percentage',
  battery_temp:'HV battery temperature',battery_voltage:'HV battery voltage',outdoor_temp:'Outside temperature',charge_status:'Charge status',plug_status:'Plug status',
  charger_voltage:'Charger output voltage',charger_current:'Charger output current',time_to_full:'Time to full (at report)',charger_type:'Charger type',station_status:'Charging station',
  charger_energy:'Reported charger energy',energy_remaining:'Reported energy remaining',battery_performance:'HV battery performance',
  aux_voltage:'12V system voltage',aux_soc:'12V state of charge',aux_status:'12V battery status',alarm:'Alarm',ignition:'Ignition',hood:'Hood',tire_system:'Ford TPMS',
  tires:'Tire pressures',locks:'Door locks',doors:'Doors & hatch',windows:'Windows',target_charge:'Target charge level',charging_costs:'Charging costs',trip_efficiency:'Trip efficiency chart'
};
const statusText = value => ({IN_PROGRESS:'Charging',NORMAL_OPERATION:'Normal operation',AC_BASIC:'AC (basic)',AC:'AC',DC_FAST:'DC fast',STATION_READY:'Station ready',NOT_READY:'Not ready'}[value] || String(value).toLowerCase().replace(/_/g,' ').replace(/^\w/, c=>c.toUpperCase()));
function absoluteTime(value){
  const d=new Date(value);return Number.isFinite(d.getTime())?new Intl.DateTimeFormat('en-US',{timeZone:zone,month:'short',day:'numeric',hour:'numeric',minute:'2-digit',timeZoneName:'short'}).format(d):'Ford timestamp unavailable';
}
function ageInfo(item){
  const stamp=Date.parse(item?.updated);
  if(!Number.isFinite(stamp))return {text:'Ford reading time unavailable',stale:true};
  const seconds=(Date.now()-stamp)/1000;
  if(seconds < -120)return {text:'Ford timestamp is in the future · '+absoluteTime(item.updated),stale:true};
  const s=Math.max(0,seconds),amount=s<60?'Under 1 min':s<3600?Math.floor(s/60)+' min':s<86400?Math.floor(s/3600)+' hr':Math.floor(s/86400)+' days';
  return {text:amount+' old · '+absoluteTime(item.updated),stale:s>3600};
}
function note(node,item){
  const info=ageInfo(item);node.className='reading-age'+(info.stale?' stale':'');node.textContent=info.text;
  node.title=(item?.source_field||'Ford reading')+(item?.assumed_unit?' · Ford unit absent; assumed '+item.assumed_unit:'');
}
function formatted(item,decimals=1){
  if(!valid(item))return '';
  if(item.unit==='min'){
    const min=Math.round(Number(item.value));return (min>=60?Math.floor(min/60)+' hr ':'')+(min%60)+' min';
  }
  if(typeof item.value==='number')return number(item.value,decimals)+(item.unit?' '+item.unit:'');
  return statusText(item.value);
}
function el(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;}
function displayTile(id,item,decimals=0){
  E(id+'-tile').hidden=!valid(item);if(!valid(item))return;
  E(id).textContent=number(item.value,decimals);
  if(E(id+'-unit'))E(id+'-unit').textContent=(item.unit||'')+(item.assumed_unit?' · Ford unit absent; assumed '+item.assumed_unit:' · reported by Ford');
  note(E(id+'-age'),item);
}
function readingRow(label,item,decimals=1){
  const row=el('div','kv'),title=el('span','',label),values=el('div');
  values.append(el('strong','',formatted(item,decimals)));
  const age=el('small');note(age,item);values.append(age);
  if(item.assumed_unit)values.append(el('small','reading-age','Ford unit absent; assumed '+item.assumed_unit));
  row.append(title,values);return row;
}
function renderGroup(panel,grid,keys,s){
  const rows=keys.filter(k=>valid(s[k])).map(k=>readingRow(labels[k],s[k]));
  E(panel).hidden=!rows.length;E(grid).replaceChildren(...rows);
}
function renderTires(s){
  let count=0;
  for(const wheel of ['FRONT_LEFT','FRONT_RIGHT','REAR_LEFT','REAR_RIGHT']){
    const tire=s.tires?.[wheel],card=E('tire-'+wheel);card.hidden=!valid(tire);if(!valid(tire))continue;count++;
    const label=el('label','',wheel.replace('_',' ')),pressure=el('strong','',number(tire.value,1)+' ');pressure.append(el('span','','PSI'));
    const parts=[label,pressure];
    if(valid(tire.status))parts.push(el('span','tpms','Ford TPMS: '+statusText(tire.status.value)));
    if(valid(tire.placard))parts.push(el('span','placard','Cold placard: '+number(tire.placard.value,1)+' PSI'));
    const age=el('small');note(age,tire);parts.push(age);
    card.classList.toggle('warning',Boolean(valid(tire.status)&&/LOW|FAULT|WARNING/.test(tire.status.value)));card.replaceChildren(...parts);
  }
  E('tires-panel').hidden=count===0;
  E('tire-system').textContent=valid(s.tire_system)?'Ford TPMS · '+statusText(s.tire_system.value):count+' of 4 wheel readings supplied';
  E('tire-note').textContent=(count<4?count+' of 4 wheel pressures returned. ':'')+'Cold placard values come from Ford; confirm against your door-jamb label and compare pressures when cold. Unitless tire values use Ford’s native kPa, converted to PSI.';
}
function positionName(item){
  const p=item.position||'',side=item.side||'';
  if(p==='ALL_DOORS')return 'All doors';
  if(p.startsWith('UNSPECIFIED_'))return statusText(side)+' · '+p.slice(12).toLowerCase();
  return statusText(p||side||'Reported position');
}
function renderVehicle(s){
  const allLock=(s.locks||[]).find(x=>valid(x)&&x.position==='ALL_DOORS');
  const main=[];if(allLock)main.push(readingRow('Door locks'+(allLock.determination==='INFERRED'?' (Ford inferred)':''),allLock));
  for(const key of ['alarm','ignition','hood'])if(valid(s[key]))main.push(readingRow(labels[key],s[key]));
  const details=[];
  for(const [key,label] of [['doors','Door'],['windows','Window'],['locks','Lock']]){
    for(const item of s[key]||[]){
      if(!valid(item)||item.position==='ALL_DOORS')continue;
      details.push(readingRow(label+' · '+positionName(item)+(item.determination==='INFERRED'?' (Ford inferred)':''),item));
    }
  }
  E('vehicle-summary').replaceChildren(...main);E('vehicle-details').replaceChildren(...details);
  E('vehicle-panel').hidden=!(main.length||details.length);
  E('vehicle-details').parentElement.hidden=!details.length;
}
function renderSupport(c){
  const features=c?.features||[];E('support-count').textContent=features.filter(x=>x.usable).length+' available groups';
  const rows=features.map(f=>{
    const row=el('div','support-row'),title=el('div');title.append(el('strong','',labels[f.feature]||f.feature));
    if(f.field)title.append(el('code','',f.field));
    let message=f.usable?'Available'+(f.count?' · '+f.count+' readings':''):f.reason||'Not in the latest response';
    if(f.usable&&f.assumed_unit)message+=' · unit absent; native '+f.assumed_unit+' assumed';
    if(f.updated)message+=' · '+absoluteTime(f.updated);
    row.append(title,el('span','',message));return row;
  });
  E('support-report').replaceChildren(...rows);
}
function renderStatus(data){
  const s=data.summary||{},has=data.has_data;
  E('version').textContent='v'+data.version;E('no-data').hidden=has;
  E('hero').hidden=![s.soc,s.range,s.odometer,s.capacity,s.health].some(valid);
  E('soc-tile').hidden=!valid(s.soc);
  if(valid(s.soc)){E('soc').textContent=number(s.soc.value,0);E('soc-bar').style.width=Math.min(100,Math.max(0,s.soc.value))+'%';note(E('soc-age'),s.soc);}
  E('charge').textContent=valid(s.charge_status)?statusText(s.charge_status.value):'';
  E('vin').textContent=s.vin_suffix?'VIN ···· '+s.vin_suffix:'';
  displayTile('range',s.range);displayTile('odo',s.odometer);displayTile('capacity',s.capacity,1);displayTile('health',s.health,1);
  // Unit conversion happens once on the server, shared by cards, exports and history.
  renderTires(s);renderVehicle(s);
  renderGroup('environment-panel','environment-grid',['battery_temp','battery_voltage','outdoor_temp','plug_status','charge_status','battery_performance','energy_remaining'],s);
  const charging=Object.assign({},s);
  labels.charging_power='Calculated charger output';
  if(!valid(s.charge_status)||!['IN_PROGRESS','CHARGING'].includes(s.charge_status.value)){charging.time_to_full=null;charging.charging_power=null;}
  renderGroup('charging-panel','charging-grid',['charging_power','charger_type','time_to_full','charger_voltage','charger_current','station_status','charger_energy'],charging);
  renderGroup('aux-panel','aux-grid',['aux_voltage','aux_soc','aux_status'],s);
  E('updated').textContent=data.last_poll?absoluteTime(data.last_poll):'No successful check recorded';
  E('vehicle-updated').textContent=s.vehicle_updated?'Ford response timestamp · '+absoluteTime(s.vehicle_updated):'Individual reading times appear on each card';
  E('connection').textContent=data.connected?'FORD RESPONDING':has?'SAVED FORD DATA':data.authorized?'FORD AUTHORIZED':'NOT CONNECTED';
  E('connection').className='online'+(data.connected?' connected':has?' cached':'');
  let diagnosis=data.last_error;
  if(!diagnosis){
    diagnosis=data.connected?'Ford’s API responded successfully. Individual readings may be older than this check.':data.authorized?'Ford authorization is saved. Fetch telemetry to check the connection.':has?'Showing saved Ford readings. Authorize Ford to resume updates.':'Authorize Ford below, then fetch telemetry.';
  }
  E('diag').textContent=diagnosis;E('diag').className='diag'+(data.last_error?' error':'');
  E('authorization').open=!data.authorized;
  const alerts=(data.alerts||[]).map(a=>{
    const n=el('div','alert '+a.level,a.message);
    if(a.updated)n.append(el('small','','Reading: '+absoluteTime(a.updated)));return n;
  });
  if(has&&!data.connected)alerts.unshift(el('div','alert warning','Showing saved Ford data. A successful refresh is needed to confirm the current API connection.'));
  E('alerts').replaceChildren(...alerts);E('alerts').hidden=!alerts.length;
  renderSupport(data.capabilities);
}
function axisDay(day,count){return count<=7?weekday(day):new Intl.DateTimeFormat('en-US',{month:'short',day:'numeric',timeZone:'UTC'}).format(new Date(day+'T12:00:00Z'));}
const INK='#8a9aab', GRID='#2a3642', MINT='#70dbc1', DARK='#151c25';
let lastCharts = {battery:[],efficiency:[]};
const localDay = new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'});
function dateKey(date){
  const parts=localDay.formatToParts(date);const part=t=>parts.find(p=>p.type===t)?.value||'';
  return `${part('year')}-${part('month')}-${part('day')}`;
}
function weekday(day){
  return new Intl.DateTimeFormat('en-US',{weekday:'short',timeZone:'UTC'}).format(new Date(day+'T12:00:00Z'));
}
function prepareCanvas(id){
  const el=E(id),w=Math.max(100,el.clientWidth||500),h=234,dpr=Math.max(1,Math.min(3,window.devicePixelRatio||1));
  el.width=Math.round(w*dpr);el.height=Math.round(h*dpr);const ctx=el.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.clearRect(0,0,w,h);ctx.textBaseline='middle';ctx.font='11px system-ui,-apple-system,Segoe UI,sans-serif';
  return {ctx,w,h};
}
function drawNoData(ctx,message,x,y){ctx.fillStyle=INK;ctx.textAlign='center';ctx.font='12px system-ui,-apple-system,Segoe UI,sans-serif';ctx.fillText(message,x,y);ctx.textAlign='left';}
function roundedBar(ctx,x,y,w,h,r){
  r=Math.min(r,w/2,h);ctx.beginPath();ctx.moveTo(x,y+h);ctx.lineTo(x,y+r);ctx.quadraticCurveTo(x,y,x+r,y);
  ctx.lineTo(x+w-r,y);ctx.quadraticCurveTo(x+w,y,x+w,y+r);ctx.lineTo(x+w,y+h);ctx.closePath();
}
function drawBattery(data){
  const {ctx:c,w,h}=prepareCanvas('battery-chart');const left=38,right=w-12,top=16,bottom=h-28;
  c.lineWidth=1;c.strokeStyle=GRID;c.fillStyle=INK;c.textAlign='left';
  for(let pct=0;pct<=100;pct+=25){const y=bottom-(pct/100)*(bottom-top);c.beginPath();c.moveTo(left,y);c.lineTo(right,y);c.stroke();c.fillText(String(pct),4,y);}
  const days=(lastCharts.efficiency||[]).map(x=>x.day);
  if(days.length){days.forEach((day,i)=>{if(i%Math.ceil(days.length/7)!==0 && i!==days.length-1)return;const x=left+(i+.5)*(right-left)/days.length;c.textAlign='center';c.fillText(axisDay(day,days.length),x,h-10);});}
  const usable=(data||[]).filter(p=>p && Number.isFinite(Number(p.soc)) && p.soc!==null && Number.isFinite(Date.parse(p.ts)));
  if(!usable.length){drawNoData(c,'No recorded battery readings yet',w/2,(top+bottom)/2);return;}
  // Fixed seven-calendar-day plot: observations are positioned within their local day.
  const dayIndex=new Map(days.map((day,i)=>[day,i]));
  const coords=[];
  usable.forEach(p=>{const dt=new Date(p.ts),di=dayIndex.get(dateKey(dt));if(di===undefined)return;
    const hh=new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(dt);
    const part=t=>Number(hh.find(k=>k.type===t)?.value||0);
    const fraction=(part('hour')*3600+part('minute')*60+part('second'))/86400;
    coords.push({x:left+(di+fraction)*(right-left)/Math.max(1,days.length),y:bottom-Math.min(100,Math.max(0,Number(p.soc)))/100*(bottom-top)});
  });
  if(!coords.length){drawNoData(c,'No readings in the last 7 days',w/2,(top+bottom)/2);return;}
  coords.sort((a,b)=>a.x-b.x);
  if(coords.length>1){const area=c.createLinearGradient(0,top,0,bottom);area.addColorStop(0,'rgba(112,219,193,.23)');area.addColorStop(1,'rgba(112,219,193,.005)');
    c.beginPath();c.moveTo(coords[0].x,bottom);coords.forEach(p=>c.lineTo(p.x,p.y));c.lineTo(coords[coords.length-1].x,bottom);c.closePath();c.fillStyle=area;c.fill();}
  c.strokeStyle=MINT;c.lineWidth=2.4;c.lineJoin='round';c.lineCap='round';c.beginPath();coords.forEach((p,i)=>i?c.lineTo(p.x,p.y):c.moveTo(p.x,p.y));c.stroke();
  const end=coords[coords.length-1];c.fillStyle=MINT;c.beginPath();c.arc(end.x,end.y,3.8,0,Math.PI*2);c.fill();
}
function drawEfficiency(data){
  const {ctx:c,w,h}=prepareCanvas('efficiency-chart');const left=35,right=w-12,top=18,bottom=h-28;
  const values=(data||[]).map(x=>x?.mi_per_kwh == null?null:Number(x.mi_per_kwh));
  const found=values.filter(v=>v!==null&&Number.isFinite(v)&&v>0);
  const yMax=Math.max(4,Math.ceil((Math.max(0,...found)*1.12)*2)/2);
  c.lineWidth=1;c.strokeStyle=GRID;c.fillStyle=INK;
  for(let i=0;i<=4;i++){
    const v=yMax*i/4,y=bottom-i*(bottom-top)/4;c.beginPath();c.moveTo(left,y);c.lineTo(right,y);c.stroke();
    c.textAlign='left';c.fillText(Number.isInteger(v)?String(v):v.toFixed(1),4,y);
  }
  const count=Math.max(1,data.length),cell=(right-left)/count,barW=Math.min(37,cell*.62);
  (data||[]).forEach((rec,i)=>{
    const x=left+cell*(i+.5),v=values[i];c.textAlign='center';c.fillStyle=INK;if(i%Math.ceil(count/7)===0 || i===count-1)c.fillText(axisDay(rec.day,count),x,h-10);
    if(v===null||!Number.isFinite(v)||v<=0)return;
    const barH=Math.min(bottom-top,v/yMax*(bottom-top)),y=bottom-barH;
    const grd=c.createLinearGradient(x,y,x,bottom);grd.addColorStop(0,'#91ead3');grd.addColorStop(1,'#358f87');c.fillStyle=grd;
    roundedBar(c,x-barW/2,y,Math.max(1,barW),barH,5);c.fill();if(count>30)return;c.fillStyle='#d6f8ec';c.font='600 11px system-ui,-apple-system,Segoe UI,sans-serif';c.fillText(v.toFixed(2),x,Math.max(9,y-9));c.font='11px system-ui,-apple-system,Segoe UI,sans-serif';
  });
  if(!found.length)drawNoData(c,'No supported Ford trip efficiency readings yet',w/2,(top+bottom)/2);
}
function renderCharts(charts){
  lastCharts=charts||{battery:[],efficiency:[],days:historyDays};
  const daily=(lastCharts.efficiency||[]).filter(x=>x.mi_per_kwh!=null);
  E('efficiency-panel').hidden=!daily.length;E('export-efficiency').hidden=!daily.length;
  E('history-label').textContent='LAST '+(lastCharts.days||historyDays)+' DAYS';
  drawBattery(lastCharts.battery||[]);if(daily.length)drawEfficiency(lastCharts.efficiency||[]);
  const battery=(lastCharts.battery||[]).filter(x=>x.soc!=null).length;
  E('battery-note').textContent=battery?number(battery,0)+' saved readings · Ford reading times where supplied; older history uses dashboard observation times.':'No readings saved in this period yet. Existing history is preserved; this fills as Ford readings are recorded.';
  E('efficiency-note').textContent=daily.length+' days with supported Ford trip data · mi/kWh. Missing days are left empty.';
}
async function jsonResponse(url,options={}){
  const res=await fetch(url,Object.assign({cache:'no-store'},options));
  if(res.redirected&&new URL(res.url,location.origin).pathname==='/login'){location.assign('/login');throw new Error('Session expired');}
  if(!res.ok)throw new Error('HTTP '+res.status);
  if(!(res.headers.get('content-type')||'').includes('application/json'))throw new Error('Unexpected response; check that all update files were installed');
  return res.json();
}
async function loadCharts(){
  const request=++chartRequest,days=historyDays;
  try{const charts=await jsonResponse('/api/charts?days='+days);if(request===chartRequest)renderCharts(charts);}
  catch(err){if(request===chartRequest)E('battery-note').textContent='History could not be loaded. '+err.message;}
}
async function update(){
  if(updating)return;updating=true;
  try{renderStatus(await jsonResponse('/api/status'));await loadCharts();}
  catch(err){
    E('connection').textContent='DASHBOARD OFFLINE';E('connection').className='online';
    E('diag').textContent='Could not reach the dashboard server. Any readings shown are saved data. '+err.message;E('diag').className='diag error';
  }finally{updating=false;}
}
E('history-days').addEventListener('change',()=>{
  historyDays=Number(E('history-days').value);
  E('export-history').href='/api/export.csv?days='+historyDays;E('export-efficiency').href='/api/efficiency.csv?days='+historyDays;loadCharts();
});
E('poll-form').addEventListener('submit',async event=>{
  event.preventDefault();const button=E('poll-button');button.disabled=true;button.textContent='Checking Ford…';
  try{
    renderStatus(await jsonResponse('/api/poll',{method:'POST',body:new URLSearchParams(new FormData(event.target))}));await loadCharts();
  }catch(err){E('diag').textContent='Ford refresh did not complete. '+err.message;E('diag').className='diag error';}
  finally{button.disabled=false;button.textContent='Fetch latest telemetry';}
});
window.addEventListener('resize',()=>{drawBattery(lastCharts.battery||[]);if(!E('efficiency-panel').hidden)drawEfficiency(lastCharts.efficiency||[]);});
renderCharts({battery:[],efficiency:[],days:7});update();setInterval(update,30000);
