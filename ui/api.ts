export const API = import.meta.env.DEV ? 'http://127.0.0.1:8765' : '';
export const collectedUrl = new URLSearchParams(location.hash.slice(1)).get('collect') || '';
const runtimeSessionKey='creative-local-runtime-session';
const isLoopback=()=>['127.0.0.1','localhost','[::1]'].includes(location.hostname);
function localRuntimeToken(){try{return isLoopback()?sessionStorage.getItem(runtimeSessionKey)||'':''}catch{return ''}}
function rememberLocalRuntime(value:string){try{if(value&&isLoopback())sessionStorage.setItem(runtimeSessionKey,value);else sessionStorage.removeItem(runtimeSessionKey)}catch{}}
let sessionToken = new URLSearchParams(location.hash.slice(1)).get('token') || localRuntimeToken();
if(sessionToken)rememberLocalRuntime(sessionToken);
if (sessionToken) history.replaceState(null, '', location.pathname + location.search);
export let teamMode=false;
export function setTeamMode(value:boolean){teamMode=value;if(value)rememberLocalRuntime('')}
// Per-tab, origin-scoped session storage only. It carries the temporary local
// runtime credential across reloads, never provider keys or OIDC credentials.
export function setSessionToken(value: string) { sessionToken = value;if(!teamMode)rememberLocalRuntime(value); }
export async function request<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if(teamMode&&options.method&&options.method!=='GET'&&typeof options.body==='string'){
    let body:any;try{body=JSON.parse(options.body)}catch{body=null}
    if(body?.api_key||(path.endsWith('/models')&&body?.provider)){
      if(!window.confirm('此设置使用项目共享凭证，获授权的项目成员可通过任务使用并产生费用。当前团队版不支持个人私有凭证。确认将该服务提供给本项目使用？'))throw new Error('已取消共享凭证设置');
      headers.set('X-Confirm-Shared-Credential','true');
    }
  }
  headers.set('Authorization', 'Bearer ' + sessionToken);
  let response:Response;
  try{response = await fetch(API + '/api' + path, {...options, headers})}
  catch(error){
    if(error instanceof TypeError)throw new Error(path.includes('/assets')?'上传连接中断。已接收的文件会保留，请检查应用是否仍在运行，再重试失败文件。':'与工作空间的连接中断。请检查应用是否仍在运行，再重试。');
    throw error;
  }
  if (!response.ok) {
    if(response.status===401&&teamMode)window.dispatchEvent(new Event('creative-auth-expired'));
    const err = await response.json().catch(() => ({detail: '服务暂时无法响应'}));
    throw new Error(err.user_message || (typeof err.detail === 'string' ? err.detail : err.detail?.map((x:any)=>x.message).join('；') || '输入格式无效'));
  }
  if (options.headers && new Headers(options.headers).get('Accept') === 'application/octet-stream') return response.blob() as Promise<T>;
  return response.json();
}
export function post<T = any>(path: string, data: unknown, method='POST') { return request<T>(path,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}); }
export async function blob(path: string) {
  return request<Blob>(path, {headers:{Accept:'application/octet-stream'}});
}
export async function download(path: string, filename: string) {
  const data = await blob(path); const url=URL.createObjectURL(data); const a=document.createElement('a');a.href=url;a.download=filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
