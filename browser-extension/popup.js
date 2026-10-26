import {captureVisiblePage} from './extract.js';
import {receiptText} from './connection.js';
const byId=id=>document.getElementById(id);
const status=byId('status'),container=byId('items'),importButton=byId('import');
let captures=[],connected=false,saving=false,pendingRequest=null;
function tell(text,error=false){status.textContent=text;status.dataset.error=String(error)}
async function bridge(operation,payload={}){const result=await chrome.runtime.sendMessage({kind:'creative-bridge',operation,payload});if(!result?.ok)throw Object.assign(Error(result?.message||'收藏连接暂时不可用，请重新连接。'),{code:result?.code});return result}
async function state(){return chrome.runtime.sendMessage({kind:'creative-bridge',action:'state'})}
function selectedCaptures(){return Array.from(container.querySelectorAll('input:checked')).map(el=>captures[Number(el.dataset.index)])}
function update(){importButton.disabled=saving||!connected||!byId('project').value||!selectedCaptures().length}
function showReceipt(result){tell(`${receiptText(result)}\n保存到：${result.project_name||'所选项目'}${Number.isInteger(result.receipt?.needs_video)?` · ${result.receipt.needs_video} 条待补视频`:''}\n收藏链接不会自动获取视频。`);byId('view').hidden=false;byId('receipt').hidden=true;pendingRequest=null}
async function refresh(reconnect=false){
 try{
  const result=await bridge('get_status',reconnect?{request_connection:true}:{});
  connected=result.state==='connected';
  const labels={connected:'已连接创意工作台',pending_confirmation:'请在桌面“设置 → 参考来源”允许此浏览器',revoked:'连接已撤销，请重新连接',expired:'授权已到期，请重新连接',confirmation_expired:'桌面确认已过期，请重新连接'};
  byId('connection-status').textContent=labels[result.state]||'需要重新连接';
  byId('connect').textContent=connected?'刷新连接':'连接创意工作台';
  byId('disconnect').hidden=!connected;
  if(connected){
   const projects=await bridge('list_allowed_projects'),stored=await state();
   const select=byId('project');select.replaceChildren();
   for(const project of projects.projects){const option=document.createElement('option');option.value=project.id;option.textContent=project.name;select.append(option)}
   if(projects.projects.some(p=>p.id===stored.project_id))select.value=stored.project_id;
   else if(projects.projects.length!==1){const option=document.createElement('option');option.value='';option.textContent='请选择保存到的项目';select.prepend(option);select.value=''}
   if(select.value)await chrome.runtime.sendMessage({kind:'creative-bridge',action:'project',project_id:select.value});
   if(stored.last_request_id){byId('receipt').hidden=false;try{showReceipt(await bridge('get_import_receipt'))}catch(error){if(error.code!=='receipt_missing')tell(error.message,true)}}
  }
 }catch(error){connected=false;byId('connection-status').textContent=error.code==='policy_restricted'?'受设备策略限制':'桌面应用尚未连接';tell(error.message,true)}
 finally{update()}
}
byId('connect').addEventListener('click',async()=>{byId('connect').disabled=true;try{await refresh(true)}finally{byId('connect').disabled=false}});
byId('disconnect').addEventListener('click',async()=>{try{await bridge('revoke_connection');connected=false;await refresh()}catch(error){tell(error.message,true)}});
byId('project').addEventListener('change',async()=>{pendingRequest=null;await chrome.runtime.sendMessage({kind:'creative-bridge',action:'project',project_id:byId('project').value});update()});
byId('receipt').addEventListener('click',async()=>{try{showReceipt(await bridge('get_import_receipt'))}catch(error){tell(error.message,true)}});
byId('capture').addEventListener('click',async()=>{
 byId('capture').disabled=true;
 try{
  const tabs=await chrome.tabs.query({active:true,currentWindow:true});if(!tabs[0]?.id)throw Error('请先打开抖音或快手公开页面。');
  const results=await chrome.scripting.executeScript({target:{tabId:tabs[0].id},func:captureVisiblePage});
  const result=results[0]?.result;if(!result)throw Error('当前页面无法读取，请刷新平台网页后重试。');
  captures=[result.page,...result.links].slice(0,50);pendingRequest=null;container.replaceChildren();
  for(const [index,item] of captures.entries()){
   const label=document.createElement('label'),check=document.createElement('input'),text=document.createElement('span');
   check.type='checkbox';check.dataset.index=String(index);check.checked=index===0;check.addEventListener('change',()=>{pendingRequest=null;update()});
   const profile=/\/(?:user|profile)\//.test(new URL(item.url).pathname);
   text.textContent=(profile?'作者主页 · ':'')+(item.title||item.url);label.append(check,text);container.append(label);
  }
  tell('勾选要保存的参考。缺失指标保留为空，互动量不代表销量。');update();
 }catch(error){tell(error.message,true)}finally{byId('capture').disabled=false}
});
importButton.addEventListener('click',async()=>{
 update();if(importButton.disabled)return;
 saving=true;update();pendingRequest=pendingRequest||crypto.randomUUID();
 try{showReceipt(await bridge('save_references',{project_id:byId('project').value,request_id:pendingRequest,captures:selectedCaptures()}))}
 catch(error){tell(`${error.message}\n可查询保存回执；未收到回执时，保留选择并重试同一请求。`,true);byId('receipt').hidden=false}
 finally{saving=false;update()}
});
async function openDesktop(){try{const result=await bridge('open_workbench',{project_id:byId('project').value});if(result.message)tell(result.message)}catch(error){tell(error.message,true)}}
byId('view').addEventListener('click',()=>void openDesktop());
byId('open-desktop').addEventListener('click',()=>void openDesktop());
void refresh();
