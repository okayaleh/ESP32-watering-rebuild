// Integration audit; run tools/simulate.py first. Exercises real HTTP/API with
// a DOM double. It verifies behavior, not an actual browser's visual layout.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('src/index.html', 'utf8');
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
  setAttribute(key,value){this.attributes[key]=value;}
  reportValidity(){return true;}
  click(){}
  getContext(){return new Proxy({}, {get:(target,key)=>target[key]||(()=>{}),set:(target,key,value)=>(target[key]=value,true)});}
}
const ids = new Map([...source.matchAll(/<([a-z]+)[^>]*\bid="([^"]+)"[^>]*>/g)].map(m=>[m[2],new Element(m[1])]));
const ranges=[0,24,168].map(hours=>{const el=new Element('button');el.dataset.hours=String(hours);return el;});
const document={getElementById:id=>ids.get(id),createElement:tag=>new Element(tag),querySelectorAll:()=>ranges,addEventListener(){},body:new Element('body'),hidden:false};
let active=0,maximum=0,requests=[],failNext=false,stallNextRead=false,readStarted=null,abortedReads=0;
const sandbox={document,window:{devicePixelRatio:1,addEventListener(){}},navigator:{},console,URL,URLSearchParams,Blob,FormData,AbortController,confirm:()=>true,
 setTimeout:(fn,ms)=>fn.name==='tick'?0:setTimeout(fn,ms).unref(),clearTimeout,
 fetch:async(url,options)=>{active++;maximum=Math.max(maximum,active);requests.push([url,options?.method||'GET']);try{if(failNext){failNext=false;throw new Error('injected link failure');}if(stallNextRead&&(options?.method||'GET')==='GET'){stallNextRead=false;return await new Promise((resolve,reject)=>{options.signal.addEventListener('abort',()=>{abortedReads++;const error=new Error('aborted test read');error.name='AbortError';reject(error);},{once:true});readStarted();});}const r=await fetch(url.startsWith('/')?'http://127.0.0.1:8080'+url:url,options);const body=await r.json();return{ok:r.ok,status:r.status,json:async()=>body};}finally{active--;}}
};
vm.createContext(sandbox);
async function run(code){return vm.runInContext(code,sandbox);}
async function fire(id,type='click'){for(const fn of ids.get(id).listeners[type]||[])await fn({preventDefault(){},currentTarget:ids.get(id)});if(ids.get('toast').className.includes('error'))throw Error(ids.get('toast').textContent);}
(async()=>{
  await run(source.match(/<script>([\s\S]*?)<\/script>/)[1]);
  assert.match(ids.get('connection').textContent,/online/);
  assert.match(ids.get('environment').textContent,/24\.6/);
  assert.match(ids.get('system-metrics').textContent,/-48/);
  assert.equal(await run('model.zones.length'),2);
  assert.equal(await run('model.history.length'),180);
  const savedZones=await run('JSON.stringify(model.zones)'),savedSchedules=await run('JSON.stringify(model.settings.schedules)');
  try {
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
    console.log(JSON.stringify({result:'passed',startupRequests:requests.slice(0,6),maxConcurrentFetches:maximum,checks:['startup','environment','system','chart','queue failure recovery','overlapping polls','stop action preempts stalled history read','cancelled read releases queue','pinmap','scan handshake','schedule add','zone rename propagation','zone add','export','calibration preservation','safe DOM text']},null,2));
  } catch(error) { console.error('AUDIT FAILURE',error);throw error; } finally {
    sandbox.restoreZones=JSON.parse(savedZones);sandbox.restoreSchedules=JSON.parse(savedSchedules);
    await run("request('/api/zones').then(zones=>post('/api/zones',{zones:restoreZones,renames:zones.some(z=>z.name==='Audit tomatoes')?{'Audit tomatoes':restoreZones[0].name}:{}}))");
    await run("post('/api/schedules',restoreSchedules)");
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
