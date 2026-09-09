// Integration audit; run tools/simulate.py first. Exercises real HTTP/API with
// a DOM double. It verifies behavior, not an actual browser's visual layout.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('src/index.html', 'utf8');
const scripts = [...source.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(match=>match[1]);
class Element {
  constructor(tag='div') { this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.style={};this.listeners={};this.attributes={};this._value='';this._text='';this.disabled=false;this.hidden=false;this.checked=false;this.className='';this.clientWidth=700;this.clientHeight=245; }
  set value(v){this._value=String(v);}
  get value(){return this._value;}
  set textContent(v){this._text=String(v);this.children=[];}
  get textContent(){return this._text+this.children.map(c=>c.textContent||'').join('');}
  append(...items){for(const item of items){item.parent=this;this.children.push(item);}}
  replaceChildren(...items){this._text='';this.children=[];this.append(...items);}
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(c=>c!==this);}
  all(){return [this,...this.children.flatMap(c=>c.all?.()||[])];}
  querySelectorAll(selector){return this.all().filter(e=>e.tagName===selector.toUpperCase());}
  addEventListener(type,fn){(this.listeners[type]||=[]).push(fn);}
  setAttribute(key,value){this.attributes[key]=String(value);}
  getAttribute(key){return this.attributes[key]??null;}
  removeAttribute(key){delete this.attributes[key];}
  focus(){document.activeElement=this;}
  reportValidity(){return true;}
  click(){}
  getContext(){
    if(!this.context){const drawing={paintCount:0,strokes:[],labels:[],clearRect(){this.paintCount++;this.strokes=[];this.labels=[];},stroke(){this.strokes.push(this.strokeStyle);},fillText(){this.labels.push(this.fillStyle);}};this.context=new Proxy(drawing,{get:(target,key)=>key in target?target[key]:(()=>{}),set:(target,key,value)=>(target[key]=value,true)});}
    return this.context;
  }
}
// Preferences must survive reload, respect explicit choices, and remain usable
// when browser privacy settings deny storage. This script runs before the UI.
function themeEnvironment({stored=null,systemDark=false,blocked=false,hasMedia=true}={}){
  const html=new Element('html'),meta=new Element('meta'),store=new Map(stored===null?[]:[['garden-dashboard-theme',stored]]),listeners=[];
  const media={matches:systemDark,addEventListener(type,fn){if(type==='change')listeners.push(fn);}};
  const environment={document:{documentElement:html,querySelector:()=>meta},window:{},localStorage:{getItem:key=>{if(environment.blocked)throw Error('Storage denied');return store.get(key)??null;},setItem:(key,value)=>{if(environment.blocked)throw Error('Storage denied');store.set(key,value);}},blocked,store,
    systemChange(dark){media.matches=dark;listeners.forEach(fn=>fn({matches:dark}));}};
  if(hasMedia)environment.window.matchMedia=()=>media;
  return environment;
}
function initializeTheme(options){const environment=themeEnvironment(options);vm.runInNewContext(scripts[0],environment);return environment;}
for(const [options,expected] of [[{},'light'],[{systemDark:true},'dark'],[{stored:'light',systemDark:true},'light'],[{stored:'dark'},'dark'],[{stored:'invalid',systemDark:true},'dark'],[{blocked:true,systemDark:true},'dark'],[{hasMedia:false},'light']]){
  const env=initializeTheme(options);assert.equal(env.document.documentElement.dataset.theme,expected);assert.equal(env.document.querySelector().attributes.content,expected==='dark'?'#14251e':'#183c31');
}
const themeEnvironmentLive=themeEnvironment({systemDark:true});
const ids = new Map([...source.matchAll(/<([a-z]+)[^>]*\bid="([^"]+)"[^>]*>/g)].map(m=>[m[2],new Element(m[1])]));
const ranges=[0,24,168].map(hours=>{const el=new Element('button');el.dataset.hours=String(hours);return el;});
const document={...themeEnvironmentLive.document,getElementById:id=>ids.get(id),createElement:tag=>new Element(tag),createElementNS:(namespace,tag)=>new Element(tag),querySelectorAll:()=>ranges,addEventListener(){},body:new Element('body'),hidden:false,activeElement:null};
let active=0,maximum=0,requests=[],failNext=false,stallNextRead=false,readStarted=null,abortedReads=0;
const sandbox={document,window:{...themeEnvironmentLive.window,devicePixelRatio:1,addEventListener(){}},localStorage:themeEnvironmentLive.localStorage,getComputedStyle:()=>({getPropertyValue:key=>{const dark=document.documentElement.dataset.theme==='dark';return key==='--chart-line'?(dark?'#3b5143':'#e1e6dc'):(dark?'#a7bbae':'#62736b');}}),navigator:{},console,URL,URLSearchParams,Blob,FormData,AbortController,confirm:()=>true,
 setTimeout:(fn,ms)=>fn.name==='tick'?0:setTimeout(fn,ms).unref(),clearTimeout,
 fetch:async(url,options)=>{active++;maximum=Math.max(maximum,active);requests.push([url,options?.method||'GET']);try{if(failNext){failNext=false;throw new Error('injected link failure');}if(stallNextRead&&(options?.method||'GET')==='GET'){stallNextRead=false;return await new Promise((resolve,reject)=>{options.signal.addEventListener('abort',()=>{abortedReads++;const error=new Error('aborted test read');error.name='AbortError';reject(error);},{once:true});readStarted();});}const r=await fetch(url.startsWith('/')?'http://127.0.0.1:8080'+url:url,options);const body=await r.json();return{ok:r.ok,status:r.status,json:async()=>body};}finally{active--;}}
};
vm.createContext(sandbox);
async function run(code){return vm.runInContext(code,sandbox);}
async function fire(id,type='click'){for(const fn of ids.get(id).listeners[type]||[])await fn({preventDefault(){},currentTarget:ids.get(id)});if(ids.get('toast').className.includes('error'))throw Error(ids.get('toast').textContent);}
async function clickElement(element){assert.ok(element,'Expected a control in its zone panel');for(const fn of element.listeners.click||[])await fn({preventDefault(){},currentTarget:element});if(ids.get('toast').className.includes('error'))throw Error(ids.get('toast').textContent);}
function buttonWithin(element,label){return element.all().find(child=>child.tagName==='BUTTON'&&child.textContent===label);}
async function auditCompactZones(){
  const snapshot=await run('({zones:model.zones,valves:model.valves,status:model.status,settings:model.settings,calibrating:model.calibrating,captureZone:model.captureZone,calibrationData:model.calibrationData})');
  const firstName='<img src=x onerror=alert(1)> / A & B',secondName='Second bed',sharedValve='Shared & bed/?',spareValve='Unassigned valve';
  sandbox.panelFixture={zones:[{name:firstName,channel:0,valves:[sharedValve],threshold:30,wet_target:65,water_duration_sec:17},{name:secondName,channel:1,valves:[sharedValve],threshold:40,wet_target:70,water_duration_sec:23},{name:'Sensor only',channel:2,valves:[],threshold:30,wet_target:60,water_duration_sec:12}],valves:[{name:sharedValve,pin:26},{name:spareValve,pin:27}]};
  await run('globalThis.savedPanelPost=post;globalThis.savedPanelRequest=request;globalThis.savedPanelRefresh=refreshStatus;globalThis.panelActions=[];globalThis.panelCalibrationReads=0;globalThis.panelRefreshes=0;globalThis.panelCalibrationResponse={busy:false,result:null,calibration:{}};post=async(path,body)=>{panelActions.push({path,body});return {ok:true};};request=async(path,...args)=>{if(path==="/api/calibrate"){panelCalibrationReads++;return panelCalibrationResponse;}return savedPanelRequest(path,...args);};refreshStatus=async()=>{panelRefreshes++;renderStatus();return model.status;};model.zones=panelFixture.zones;model.valves=panelFixture.valves;model.settings={...JSON.parse(JSON.stringify(model.settings)),zone_thresholds:{},max_valve_open_sec:600};model.status={wifi_connected:true,time_synced:true,valves:{},moisture:[]};model.calibrating=false;model.captureZone=null;model.calibrationData=null;renderLive();renderStatus();');
  try{
    const first=await run('model.zoneNodes.get(panelFixture.zones[0].name)'),second=await run('model.zoneNodes.get(panelFixture.zones[1].name)'),sensorOnly=await run('model.zoneNodes.get("Sensor only")');
    assert.equal(first.gauge.getAttribute('role'),'img');
    assert.match(first.gauge.getAttribute('aria-label'),/unavailable|no reading/i);
    for(const attribute of ['aria-valuemin','aria-valuemax','aria-valuenow','aria-valuetext'])assert.equal(first.gauge.getAttribute(attribute),null);
    assert.ok(first.gauge.all().includes(first.raw),'Raw ADC belongs inside the gauge SVG');
    assert.match(first.card.textContent,/<img src=x onerror=alert\(1\)> \/ A & B/);
    assert.equal(first.card.all().some(element=>element.tagName==='IMG'),false,'User names must remain literal text');
    assert.equal(first.seconds.value,'17');assert.equal(second.seconds.value,'23');
    const setReading=async(percent,raw)=>{sandbox.panelReading={name:firstName,percent,raw,error:null};await run('model.status.moisture=[panelReading];renderStatus();');};
    // Invalid or absent percentages cannot masquerade as a dry 0% reading.
    for(const invalid of [undefined,null,'',false,'25','bad',NaN,Infinity]){
      await setReading(invalid,0);
      assert.equal(first.value.textContent,'—',String(invalid)+' must be unavailable');
      assert.equal(first.gauge.getAttribute('role'),'img');
      for(const attribute of ['aria-valuemin','aria-valuemax','aria-valuenow','aria-valuetext'])assert.equal(first.gauge.getAttribute(attribute),null,'Unavailable readings must not expose a numeric meter');
      assert.match(first.gauge.getAttribute('aria-label'),/unavailable|no reading/i);
      assert.match(first.gauge.getAttribute('aria-label'),/raw.*\b0\b/i);
      assert.equal(first.raw.textContent.replace(/[^0-9]/g,''),'0','A real raw zero is independent of percentage availability');
    }
    for(const [percent,clamped] of [[-19,0],[0,0],[42.5,42.5],[100,100],[143,100]]){
      await setReading(percent,32767);
      assert.equal(first.gauge.getAttribute('role'),'meter');
      assert.equal(first.gauge.getAttribute('aria-valuemin'),'0');assert.equal(first.gauge.getAttribute('aria-valuemax'),'100');
      assert.equal(first.gauge.getAttribute('aria-valuenow'),String(clamped));
      assert.equal(first.value.textContent,clamped+'%');
      assert.equal(first.raw.textContent.replace(/[^0-9]/g,''),'32767');
    }
    for(const invalid of [null,undefined,'',false,'0',NaN]){
      await setReading(50,invalid);
      assert.equal(first.raw.textContent.includes('0'),false,'Unavailable raw ADC must not be shown as zero');
      assert.match(first.raw.textContent,/—|unavailable/i);
    }
    // Polling changes readings and all copies of a shared valve, preserving an
    // in-progress edit and keyboard focus on that zone's duration field.
    first.seconds.value='47';first.seconds.focus();
    await run('model.status.valves[panelFixture.valves[0].name]={open:true,seconds_open:3,last_close_reason:"manual"};renderStatus();');
    assert.equal((await run('model.zoneNodes.get(panelFixture.zones[0].name)')).seconds,first.seconds);
    assert.equal(first.seconds.value,'47');assert.equal(document.activeElement,first.seconds);
    const sharedViews=await run('model.valveNodes.get(panelFixture.valves[0].name)');
    assert.ok(sharedViews.length>=3,'Both assigned zone panels and the all-valve tools must remain available');
    for(const refs of sharedViews){assert.match(refs.state.textContent,/Watering.*3s/);assert.match(refs.detail.textContent,/GPIO 26/);}
    assert.match(first.card.textContent,/Watering.*3s/);assert.match(second.card.textContent,/Watering.*3s/);
    await run('model.status.valves[panelFixture.valves[0].name].open=false;renderStatus();');
    for(const refs of sharedViews)assert.match(refs.state.textContent,/Closed/);
    assert.match(ids.get('valves-live').textContent,/Unassigned valve/);
    assert.ok((await run('model.valveNodes.get(panelFixture.valves[1].name)')).length>=1);
    assert.equal(buttonWithin(sensorOnly.card,'Water zone').disabled,true,'An unassigned zone must not pretend to queue water');
    await clickElement(buttonWithin(first.card,'Water zone'));
    let action=await run('panelActions.at(-1)'),url=new URL(action.path,'http://controller');
    assert.equal(url.pathname,'/api/zone/trigger');assert.equal(url.searchParams.get('zone'),firstName);assert.equal(url.searchParams.get('duration'),'47');
    await clickElement(buttonWithin(first.card,'Water'));
    action=await run('panelActions.at(-1)');url=new URL(action.path,'http://controller');
    assert.equal(url.pathname,'/api/water/trigger');assert.equal(url.searchParams.get('valve'),sharedValve);assert.equal(url.searchParams.get('duration'),'47');
    for(const [label,state] of [['Open','open'],['Close','close']]){
      await clickElement(buttonWithin(first.card,label));action=await run('panelActions.at(-1)');url=new URL(action.path,'http://controller');
      assert.equal(url.pathname,'/api/valve');assert.equal(url.searchParams.get('valve'),sharedValve);assert.equal(url.searchParams.get('state'),state);
    }
    const beforeInvalid=await run('panelActions.length');first.seconds.reportValidity=()=>false;
    await clickElement(buttonWithin(first.card,'Water zone'));await clickElement(buttonWithin(first.card,'Water'));
    assert.equal(await run('panelActions.length'),beforeInvalid,'Invalid duration fields must prevent a watering request');first.seconds.reportValidity=()=>true;
    // Per-zone calibration captures must not follow the legacy global dropdown.
    ids.get('calibration-zone').value=secondName;
    await run('panelCalibrationResponse={busy:true,result:null,calibration:{}}');
    await clickElement(first.captureDry);action=await run('panelActions.at(-1)');
    assert.equal(action.path,'/api/calibrate');assert.deepEqual({...action.body},{zone:firstName,point:'dry'});
    for(const refs of [first,second,sensorOnly]){assert.equal(refs.captureDry.disabled,true);assert.equal(refs.captureWet.disabled,true);}
    sandbox.panelCaptureResult={busy:false,result:{zone:firstName,point:'dry',raw:16000,samples:40,spread_raw:16},calibration:{[firstName]:{dry_raw:16000,wet_raw:8000},[secondName]:{dry_raw:17000,wet_raw:8500}}};
    await run('panelCalibrationResponse=panelCaptureResult;loadCalibration()');
    assert.match(first.calibration.textContent,/16,?000/);assert.doesNotMatch(second.calibration.textContent,/16,?000/);
    for(const refs of [first,second]){assert.equal(refs.captureDry.disabled,false);assert.equal(refs.captureWet.disabled,false);}
    ids.get('calibration-zone').value=firstName;
    sandbox.panelCaptureResult={busy:false,result:{zone:secondName,point:'wet',raw:7890,samples:42,spread_raw:12},calibration:{[firstName]:{dry_raw:16000,wet_raw:8000},[secondName]:{dry_raw:17000,wet_raw:7890}}};
    await run('panelCalibrationResponse=panelCaptureResult');await clickElement(second.captureWet);action=await run('panelActions.at(-1)');
    assert.deepEqual({...action.body},{zone:secondName,point:'wet'});
    assert.match(second.calibration.textContent,/7,?890/);assert.doesNotMatch(first.calibration.textContent,/7,?890/);
    const beforeRefresh=await run('panelCalibrationReads');await clickElement(buttonWithin(first.card,'Refresh sensor'));
    assert.ok(await run('panelCalibrationReads')>beforeRefresh,'Sensor refresh must retrieve current calibration results');
    // A configuration reload adopts changed defaults only for untouched fields.
    // Explicit manual overrides remain useful when another setting is saved.
    await run('model.zones[0].water_duration_sec=21;model.zones[1].water_duration_sec=31;renderLive();renderStatus();');
    assert.equal((await run('model.zoneNodes.get(panelFixture.zones[0].name)')).seconds.value,'47','A manually edited duration survives configuration reload');
    assert.equal((await run('model.zoneNodes.get(panelFixture.zones[1].name)')).seconds.value,'31','An untouched duration follows its newly saved default');
  }finally{
    sandbox.panelRestore=snapshot;
    await run('post=savedPanelPost;request=savedPanelRequest;refreshStatus=savedPanelRefresh;Object.assign(model,panelRestore);renderLive();renderStatus();');
    document.activeElement=null;
  }
}
(async()=>{
  for(const script of scripts)await run(script);
  assert.match(ids.get('connection').textContent,/online/);
  assert.match(ids.get('environment').textContent,/24\.6/);
  assert.match(ids.get('system-metrics').textContent,/-48/);
  assert.equal(await run('model.zones.length'),2);
  assert.equal(await run('model.history.length'),180);
  const savedZones=await run('JSON.stringify(model.zones)'),savedSchedules=await run('JSON.stringify(model.settings.schedules)');
  try {
    const themeRoot=document.documentElement,toggle=ids.get('theme-toggle'),chart=ids.get('history-chart').getContext(),themeRequests=requests.length;
    assert.equal(themeRoot.dataset.theme,'dark');assert.equal(toggle.attributes['aria-pressed'],'true');
    assert.equal(ids.get('history-legend').children[0].children[0].style.background,'#9bd3a3');assert.ok(chart.strokes.includes('#9bd3a3'));assert.ok(chart.strokes.includes('#3b5143'));assert.ok(chart.labels.every(color=>color==='#a7bbae'));
    const beforeThemePaint=chart.paintCount;
    themeEnvironmentLive.systemChange(false);
    assert.equal(themeRoot.dataset.theme,'light');assert.equal(toggle.attributes['aria-pressed'],'false');assert.ok(chart.paintCount>beforeThemePaint);
    assert.equal(ids.get('history-legend').children[0].children[0].style.background,'#286747');assert.ok(chart.strokes.includes('#286747'));assert.ok(chart.strokes.includes('#e1e6dc'));assert.ok(chart.labels.every(color=>color==='#62736b'));
    await fire('theme-toggle');assert.equal(themeRoot.dataset.theme,'dark');assert.equal(toggle.attributes['aria-pressed'],'true');
    assert.equal(themeEnvironmentLive.store.get('garden-dashboard-theme'),'dark');
    themeEnvironmentLive.systemChange(false);assert.equal(themeRoot.dataset.theme,'dark');
    assert.equal(initializeTheme({stored:themeEnvironmentLive.store.get('garden-dashboard-theme')}).document.documentElement.dataset.theme,'dark');
    await fire('theme-toggle');assert.equal(themeRoot.dataset.theme,'light');assert.equal(toggle.attributes['aria-pressed'],'false');assert.equal(themeEnvironmentLive.store.get('garden-dashboard-theme'),'light');
    themeEnvironmentLive.blocked=true;
    await fire('theme-toggle');assert.equal(themeRoot.dataset.theme,'dark');assert.equal(toggle.attributes['aria-pressed'],'true');
    themeEnvironmentLive.systemChange(false);assert.equal(themeRoot.dataset.theme,'dark');
    themeEnvironmentLive.blocked=false;
    assert.equal(requests.length,themeRequests,'Theme changes must not send controller requests');
    // Update status explains standalone operation, preserves a selected release
    // through polling, and requires an explicit install after manual checks.
    const originalStatus=await run('JSON.stringify(model.status)');
    await run("model.status={wifi_connected:true,time_synced:true,update:{source:'github',repository:'okayaleh/ESP32-watering-rebuild',installed_version:'2.0.0-rebuild.6',available_version:'2.0.0-rebuild.7',available_versions:['2.0.0-rebuild.7','2.0.0-rebuild.6','2.0.0-rebuild.5'],available:true,auto_install:true,check_hour:4,state:'available'}};renderUpdate();");
    assert.match(ids.get('update-source').textContent,/Direct from GitHub/);
    assert.match(ids.get('update-automation').textContent,/04:00/);
    assert.match(ids.get('update-summary').textContent,/2\.0\.0-rebuild\.7.*ready to install/);
    assert.equal(ids.get('update-releases').hidden,false);
    assert.deepEqual(ids.get('update-version').children.map(option=>option.value),['2.0.0-rebuild.7','2.0.0-rebuild.5']);
    ids.get('update-version').value='2.0.0-rebuild.5';await run('renderUpdate()');
    assert.equal(ids.get('update-version').value,'2.0.0-rebuild.5');
    await run("model.status.update.busy=true;model.status.update.state='downloading';renderUpdate();");
    assert.equal(ids.get('update-check').disabled,true);assert.equal(ids.get('update-apply').disabled,true);
    assert.match(ids.get('update-summary').textContent,/Downloading and verifying/);
    await run("model.status.update.busy=false;model.status.update.error='<img src=x onerror=alert(1)>';renderUpdate();");
    assert.match(ids.get('update-summary').textContent,/<img src=x/);assert.equal(ids.get('update-summary').children.length,0);
    await run("model.status.update.error=null;model.status.update.available=false;model.status.update.state='idle';model.status.wifi_connected=false;renderUpdate();");
    assert.match(ids.get('update-summary').textContent,/home Wi-Fi with internet/);assert.equal(ids.get('update-apply').disabled,true);
    await run("model.status.wifi_connected=true;model.status.time_synced=false;renderUpdate();");
    assert.match(ids.get('update-summary').textContent,/clock to synchronize/);
    await run("model.status.time_synced=true;model.status.update.state='checked';renderUpdate();");
    assert.match(ids.get('update-summary').textContent,/matches the selected release/);
    await run("model.status.update.automatic_paused=true;model.status.update.held_version='2.0.0-rebuild.5';renderUpdate();");
    assert.match(ids.get('update-automation').textContent,/paused.*2\.0\.0-rebuild\.5/);
    await run("model.status.update.automatic_paused=false;model.status.update.auto_install=false;renderUpdate();");
    assert.match(ids.get('update-automation').textContent,/Automatic installation is off/);
    await run("globalThis.updateAudit=[];globalThis.savedUpdatePost=post;post=async(path,body)=>{updateAudit.push({path,body});return {ok:true};}");
    try{
      await fire('update-select');
      assert.equal(await run('updateAudit[0].path'),'/api/update/check');
      assert.equal(await run('updateAudit[0].body.version'),'2.0.0-rebuild.5');
      await fire('update-check');
      assert.equal(await run('JSON.stringify(updateAudit[1].body)'),'{}');
      assert.equal(await run('updateAudit.length'),2,'Checks must not trigger an installation');
    }finally{await run('post=savedUpdatePost');sandbox.restoredUpdateStatus=JSON.parse(originalStatus);await run('model.status=restoredUpdateStatus;renderUpdate()');}
    await auditCompactZones();
    // The request queue must survive a rejection and serialize simultaneous jobs.
    failNext=true;
    await run("Promise.allSettled([request('/api/status'),request('/api/settings'),request('/api/events')])");
    assert.equal(maximum,1);
    await run("Promise.all([tick(),tick(),refreshStatus(),loadHistory()])");
    assert.equal(maximum,1);
    // A stop action must cancel a stalled history GET, wait for its cleanup,
    // then write through the same queue without marking the controller offline.
    const startedRead=new Promise(resolve=>{readStarted=resolve;});
    stallNextRead=true;
    const interruptedHistory=run('loadHistory()').then(()=>{throw Error('Stalled read unexpectedly completed');},error=>error);
    await startedRead;
    const beforeStop=requests.length;
    await fire('stop-all');
    const cancelled=await interruptedHistory;
    assert.equal(cancelled.name,'ReadCancelledError');
    assert.equal(cancelled.cancelled,true);
    assert.equal(abortedReads,1);
    assert.deepEqual(requests[beforeStop],['/api/water/stop','POST']);
    assert.equal(maximum,1);
    assert.equal(await run('activeRead'),null);
    assert.match(ids.get('connection').textContent,/online/);
    await run('loadHistory()');
    assert.equal(await run('model.history.length'),180);
    await fire('refresh-pinmap');
    assert.match(ids.get('pinmap').textContent,/GPIO/);
    await fire('scan-i2c');
    await new Promise(r=>setTimeout(r,30));
    if(await run('model.scanning'))await run('pollScan()');
    assert.equal(await run('model.scanning'),false);
    await run("scheduleRow({id:999,hour:7,minute:30,duration_sec:15,enabled:true,zone_names:[model.zones[0].name]})");
    await fire('schedule-form','submit');
    await run("zoneRows[0].card.all().find(e=>e.tagName==='INPUT'&&e.value===model.zones[0].name).value='Audit tomatoes'");
    await fire('zone-form','submit');
    assert.equal(await run('model.settings.schedules.find(s=>s.id===999).zone_names[0]'),'Audit tomatoes');
    await run("zoneRow({name:'Audit bed',channel:2,valves:[model.valves[0].name]})");
    await fire('zone-form','submit');
    assert.equal(await run('model.zones.length'),3);
    const exported=await run("request('/api/config/export')");
    assert.equal(exported.hardware.zone_channels['Audit bed'],2);
    assert.equal(exported.wifi,undefined);
    // Empty calibration fields are omitted on a plain zone edit, protecting a
    // capture made since the editor was loaded.
    assert.equal(await run("'dry_raw' in zoneRows[0].read()"),false);
    // Render malicious names as literal text; no HTML parsing is used.
    await run("model.zones.push({name:'<img src=x onerror=alert(1)>',channel:3,valves:[]});renderLive();renderStatus();");
    assert.match(ids.get('zones-live').textContent,/<img src=x/);
    console.log(JSON.stringify({result:'passed',startupRequests:requests.slice(0,6),maxConcurrentFetches:maximum,checks:['system theme default','stored theme reload','invalid preference fallback','blocked storage fallback','theme toggle accessibility','system theme changes','manual preference overrides system','chart and legend theme redraw','theme changes stay local','standalone update status','retained release selection survives polling','busy update controls','literal update errors','offline and clock guidance','automatic update hold and opt-out','manual version check without install','startup','environment','system','chart','queue failure recovery','overlapping polls','stop action preempts stalled history read','cancelled read releases queue','pinmap','scan handshake','schedule add','zone rename propagation','zone add','export','calibration preservation','safe DOM text','zone gauge accessibility','unavailable percentage never zero','raw zero stays visible','percentage clamping','invalid raw stays unavailable','duration edit and focus survive polling','shared valve status remains consistent','unassigned valve controls retained','sensor-only zone disables watering','zone watering duration and target','per-zone valve start and stop routes','invalid duration prevents requests','per-zone calibration target','busy capture disables every zone','calibration result stays with its zone','per-zone sensor refresh','untouched durations follow changed defaults','manual duration override survives configuration reload']},null,2));
  } catch(error) { console.error('AUDIT FAILURE',error);throw error; } finally {
    sandbox.restoreZones=JSON.parse(savedZones);sandbox.restoreSchedules=JSON.parse(savedSchedules);
    await run("request('/api/zones').then(zones=>post('/api/zones',{zones:restoreZones,renames:zones.some(z=>z.name==='Audit tomatoes')?{'Audit tomatoes':restoreZones[0].name}:{}}))");
    await run("post('/api/schedules',restoreSchedules)");
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
