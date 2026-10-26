import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {mountAdmin} from './console.js';

const until=async check=>{for(let i=0;i<40;i++){if(check())return;await new Promise(r=>setTimeout(r,5))}assert.ok(check())};
function enter(dom,name,value){const input=dom.window.document.querySelector(`[name="${name}"]`);assert.ok(input,name);input.value=value;return input}
function submit(dom,name){const form=dom.window.document.querySelector(`form[data-form="${name}"]`);assert.ok(form,name);form.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}))}
async function click(dom,action){await until(()=>{const button=dom.window.document.querySelector(`[data-action="${action}"]`);return button&&!button.disabled});const btn=dom.window.document.querySelector(`[data-action="${action}"]`);assert.ok(btn,action);btn.click()}

test('administrator submits one-time code without retaining it in the form',async()=>{
 const dom=new JSDOM('<main id="app"></main>',{url:'https://licenses.example/admin'});
 const requests=[];
 mountAdmin(dom.window.document.querySelector('#app'),async(path,opts)=>{
  if(path==='/admin/login')requests.push(JSON.parse(opts.body));
  return {ok:true,json:async()=>path==='/admin/login'?{token:'test-session'}:path==='/admin/config'?{features:[],period_days:[]}:[]};
 });
 enter(dom,'admin_password','operator-password');enter(dom,'admin_otp','287082');
 submit(dom,'login');await until(()=>requests.length===1);
 assert.deepEqual(requests[0],{password:'operator-password',otp:'287082'});
 assert.equal(dom.window.document.querySelector('[name="admin_otp"]')?.value||'','');
 assert.equal(dom.window.localStorage.length,0);
 dom.window.close();
});

test('operator signs in, issues customer, manages lifecycle and logs out without persistent token',async()=>{
 const dom=new JSDOM('<main id="app"></main>',{url:'https://licenses.example/admin'});
 const calls=[];const licenses=[];
 const fetcher=async(path,options={})=>{
  const body=options.body?JSON.parse(options.body):null;calls.push({path,body,headers:options.headers});
  let data={};
  if(path==='/admin/login') data={token:'temporary-session'};
  else {assert.equal(options.headers.Authorization,'Bearer temporary-session');
   if(path==='/admin/config')data={features:['workspace.use','assets.analyze'],period_days:[3,30,180,365]};
   else if(path==='/admin/licenses'&&options.method==='POST') {data={id:'license-one',customer:body.customer,days:body.days,features:body.features,expires_at:null,device_id:null,disabled:0};licenses.push(data)}
   else if(path==='/admin/licenses')data=licenses;
   else if(path.endsWith('/disable'))licenses[0].disabled=1;
   else if(path.endsWith('/enable'))licenses[0].disabled=0;
  }
  return {ok:true,status:200,json:async()=>data};
 };
 mountAdmin(dom.window.document.querySelector('main'),fetcher);
 enter(dom,'admin_password','operator-secret');submit(dom,'login');
 await until(()=>dom.window.document.querySelector('[name="customer"]'));
 enter(dom,'customer','Alice <img src=x onerror=alert(1)>');enter(dom,'customer_password','customer-secret');enter(dom,'days','30');submit(dom,'issue');
 await until(()=>dom.window.document.querySelector('[data-license="license-one"]'));
 assert.equal(dom.window.document.querySelector('img'),null);
 assert.equal(dom.window.document.querySelector('[name="customer_password"]').value,'');
 dom.window.document.querySelector('[data-license="license-one"]').click();
 enter(dom,'renew_days','180');submit(dom,'renew');await until(()=>calls.some(x=>x.path.endsWith('/renew')));
 await click(dom,'disable');await until(()=>dom.window.document.querySelector('[data-action="enable"]'));
 await click(dom,'enable');await until(()=>dom.window.document.querySelector('[data-action="disable"]'));
 enter(dom,'device_id','new-device');submit(dom,'transfer');await until(()=>calls.some(x=>x.path.endsWith('/transfer')));
 await click(dom,'release');await until(()=>calls.some(x=>x.path.endsWith('/release')));
 await click(dom,'logout');await until(()=>dom.window.document.querySelector('[name="admin_password"]'));
 assert.equal(dom.window.localStorage.length,0);assert.equal(dom.window.sessionStorage.length,0);
 assert.equal(calls.find(x=>x.path.endsWith('/renew')).body.days,180);
 assert.equal(calls.find(x=>x.path.endsWith('/transfer')).body.device_id,'new-device');
 assert.deepEqual(calls.find(x=>x.path==='/admin/licenses'&&x.body).body.features,['workspace.use','assets.analyze']);
});

test('failed login clears password and safely displays error without opening account list',async()=>{
 const dom=new JSDOM('<main></main>',{url:'https://licenses.example/admin'});
 mountAdmin(dom.window.document.querySelector('main'),async()=>({ok:false,status:401,json:async()=>({detail:'<svg onload=alert(1)>未验证'})}));
 enter(dom,'admin_password','wrong');submit(dom,'login');
 await until(()=>dom.window.document.querySelector('[role="alert"]')?.textContent.includes('未验证'));
 assert.equal(dom.window.document.querySelector('[name="admin_password"]').value,'');
 assert.equal(dom.window.document.querySelector('svg'),null);
 assert.equal(dom.window.document.querySelector('[name="customer"]'),null);
});

test('existing account features can be edited and audit remains text only',async()=>{
 const dom=new JSDOM('<main></main>',{url:'https://licenses.example/admin'});const calls=[];
 const account={id:'existing',customer:'客户',features:['workspace.use'],days:30,disabled:0};
 const fetcher=async(path,options={})=>{
  const body=options.body?JSON.parse(options.body):null;calls.push({path,body});let data={};
  if(path==='/admin/login')data={token:'session'};
  else if(path==='/admin/config')data={features:['workspace.use','script.generate'],period_days:[3,30,180,365]};
  else if(path==='/admin/licenses')data=[account];
  else if(path.endsWith('/features'))account.features=body.features;
  else if(path.endsWith('/audit'))data=[{action:'set_features',at:1000,detail:'{"features":["<img src=x>"]}'}];
  return {ok:true,status:200,json:async()=>data};
 };
 mountAdmin(dom.window.document.querySelector('main'),fetcher);enter(dom,'admin_password','admin-secret');submit(dom,'login');
 await until(()=>dom.window.document.querySelector('[data-license="existing"]'));
 dom.window.document.querySelector('[data-license="existing"]').click();
 const form=dom.window.document.querySelector('[data-form="features"]');assert.ok(form);
 form.querySelector('[value="script.generate"]').checked=true;submit(dom,'features');
 await until(()=>calls.some(call=>call.path.endsWith('/features')));
 assert.deepEqual(calls.find(call=>call.path.endsWith('/features')).body.features,['workspace.use','script.generate']);
 await click(dom,'audit');await until(()=>dom.window.document.querySelector('[data-audit]'));
 assert.equal(dom.window.document.querySelector('img'),null);
 assert.equal(dom.window.localStorage.length,0);
});
