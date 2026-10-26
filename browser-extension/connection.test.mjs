import {test} from 'node:test';
import assert from 'node:assert/strict';
import {parseConnection,localBase,workbenchMatch,receiptText,focusWorkbench} from './connection.js';

test('dynamic local connection accepts the app provided port and excludes remote or credential URLs',()=>{
 const value=parseConnection(JSON.stringify({base_url:'http://127.0.0.1:58335',code:'ABCDEFGH23',project_id:'p',expires_at:Date.now()+120000}));
 assert.equal(value.base_url,'http://127.0.0.1:58335');
 assert.equal(value.project_id,'p');
 for(const bad of ['https://remote.test','http://127.0.0.1:5/path','http://user:secret@localhost:5','http://localhost:5/?token=secret'])assert.throws(()=>localBase(bad));
 assert.throws(()=>parseConnection(JSON.stringify({...value,token:'secret'})));
});
test('workbench lookup is limited to the supplied local origin without credentials',()=>{
 const url=workbenchMatch('http://localhost:58335');
 assert.equal(url,'http://localhost:58335/*');assert.equal(url.includes('token'),false);assert.equal(url.includes('secret'),false);
});
test('receipts report actual added updated skipped and failed counts and never invent missing counts',()=>{
 assert.equal(receiptText({receipt:{created:2,updated:1,skipped:0,failed:1}}),'新增 2 条 · 更新 1 条 · 跳过 0 条 · 未完成 1 条');
 assert.equal(receiptText({references:[{id:'a'}]}),'已保存 1 条参考；新增与更新数量未提供。');
});
test('view only activates an already open tab at the exact origin and never opens a new root page',async()=>{
 const actions=[];
 const browser={tabs:{query:async query=>{actions.push(query);return [{id:2,url:'http://localhost:58335/',windowId:3}]},update:async(...args)=>actions.push(args)},windows:{update:async(...args)=>actions.push(args)}};
 assert.equal(await focusWorkbench(browser,'http://localhost:58335'),true);
 assert.deepEqual(actions,[{url:'http://localhost:58335/*'},[2,{active:true}],[3,{focused:true}]]);
 browser.tabs.query=async()=>[{id:4,url:'http://localhost:9999/',windowId:3}];
 assert.equal(await focusWorkbench(browser,'http://localhost:58335'),false);
});
