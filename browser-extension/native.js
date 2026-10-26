export const HOST_NAME='org.creativeagent.collector';
export const OPERATIONS=new Set(['get_status','list_allowed_projects','save_references','get_import_receipt','open_workbench','revoke_connection']);
const uuid=()=>crypto.randomUUID();
export class NativeConnection{
 constructor(browser,id=uuid){this.browser=browser;this.id=id}
 async state(){const stored=await this.browser.storage.local.get('creative_connection');return stored.creative_connection||{}}
 async persist(value){await this.browser.storage.local.set({creative_connection:value});return value}
 async selectProject(project_id){const state=await this.state();return this.persist({...state,project_id})}
 async call(operation,payload={}){
  if(!OPERATIONS.has(operation))throw Error('收藏工具不支持此操作。');
  let state=await this.state();
  if(!state.installation_id)state=await this.persist({...state,installation_id:this.id()});
  const request={...payload,operation,installation_id:state.installation_id,protocol_version:1,connection_id:state.connection_id,project_id:payload.project_id||state.project_id};
  if(payload.request_connection===true){delete request.connection_id}
  if(operation==='save_references'){
   request.request_id=payload.request_id||this.id();
   state=await this.persist({...state,last_request_id:request.request_id,last_project_id:request.project_id});
  }
  if(operation==='get_import_receipt'){
   request.request_id=payload.request_id||state.last_request_id;
   request.project_id=payload.project_id||state.last_project_id;
  }
  let result;
  try{result=await this.browser.runtime.sendNativeMessage(HOST_NAME,request)}
  catch(error){const denied=/forbidden|prohibited|policy|access.*denied/i.test(error.message||'');throw Object.assign(Error(denied?'浏览器或公司设备策略阻止了收藏工具连接，请联系设备管理员。':'未能连接桌面应用。请打开创意工作台；仍无法连接时，在桌面设置中检查安装。'),{code:denied?'policy_restricted':'desktop_offline'})}
  if(!result?.ok)throw Object.assign(Error(result?.message||'桌面应用未返回有效状态，请重试。'),{code:result?.code||'bridge_unavailable'});
  if(result.connection_id)state=await this.persist({...state,connection_id:result.connection_id,connection_state:result.state});
  if(operation==='save_references'||operation==='get_import_receipt')await this.persist({...state,last_receipt:result});
  if(operation==='revoke_connection')await this.persist({...state,connection_state:'revoked'});
  return result;
 }
}
