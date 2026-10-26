import {useEffect,useState} from 'react';
import {Copy,RefreshCw} from 'lucide-react';
import {API,post,request,teamMode} from '../api';
import './BrowserConnection.css';

type Connection={connection_id:string;state:string;origin:string;project_ids:string[];expires_at?:number};
type Status={state:string;message?:string;chrome_store_url?:string;edge_store_url?:string;store_state:string;protocol_version?:number};
const stateLabels:Record<string,string>={pending_confirmation:'浏览器正在请求连接',connected:'浏览器已连接',revoked:'浏览器连接已撤销',expired:'浏览器授权已到期',confirmation_expired:'浏览器连接确认已过期'};
type Receipt={request_id:string;project_name:string;receipt:{created:number;updated:number;needs_video:number}};
type Pairing={code:string;project_id:string;expires_at:number;base_url:string};
export default function BrowserConnection({base}:{base:string}){return <ProjectConnection key={base} base={base}/>}
function ProjectConnection({base}:{base:string}){
 const [status,setStatus]=useState<Status|null>(null),[connections,setConnections]=useState<Connection[]>([]),[busy,setBusy]=useState(false),[message,setMessage]=useState(''),[error,setError]=useState(''),[pairing,setPairing]=useState<Pairing|null>(null),[receipts,setReceipts]=useState<Receipt[]>([]);
 const projectId=decodeURIComponent(base.split('/projects/')[1]?.split('/')[0]||'');
 async function refresh(){try{const [value,rows]=await Promise.all([request<Status>('/browser-bridge/status'),request<{connections:Connection[]}>('/browser-bridge/connections')]);if(!rows||!Array.isArray(rows.connections))throw new Error('未取得有效的浏览器连接状态，请刷新连接或重新打开桌面应用。');setStatus(value);setConnections(rows.connections);setError('');if(value.state==='ready'){try{const result=await request<{receipts:Receipt[]}>(`${base}/browser-bridge/receipts`);setReceipts(result.receipts||[])}catch{setReceipts([])}}}catch(e){setConnections([]);setError((e as Error).message)}}
 useEffect(()=>{if(teamMode)return;void refresh();const timer=window.setInterval(()=>void refresh(),5000);return()=>window.clearInterval(timer)},[base]);
 async function approve(row:Connection){setBusy(true);setError('');try{await post(`/browser-bridge/connections/${row.connection_id}/approve`,{project_ids:[projectId]});setMessage('已允许此浏览器保存到当前项目。回到扩展，刷新连接后即可连续收藏。');await refresh()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 async function revoke(row:Connection){setBusy(true);try{await post(`/browser-bridge/connections/${row.connection_id}/revoke`,{});setMessage('已撤销此浏览器连接。已保存的参考仍保留。');await refresh()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 async function diagnostics(){try{await navigator.clipboard.writeText(JSON.stringify({state:status?.state,protocol_version:status?.protocol_version,store_state:status?.store_state,connection_states:connections.map(row=>row.state)},null,2));setMessage('诊断信息已复制，不含密钥、配对码或项目内容。')}catch{setError('无法自动复制。请记录上方连接状态后联系支持。')}}
 async function legacyPair(){setBusy(true);try{const value=await post<{code:string;project_id:string;expires_in:number}>(`${base}/discovery/pairing`,{});setPairing({code:value.code,project_id:value.project_id,expires_at:Date.now()+value.expires_in*1000,base_url:new URL(API||location.origin).origin})}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 const active=connections.filter(row=>['pending_confirmation','connected','revoked','expired','confirmation_expired'].includes(row.state));
 return <section className="browser-connection" aria-label="浏览器收藏连接">
  <h3>浏览器收藏</h3><p className="muted">在抖音、快手网页挑选参考，保存到你允许的项目。</p>
  {status?.chrome_store_url&&<a className="browser-store-link" href={status.chrome_store_url} target="_blank" rel="noreferrer">安装 Chrome 收藏工具</a>}
  {status?.edge_store_url&&<a className="browser-store-link" href={status.edge_store_url} target="_blank" rel="noreferrer">安装 Edge 收藏工具</a>}
  {status?.store_state==='awaiting_store_publication'&&<p className="browser-store-pending" role="status">商店版待上架</p>}
  <p className="fine">安装后，在扩展中点击“连接创意工作台”，再在这里允许一次。以后可直接选择项目并保存，不需要填写模型密钥。</p>
  {status?.state==='not_installed'&&<p className="fine">本机收藏工具尚未安装。你仍可在“我的素材”粘贴链接、导入表格或添加本地视频。</p>}
  {status?.message&&<p role="status">{status.message}</p>}
  <div className="browser-connections">{active.map(row=><div key={row.connection_id} className="browser-connection-row">
   <div><strong>{stateLabels[row.state]}</strong><p className="fine">{row.state==='pending_confirmation'?'允许后，此浏览器可向当前项目保存公开参考。':row.state!=='connected'?'回到扩展中重新连接，已保存的参考仍保留。':row.project_ids.includes(projectId)?'已获准保存到当前项目。':'此连接尚未获准保存到当前项目。'}</p><details><summary>查看安装来源</summary><code>{row.origin}</code></details></div>
   {row.state==='pending_confirmation'?<button type="button" className="primary" disabled={busy||!projectId} onClick={()=>void approve(row)}>允许保存到当前项目</button>:row.state==='connected'?<button type="button" className="quiet bordered" disabled={busy} onClick={()=>void revoke(row)}>撤销连接</button>:null}
  </div>)}</div>
  {receipts.length>0&&<div className="browser-receipts" aria-label="最近收藏回执"><h4>最近保存到当前项目</h4>{receipts.slice(0,3).map(receipt=><p className="fine" key={receipt.request_id}>{receipt.project_name}：新增 {receipt.receipt.created} 条 · 已有 {receipt.receipt.updated} 条 · 待补视频 {receipt.receipt.needs_video} 条</p>)}</div>}
  <details><summary>连接遇到问题</summary><p className="fine">请先打开桌面应用，在扩展中点击“连接创意工作台”或“刷新连接”。公司设备限制安装时，请联系设备管理员。</p><div className="inline"><button type="button" className="quiet bordered" disabled={teamMode} onClick={()=>void refresh()}><RefreshCw size={15}/>刷新连接状态</button><button type="button" className="quiet bordered" onClick={()=>void diagnostics()}><Copy size={15}/>复制诊断信息</button></div></details>
  <details><summary>开发与排障：单次导入</summary><p className="fine">仅供旧版扩展兼容，本次导入后即失效，不代表长期连接。</p><button type="button" className="quiet bordered" disabled={busy||teamMode} onClick={()=>void legacyPair()}>生成本次导入连接</button>{pairing&&<><label className="field">本次连接信息<textarea readOnly value={JSON.stringify(pairing)} onFocus={e=>e.currentTarget.select()}/></label><p className="fine">生成后 2 分钟内配对；保存授权仅用于本次导入。</p></>}</details>
  {teamMode&&<p className="fine">浏览器收藏需要本机独立运行的创作台。</p>}
  {message&&<p role="status">{message}</p>}{error&&<p role="alert">{error}</p>}
 </section>;
}
