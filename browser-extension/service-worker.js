import {NativeConnection,OPERATIONS} from './native.js';
const connection=new NativeConnection(chrome);
// MV3 can terminate after any event. Only non-secret state is kept in storage.
let queue=Promise.resolve();
chrome.runtime.onMessage.addListener((message,sender,respond)=>{
 if(sender.id!==chrome.runtime.id||!sender.url?.startsWith(chrome.runtime.getURL(''))||message?.kind!=='creative-bridge')return false;
 if(message.action==='state'){connection.state().then(value=>respond({ok:true,...value}));return true}
 if(message.action==='project'){
  queue=queue.then(()=>connection.selectProject(message.project_id));
 }else if(OPERATIONS.has(message.operation)){
  queue=queue.then(()=>connection.call(message.operation,message.payload||{}));
 }else{respond({ok:false,message:'收藏工具不支持此操作。'});return false}
 queue.then(value=>respond({ok:true,...value}),error=>respond({ok:false,message:error.message,code:error.code}));
 queue=queue.catch(()=>{});return true;
});
