import {useEffect,useRef,useState,type ReactNode} from 'react';
import {post,request,blob} from '../api';
import PatternLibrary from './PatternLibrary';
import ReferenceImport from './ReferenceImport';
import BrowserConnection from '../settings/BrowserConnection';
import './PublicReferences.css';
type Row=Record<string,any>;
type Props={base:string;onSelect?:(assetId:string)=>void;selectedAssetIds?:string[];connectedData?:ReactNode;initialQuery?:string;initialImport?:boolean;onUsePattern?:(text:string)=>void};
const platformName=(p:string)=>p==='douyin'?'抖音':p==='kuaishou'?'快手':p||'平台未提供';
const metricName:Record<string,string>={likes:'点赞',comments:'评论',shares:'分享',favorites:'收藏',views:'播放',followers:'粉丝'};
const sources=[['saved','我的收藏'],['platform','平台网页'],['patterns','创意结构库'],['connected','已连接的数据服务']];
const fetching=(ref:Row)=>['queued','running','resolving','downloading','processing'].includes(ref.media_fetch?.state)||['queued','resolving','downloading','processing'].includes(ref.media_fetch?.stage);
const stageName:Record<string,string>={queued:'等待获取',running:'正在获取视频',resolving:'正在读取视频地址',downloading:'正在获取视频',processing:'正在整理视频',available:'视频已取得',succeeded:'视频已取得',failed:'视频未取得',cancelled:'已停止获取'};
function ReferenceCover({base,asset,title}:{base:string;asset?:Row;title:string}){
 const [url,setUrl]=useState('');
 useEffect(()=>{setUrl('');if(asset?.inspection!=='ready')return;let active=true,objectUrl='';void blob(`${base}/assets/${encodeURIComponent(asset.id)}/file?poster=true`).then(data=>{if(!active)return;objectUrl=URL.createObjectURL(data);setUrl(objectUrl)}).catch(()=>{});return()=>{active=false;if(objectUrl)URL.revokeObjectURL(objectUrl)}},[base,asset?.id,asset?.inspection]);
 return url?<img className="reference-cover" src={url} alt={`${title}的本地封面`} onError={()=>setUrl('')}/>:<div className="reference-cover" aria-label="封面未提供">封面<br/>未提供</div>;
}
export default function PublicReferences({base,onSelect,selectedAssetIds,connectedData,initialQuery='',initialImport=false,onUsePattern}:Props){
 const scope=useRef(base);scope.current=base;
 const [plans,setPlans]=useState<Row[]>([]),[references,setReferences]=useState<Row[]>([]),[assets,setAssets]=useState<Row[]>([]),[jobs,setJobs]=useState<Row[]>([]),[parses,setParses]=useState<Row[]>([]);
 const [query,setQuery]=useState(initialQuery),[savedQuery,setSavedQuery]=useState(initialQuery),[error,setError]=useState(''),[busy,setBusy]=useState(false),[message,setMessage]=useState('');
 const [selected,setSelected]=useState<Record<string,string>>({}),[chosen,setChosen]=useState<string[]>([]),[rights,setRights]=useState<Record<string,boolean>>({});
 const [localMatches,setLocalMatches]=useState<Row|null>(null);
 const [source,setSource]=useState('saved'),[importOpen,setImportOpen]=useState(initialImport);
 const requestKeys=useRef<Record<string,string>>({});
 const endpoint=`${base}/discovery`;
 async function load(){const at=base;const [ps,refs,state]=await Promise.all([request<Row[]>(`${endpoint}/plans`),request<Row[]>(`${endpoint}/references`),request<Row>(`${base}/state`)]);if(scope.current!==at)return;setPlans(ps);setReferences(refs);setAssets(state.assets||[]);setJobs(state.jobs||[]);setParses(state.parses||[])}
 useEffect(()=>{setLocalMatches(null);setPlans([]);setReferences([]);setSelected({});setError('');setMessage('');setChosen([]);setBusy(false);setRights({});setImportOpen(false);setSource('saved');requestKeys.current={};void load().catch(e=>{if(scope.current===base)setError(e.message)})},[base]);
 useEffect(()=>{setQuery(initialQuery);setSavedQuery(initialQuery)},[base,initialQuery]);
 useEffect(()=>{setImportOpen(initialImport)},[base,initialImport]);
 const pending=references.filter(fetching).map(ref=>ref.id).sort().join(',');
 useEffect(()=>{
  if(!pending)return;let active=true;const at=base;let requesting=false;
  const timer=window.setInterval(()=>{if(requesting)return;requesting=true;void Promise.all(pending.split(',').map(async id=>({requestedId:id,reference:(await request<Row>(`${endpoint}/references/${encodeURIComponent(id)}/media`)).reference}))).then(results=>{if(!active||scope.current!==at)return;setReferences(old=>old.map(ref=>results.find(value=>value.reference?.id===ref.id)?.reference||ref));if(results.some(value=>value.reference?.can_visually_analyze||value.reference?.id&&value.reference.id!==value.requestedId))void load().catch(e=>{if(active&&scope.current===at)setError(e.message)})}).catch(e=>{if(active&&scope.current===at)setError('暂时无法更新视频进度，请刷新收藏。'+e.message)}).finally(()=>{requesting=false})},1500);
  return()=>{active=false;window.clearInterval(timer)};
 },[base,pending]);
 async function act(fn:()=>Promise<void>){const at=base;setBusy(true);setError('');setMessage('');try{await fn()}catch(e){if(scope.current===at)setError(e instanceof Error?e.message:'操作未完成，请重试。')}finally{if(scope.current===at)setBusy(false)}}
 async function attach(rid:string,assetId:string){const at=base;const row=await post<Row>(`${endpoint}/references/${encodeURIComponent(rid)}/attach`,{asset_id:assetId});if(scope.current===at){setReferences(old=>old.map(r=>r.id===rid?row:r));setMessage('已关联视频，收藏继续使用原记录。')}}
 async function fetchMedia(ref:Row){const at=base;if(!rights[ref.id])return;if(['failed','cancelled'].includes(ref.media_fetch?.state)||ref.media_fetch?.stage==='failed')delete requestKeys.current[ref.id];const key=requestKeys.current[ref.id]||(requestKeys.current[ref.id]=crypto.randomUUID());const job=await post<Row>(`${endpoint}/references/${encodeURIComponent(ref.id)}/media`,{rights_confirmed:true,idempotency_key:key});if(scope.current!==at)return;setReferences(old=>old.map(r=>r.id===ref.id?{...r,media_job_id:job.id,media_fetch:{state:job.state,stage:job.state==='queued'?'queued':job.state,...(job.result?.failure?{failure:job.result.failure}:{})}}:r));setMessage('已提交视频获取请求。取得视频后才能分析画面。');const result=await request<Row>(`${endpoint}/references/${encodeURIComponent(ref.id)}/media`);if(scope.current!==at)return;if(result.reference?.id&&result.reference.id!==ref.id)await load();else if(result.reference)setReferences(old=>old.map(value=>value.id===ref.id?result.reference:value));if(['failed','cancelled'].includes(job.state))delete requestKeys.current[ref.id]}
 const matching=references.filter(ref=>!savedQuery.trim()||[ref.title,ref.author,ref.url,platformName(ref.platform)].some(value=>String(value||'').toLocaleLowerCase().includes(savedQuery.trim().toLocaleLowerCase())));
 return <section className="public-references" aria-label="免费公开发现">
  <h3>找参考</h3>
  <div className="reference-source-tabs" role="tablist" aria-label="参考来源">{sources.map(([id,label])=><button type="button" key={id} id={'reference-source-'+id} role="tab" aria-selected={source===id} aria-controls={'reference-panel-'+id} tabIndex={source===id?0:-1} onKeyDown={e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const ids=sources.map(([value])=>value);const index=e.key==='Home'?0:e.key==='End'?ids.length-1:(ids.indexOf(id)+(e.key==='ArrowRight'?1:-1)+ids.length)%ids.length;setSource(ids[index]);(e.currentTarget.parentElement?.querySelectorAll('button')[index] as HTMLButtonElement)?.focus()}} onClick={()=>setSource(id)}>{label}</button>)}</div>
  <section hidden={source!=='platform'} role="tabpanel" id="reference-panel-platform" aria-labelledby="reference-source-platform">
   <p className="fine">搜索范围：抖音、快手平台网页。前往平台挑选，收藏后回到这里。互动指标不代表销量。</p>
   <form onSubmit={e=>{e.preventDefault();void act(async()=>{const at=base;await post(`${endpoint}/plans`,{query,platforms:['douyin','kuaishou']});if(scope.current===at)await load()})}}><label className="field">搜索关键词<input value={query} maxLength={200} onChange={e=>setQuery(e.target.value)} placeholder="商品、场景或同行账号"/></label><button disabled={busy||!query.trim()}>生成免费搜索计划</button></form>
   {plans.slice().reverse().slice(0,5).map(plan=><div className="script-row" key={plan.id}><strong>{plan.query}</strong><div className="actions">{(plan.links||[]).map((item:Row)=><a key={item.platform} href={item.url} target="_blank" rel="noopener noreferrer">去{platformName(item.platform)}搜索</a>)}</div></div>)}
   <BrowserConnection key={base} base={base}/>
  </section>
  {source==='patterns'&&<section role="tabpanel" id="reference-panel-patterns" aria-labelledby="reference-source-patterns"><PatternLibrary base={base} onUse={onUsePattern}/></section>}
  {source==='connected'&&<section role="tabpanel" id="reference-panel-connected" aria-labelledby="reference-source-connected"><p className="fine">使用你自行连接的数据服务，查询费用单独记录。未连接也可使用收藏和平台网页。</p>{connectedData||<p className="fine">可在设置的“参考来源”中连接数据服务。</p>}</section>}
  {error&&<p role="alert" className="error">{error}</p>}{message&&<p role="status">{message}</p>}
  <section hidden={source!=='saved'} role="tabpanel" id="reference-panel-saved" aria-labelledby="reference-source-saved">
   <div className="inline between"><p className="fine">当前项目的收藏 · {references.length} 条</p><div className="actions"><button type="button" disabled={busy} aria-expanded={importOpen} aria-controls="add-reference-panel" onClick={()=>setImportOpen(value=>!value)}>添加参考</button><button type="button" disabled={busy} onClick={()=>void act(load)}>刷新收藏</button></div></div>
   <label className="field">搜索收藏<input aria-label="搜索收藏" value={savedQuery} onChange={e=>setSavedQuery(e.target.value)} placeholder="搜索当前项目的标题、作者或链接"/></label>
   <button className="quiet bordered" type="button" disabled={busy||!savedQuery.trim()} onClick={()=>void act(async()=>{const at=base;const found=await post<Row>(base+'/knowledge/search',{query:savedQuery});if(scope.current===at)setLocalMatches(found)})}>按内容查找本地参考</button>
   {localMatches&&<section aria-label="本地内容匹配"><p role="status">{localMatches.local_retrieval?.message} · 找到 {localMatches.reference_patterns?.length||0} 段参考内容</p>{!(localMatches.reference_patterns||[]).length&&<p className="fine">暂未找到适合的内容。只有链接的收藏还需要添加视频或可靠台词。</p>}{(localMatches.reference_patterns||[]).map((r:Row)=><article key={r.id}><b>{r.title} · {r.type}</b><p>{r.statement}</p><p className="fine">{r.suitable_reason}</p>{r.asset_id&&onSelect&&<button type="button" onClick={()=>onSelect(r.asset_id)}>选入本次分析</button>}</article>)}</section>}
   <p className="fine">搜索范围：当前项目已收藏的参考。</p>
   <section id="add-reference-panel" hidden={!importOpen}><ReferenceImport key={base} base={base} onImported={async()=>{await load();setSource('saved');setSavedQuery('')}}/></section>
   {!references.length&&<div className="empty"><p>还没有参考视频，先收藏一条或添加链接</p><button type="button" onClick={()=>setImportOpen(true)}>添加链接</button></div>}
   {!!references.length&&!matching.length&&<p className="fine">没有匹配的收藏，可以调整关键词，或去平台网页挑选。</p>}
   {matching.map(ref=>{const latest=ref.observations?.at(-1);const asset=assets.find(a=>a.id===ref.asset_id);const duration=ref.duration_seconds??(asset?.metadata?.duration_ms?asset.metadata.duration_ms/1000:null);const isChosen=selectedAssetIds?selectedAssetIds.includes(ref.asset_id):chosen.includes(ref.id);const progress=ref.media_fetch;const mediaBusy=fetching(ref);const failure=progress?.failure;const relatedJob=jobs.filter(job=>['analysis','parse'].includes(job.kind)&&[...(job.payload?.asset_ids||[]),...(job.payload?.scope?.asset_ids||[])].includes(ref.asset_id)).at(-1);const hasAnalysis=parses.some(parse=>parse.rows?.some((row:Row)=>row.asset_id===ref.asset_id))||relatedJob?.state==='succeeded';return <article className="script-row" key={ref.id}>
    <div className="reference-card-head"><ReferenceCover base={base} asset={ref.can_visually_analyze?asset:undefined} title={ref.title||'参考视频'}/><div><h4>{ref.title||'标题未提供'}</h4><p className="fine">{platformName(ref.platform)} · {ref.author||'作者未提供'}</p><p className="fine">时长：{duration!=null?`${duration} 秒`:'未提供'} · 发布时间：{ref.published_at||'未提供'}</p></div></div>
    <p className="fine">来源：{latest?.source==='user_import'?'用户导入':latest?.source==='user_manual_link'?'手动链接':'主动页面收藏'} · 采集于 {latest?.captured_at||'未提供'}{ref.identity_state==='pending'?' · 短链接身份待确认':''}</p><a href={ref.url} target="_blank" rel="noopener noreferrer">查看公开页面</a>
    <p className="fine">{latest?.metrics?.length?latest.metrics.map((m:Row)=>`${metricName[m.name]||m.name} ${m.raw}（${m.basis==='user_import'?'用户导入，':''}观察于 ${m.as_of}）`).join('；'):'互动指标未提供'}；未获得商业成交数据。</p>
    <div className="reference-states" aria-label="参考处理状态"><span>链接：已保存</span><span>视频：{ref.can_visually_analyze?(progress?.state==='succeeded'||progress?.stage==='available'?'已取得':'已关联本地文件'):progress?stageName[progress.stage]||stageName[progress.state]||'尚未取得':'尚未取得'}</span><span>分析：{hasAnalysis?'已有结果':relatedJob&&['queued','running'].includes(relatedJob.state)?'处理中':'尚未开始'}</span></div>
    <p className="fine">用途：参考，未确认可用于成片。</p>
    {!ref.can_visually_analyze&&<>
     <div className="reference-fetch"><label className="check-label"><input type="checkbox" aria-label={`我有权获取并分析这条视频：${ref.title||'参考视频'}`} checked={!!rights[ref.id]} disabled={busy||mediaBusy} onChange={e=>setRights({...rights,[ref.id]:e.target.checked})}/>我有权获取并分析这条视频</label><button type="button" disabled={busy||mediaBusy||!rights[ref.id]} onClick={()=>void act(()=>fetchMedia(ref))}>{mediaBusy?'正在获取视频…':'获取视频'}</button></div>
     {mediaBusy&&<p className="fine" role="status">{stageName[progress?.stage]||'正在获取视频'}{Number(progress?.downloaded_bytes)>0?` · 已接收 ${(progress.downloaded_bytes/1024/1024).toFixed(1)} MB`:''}</p>}
     {failure&&<p className="reference-media-failure" role="status">{failure.message||'暂时无法读取这条视频'}，可以导入视频文件。</p>}
     <details className="reference-file-options"><summary>补充已有视频文件</summary><label className="field">关联已有文件：{ref.title||'公开参考'}<select value={selected[ref.id]||''} onChange={e=>setSelected({...selected,[ref.id]:e.target.value})}><option value="">选择当前项目的文件</option>{assets.map(a=><option value={a.id} key={a.id}>{a.title||a.id}</option>)}</select></label><button disabled={busy||mediaBusy||!selected[ref.id]} onClick={()=>void act(()=>attach(ref.id,selected[ref.id]))}>关联文件</button><label className="field">导入有权使用的文件<input type="file" accept="video/*,image/*" disabled={busy||mediaBusy} onChange={e=>{const file=e.target.files?.[0];e.target.value='';if(!file)return;void act(async()=>{const at=base;const data=new FormData();data.append('file',file);const added=await request<Row>(`${base}/assets?source_kind=inspiration`,{method:'POST',body:data});if(scope.current!==at)return;await attach(ref.id,added.id);if(scope.current===at)await load()})}}/></label></details>
    </>}
    <button type="button" disabled={busy||!ref.can_visually_analyze||!ref.asset_id||!onSelect} aria-pressed={isChosen} onClick={()=>{setMessage('');onSelect?.(ref.asset_id);setChosen(old=>old.includes(ref.id)?old:[...old,ref.id])}}>加入本次创作</button>{isChosen&&<p role="status">已加入本次创作：{ref.title||'参考视频'}</p>}
   </article>})}
  </section>
 </section>;
}
