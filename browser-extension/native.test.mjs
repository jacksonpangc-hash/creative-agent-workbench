import {test} from 'node:test';
import assert from 'node:assert/strict';
import {NativeConnection} from './native.js';

test('popup restart restores project and receipt request without storing credentials',async()=>{
 const persisted={};
 const chrome={storage:{local:{get:async()=>({...persisted}),set:async value=>Object.assign(persisted,value)}},runtime:{sendNativeMessage:async(host,request)=>({ok:true,state:'connected',connection_id:'connection-1',projects:[{id:'p',name:'项目'}],request_id:request.request_id})}};
 const first=new NativeConnection(chrome,()=> 'installation-0001');
 await first.call('get_status');await first.selectProject('p');
 await first.call('save_references',{captures:[{url:'https://www.douyin.com/video/1'}]});
 const second=new NativeConnection(chrome);
 const state=await second.state();
 assert.equal(state.project_id,'p');assert.ok(state.last_request_id);
 assert.equal(JSON.stringify(persisted).includes('token'),false);
 assert.equal(JSON.stringify(persisted).includes('captures'),false);
});
