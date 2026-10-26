import {Fragment,useEffect,useRef,useState} from 'react';
import {request,post,download} from '../api';
import './ScriptsWorkspace.css';
import {DraftPersistence} from '../draftPersistence';
import FootagePicker from './FootagePicker';
import CreativeBasis from '../strategy/CreativeBasis';
type Item=Record<string,any>;
type DraftCache={active:string,drafts:Record<string,Item>};
type Props={base:string,initialScriptId?:string,assets:Item[],onUseScript:(s:Item)=>void,onOpenReference?:(ref:Item,hasUnsavedChanges:boolean)=>void};
const row=()=>({start_ms:0,end_ms:3000,dialogue:'',tone:'',visual:'',notes:'',references:[]});
const blank=()=>({title:'新脚本',objective:'',rows:[row()],confirmed:false,timing_estimated:false,production_notes:[]});
const content=(s:Item)=>Object.fromEntries(Object.entries(s).filter(([k])=>!['id','project_id','version','created_at','updated_at','revisions','source'].includes(k)));
const draftKey=(draft:Item)=>draft.id||'__new';
const storageKey=(base:string)=>'creative-script-drafts-053:'+encodeURIComponent(base);
function readCache(base:string):DraftCache{
 try{const stored=JSON.parse(sessionStorage.getItem(storageKey(base))||'null');if(stored&&typeof stored.active==='string'&&stored.drafts&&typeof stored.drafts==='object')return {active:stored.active,drafts:Object.fromEntries(Object.entries(stored.drafts).filter(([,value])=>value&&Array.isArray((value as Item).rows))) as Record<string,Item>};}catch{}
 return {active:'',drafts:{}};
}
export default function ScriptsWorkspace(props:Props){return <ScriptWorkspaceEditor key={props.base} {...props}/>}
function ScriptWorkspaceEditor({base,initialScriptId,assets,onUseScript,onOpenReference}:Props){
 const cache=useRef(readCache(base));
 const [scripts,setScripts]=useState<Item[]>([]),[tasks,setTasks]=useState<Item[]>([]),[draft,setDraft]=useState<Item|null>(()=>cache.current.drafts[cache.current.active]||null);
 const [pickRow,setPickRow]=useState<number|null>(null);
 const [message,setMessage]=useState(''),[busy,setBusy]=useState(false),[dirty,setDirty]=useState(()=>!!cache.current.drafts[cache.current.active]);
 const [draftsReady,setDraftsReady]=useState(false),[draftWarning,setDraftWarning]=useState('');
 const [recoveries,setRecoveries]=useState<Item[]>([]);
 const repository=useRef(new DraftPersistence((path,body)=>post(path,body,'PUT'))),draftIdentity=useRef('script-editor'),mounted=useRef(true);
 const draftReadAttempt=useRef(0);
 const projectId=base.split('/').at(-1)!;
 const persist=()=>{try{sessionStorage.setItem(storageKey(base),JSON.stringify(cache.current))}catch{}
  if(draftsReady)void repository.current.save(projectId,draftIdentity.current,{scriptDrafts:structuredClone(cache.current)}).then(saved=>{
   if(!mounted.current)return;setDraftWarning('');if(saved&&saved.identity!==draftIdentity.current){draftIdentity.current=saved.identity;setMessage('另一窗口的修改已保留，这份编辑草稿已另存。')}
  }).catch(()=>{if(mounted.current)setDraftWarning('脚本修改暂时无法存到本机，请保留此窗口并重试。')});
 };
 const remember=()=>{if(draft&&dirty){cache.current.drafts[draftKey(draft)]=structuredClone(draft);cache.current.active=draftKey(draft);persist()}};
 useEffect(()=>{if(draft&&dirty){cache.current.drafts[draftKey(draft)]=structuredClone(draft);cache.current.active=draftKey(draft);persist()}},[draft,dirty,base,draftsReady]);
 const load=async()=>{const [s,t]=await Promise.all([request(base+'/scripts'),request(base+'/production')]);setScripts(s);setTasks(t)};
 const restoreDrafts=async()=>{const attempt=++draftReadAttempt.current;setDraftWarning('');
 try{const [s,t,result]=await Promise.all([request(base+'/scripts'),request(base+'/production'),request(base+'/drafts')]);
  if(!mounted.current||attempt!==draftReadAttempt.current)return;
  const rows=Array.isArray(result.drafts)?result.drafts:[];await repository.current.hydrate(projectId,rows);
  if(!mounted.current||attempt!==draftReadAttempt.current)return;setScripts(s);setTasks(t);const saved=rows.filter((r:Item)=>r.content?.scriptDrafts);
  if(saved.length){const current=saved[0];draftIdentity.current=current.identity;cache.current=structuredClone(current.content.scriptDrafts);setRecoveries(saved.slice(1));setDraft(cache.current.drafts[cache.current.active]||null);setDirty(!!cache.current.drafts[cache.current.active])}
  setDraftsReady(true);setMessage('');setDraftWarning('');
 }catch{if(mounted.current&&attempt===draftReadAttempt.current)setDraftWarning('脚本草稿暂时无法读取，已有内容已保留。请重试。')}
 };
 useEffect(()=>{mounted.current=true;void restoreDrafts();return()=>{mounted.current=false;draftReadAttempt.current++}},[base]);
 useEffect(()=>{if(!initialScriptId||!draftsReady)return;const saved=scripts.find(s=>s.id===initialScriptId);if(saved){const local=cache.current.drafts[initialScriptId];setDraft(structuredClone(local||saved));setDirty(!!local)}},[initialScriptId,draftsReady]);
 const run=async(fn:()=>Promise<void>)=>{if(busy)return;setBusy(true);setMessage('');try{await fn()}catch(e){setMessage((e as Error).message)}finally{setBusy(false)}};
 const edit=(patch:Item)=>{setDraft(v=>({...v,...patch}));setDirty(true)};
 const open=(s:Item)=>{remember();const local=cache.current.drafts[draftKey(s)];setDraft(structuredClone(local||s));setDirty(!!local);setMessage('')};
 const create=()=>{remember();setDraft(structuredClone(cache.current.drafts.__new||blank()));setDirty(true);setMessage('')};
 const save=(confirmed=false)=>run(async()=>{
  if(!draft)return;
  const next=await post<Item>(base+'/scripts'+(draft.id?'/'+draft.id:''),{...content(draft),production_notes:(draft.production_notes||[]).map((note:string)=>note.trim()).filter(Boolean),confirmed,...(draft.id?{expected_version:draft.version}:{})},draft.id?'PUT':'POST');
  delete cache.current.drafts[draftKey(draft)];delete cache.current.drafts[draftKey(next)];cache.current.active='';persist();
  setDraft(next);setDirty(false);await load();setMessage(next.unresolved_items?.length?'脚本已保存，还有几处需要确认。':'已保存，可以稍后继续修改。');
 });
 const editRow=(index:number,patch:Item)=>edit({rows:draft!.rows.map((r:Item,i:number)=>i===index?{...r,...patch}:r)});
 const ownAssets=assets.filter(a=>['own','raw','finished','account','pushed'].includes(a.source_kind)&&a.inspection==='ready'&&a.metadata?.reuse_allowed!==false&&a.metadata?.rights?.derivative_create!==false&&(!draft?.sku_id||a.metadata?.sku_id===draft.sku_id||a.metadata?.generic_footage)&&(!draft?.source||(draft.source.selection_snapshot?.own_assets||[]).some((v:Item)=>v.id===a.id)));
 const ownRef=(a:Item)=>({asset_id:a.id,start_ms:a.media_type==='image'?null:0,end_ms:a.media_type==='image'?null:a.metadata.duration_ms,observation:'',provenance:'metadata',usage_mode:'normal',use_duration_ms:null,usage_note:''});
 const footage=draft?.rows.flatMap((r:Item,i:number)=>(r.own_references||[]).map((ref:Item)=>({row:i+1,...ref})))||[];
 const reshoots=draft?.rows.flatMap((r:Item,i:number)=>r.reshoot?.trim()?[{row:i+1,text:r.reshoot}]:[])||[];
 const needsClaimReview=(claim:Item)=>!claim.reviewed||!claim.fact_ids?.length||claim.fact_ids.some((id:string)=>!draft?.product_facts_snapshot?.claims?.some((f:Item)=>f.fact_id===id&&f.confirmed));
 const unreviewedClaims=draft?.rows.flatMap((r:Item,i:number)=>(r.factual_claims||[]).filter(needsClaimReview).map((c:Item)=>({row:i+1,text:c.statement})))||[];
 const referenceLink=(ref:Item,label:string,own=false)=>{
  const asset=assets.find(a=>a.id===ref.asset_id);
  const source=draft?.source,scope=source?.scope;
  const sourceIds=own?(source?.selection_snapshot?.own_assets||[]).map((a:Item)=>a.id):(scope?.asset_ids||[]);
  const ranges=(scope?.selected_segments||[]).filter((r:Item)=>r.asset_id===ref.asset_id);
  const validTime=typeof ref.start_ms==='number'&&Number.isFinite(ref.start_ms)&&typeof ref.end_ms==='number'&&Number.isFinite(ref.end_ms)&&ref.start_ms>=0&&ref.end_ms>ref.start_ms&&(!Number.isFinite(asset?.metadata?.duration_ms)||ref.end_ms<=asset!.metadata.duration_ms);
  const wholeImage=asset?.media_type==='image'&&ref.start_ms==null&&ref.end_ms==null;
  const reason=!asset?'原素材已移除或不在当前项目，暂时无法回看。':asset.inspection!=='ready'?'素材尚未就绪，可在我的素材查看进度。':source&&!sourceIds.includes(ref.asset_id)?'这条素材不在当前脚本的来源中。':!wholeImage&&(asset.media_type!=='video'||!validTime)?'这段时间无法定位，请核对原片起止位置。':!own&&ranges.length&&!ranges.some((r:Item)=>ref.start_ms!=null&&ref.end_ms!=null&&r.start_ms<=ref.start_ms&&r.end_ms>=ref.end_ms)?'这段时间超出了原分析片段，请核对起止位置。':!onOpenReference?'当前页面暂不支持素材回看。':'';
  return <><button className="text-button" disabled={!!reason} onClick={()=>{if(!reason)onOpenReference?.(ref,dirty)}}>{label} · {asset?.title||'原素材暂不可用'} · {ref.start_ms==null?'整图':`${ref.start_ms/1000}–${ref.end_ms/1000}秒`}</button>{reason&&<span className="script-ref-reason">{reason}</span>}</>;
 };
 const editOwnRef=(i:number,n:number,patch:Item)=>editRow(i,{own_references:(draft!.rows[i].own_references||[]).map((r:Item,j:number)=>j===n?{...r,...patch}:r)});
 return <div className="view-body script-workspace script-workspace-053">
  {message&&<p role="status" className="notice">{message}</p>}
  {draftWarning&&<p role="alert">{draftWarning}<button className="text-button" onClick={()=>draftsReady?persist():void restoreDrafts()}>{draftsReady?'重试保存草稿':'重试读取草稿'}</button></p>}
  {!draftsReady&&!draftWarning&&<p role="status">正在读取本机脚本草稿…</p>}
  {recoveries.length>0&&<details><summary>其他窗口保留的脚本草稿</summary>{recoveries.map(entry=><button className="quiet bordered" key={entry.identity} onClick={()=>{remember();draftIdentity.current=entry.identity;cache.current=structuredClone(entry.content.scriptDrafts);setDraft(cache.current.drafts[cache.current.active]||null);setDirty(!!cache.current.drafts[cache.current.active])}}>恢复：{entry.content.scriptDrafts.drafts[entry.content.scriptDrafts.active]?.title||'未完成脚本'}</button>)}</details>}
  <div className="script-workspace-heading"><div><h2>拍摄脚本</h2><p className="fine">把画面、台词和素材整理到一起。</p></div><button className="quiet bordered" disabled={busy||!draftsReady} onClick={create}>{cache.current.drafts.__new?'继续未保存脚本':'新建脚本'}</button></div>
  <div className="script-list" aria-label="已保存脚本">{scripts.map(s=><button className="quiet bordered" key={s.id} disabled={busy||!draftsReady} aria-pressed={draft?.id===s.id} onClick={()=>open(s)}>{s.title} · v{s.version}</button>)}</div>
  {!draft&&<p className="script-empty">选择一条脚本继续修改，或新建拍摄脚本。</p>}
  {draft&&<section className="script-editor" aria-label="脚本编辑区">
   <div className="script-save-bar"><div className="script-save-actions"><button className="primary" disabled={busy||!draftsReady||!draft.objective?.trim()} onClick={()=>save(false)}>保存新版本</button><button className="quiet bordered" disabled={busy||!draftsReady||!draft.objective?.trim()} onClick={()=>save(true)}>确认当前脚本</button><span className="script-save-state" role="status">{dirty?'修改尚未保存':draft.confirmed?'已保存 · 已确认':'已保存'}</span></div><div className="script-export-actions" role="group" aria-label="导出拍摄稿"><span>导出拍摄稿</span>{['docx','xlsx','pdf','md','json'].map(f=><button className="quiet bordered" key={f} disabled={busy||!draftsReady||dirty||!draft.id} onClick={()=>run(()=>download(base+`/scripts/${draft.id}/export?format=${f}&version=${draft.version}`,`${draft.title}-v${draft.version}.${f}`))}>{f.toUpperCase()}</button>)}</div></div>
   <fieldset disabled={busy||!draftsReady} className="script-editor-fields">
    <div className="script-title-fields"><label className="field">脚本标题<input aria-label="脚本标题" value={draft.title||''} onChange={e=>edit({title:e.target.value})}/></label><label className="field">创作目标<input aria-label="脚本创作目标" value={draft.objective||''} onChange={e=>edit({objective:e.target.value})}/></label></div>
    <div className="script-revise-actions" role="group" aria-label="继续修改脚本">{[['换个开头','换个开头，保留其余脚本和当前可用素材范围。'],['缩短到30秒','缩短到30秒，优先保留核心卖点和现有素材。'],['减少补拍','减少补拍，优先使用当前已确认可复用的素材。']].map(([label,instruction])=><button className="quiet bordered" key={label} disabled={busy||dirty||!draft.id} onClick={()=>onUseScript({...draft,edit_instruction:instruction})}>{label}</button>)}{dirty?<span>先保存修改，再调整这份稿。</span>:!draft.id&&<span>保存后可请 AI 帮你修改。</span>}</div>
    {draft.timing_estimated&&<p className="fine">时长为估算，试读和试拍后再核对。</p>}
    {draft.unresolved_items?.length>0&&<details className="script-unresolved"><summary>制作前需要确认 · {draft.unresolved_items.length} 项</summary>{draft.unresolved_items.map((item:string,n:number)=><p key={n}>{item}{!(draft.validation_issues||[]).includes(item)&&<button className="quiet" aria-label={`将第${n+1}项归入制作备注`} onClick={()=>edit({unresolved_items:draft.unresolved_items.filter((_:string,index:number)=>index!==n),production_notes:[...(draft.production_notes||[]),item]})}>归入制作备注</button>}</p>)}</details>}
    <div className="script-table-wrap"><CreativeBasis base={base} snapshotId={draft.context_snapshot_id} onOpen={ref=>onOpenReference?.(ref,dirty)}/><table className="script-edit-table"><colgroup><col className="script-col-shot"/><col className="script-col-visual"/><col className="script-col-dialogue"/><col className="script-col-duration"/><col className="script-col-material"/></colgroup><thead><tr>{['镜头','画面怎么拍','台词 / 字幕','时长','用哪段素材'].map(label=><th scope="col" key={label}>{label}</th>)}</tr></thead><tbody>
     {draft.rows.map((r:Item,i:number)=><Fragment key={i}>
      <tr className="script-main-row"><th scope="row"><span className="script-shot-number">{i+1}</span></th><td><textarea aria-label={`第${i+1}行画面`} value={r.visual||''} placeholder="描述这个镜头怎样拍" onChange={e=>editRow(i,{visual:e.target.value})}/></td><td><textarea aria-label={`第${i+1}行台词`} value={r.dialogue||''} placeholder="写下要说的话" onChange={e=>editRow(i,{dialogue:e.target.value})}/><input className="script-subtitle" aria-label={`第${i+1}行字幕`} value={r.subtitle||''} placeholder="字幕（可选）" onChange={e=>editRow(i,{subtitle:e.target.value})}/>{(r.factual_claims||[]).filter(needsClaimReview).map((claim:Item,n:number)=><p className="script-inline-confirmation" key={n}>需要确认：{claim.statement}</p>)}</td><td><label className="script-duration"><input aria-label={`第${i+1}镜时长`} type="number" min="0.1" step="0.1" value={Math.round((r.end_ms-r.start_ms)/100)/10} onChange={e=>editRow(i,{end_ms:r.start_ms+Math.round(Number(e.target.value)*1000)})}/><span>秒</span></label><span className="script-time-range">{r.start_ms/1000}–{r.end_ms/1000}秒</span></td><td>
       {(r.own_references||[]).map((ref:Item,n:number)=><div className="script-material-link" key={n}>{referenceLink(ref,`回看自有片段${n+1}`,true)}</div>)}
       {!r.own_references?.length&&<p className="script-material-hint">{r.reshoot?.trim()?'补拍安排':'选择已有素材，或写下这个画面怎样补拍。'}</p>}
       <label className="field script-reshoot-field"><span>补拍动作</span><textarea aria-label={`第${i+1}镜补拍说明`} value={r.reshoot||''} placeholder="例如：补拍一次拆开包装的近景" onChange={e=>editRow(i,{reshoot:e.target.value})}/></label>
       <button className="quiet script-add-material" disabled={!ownAssets.length} aria-label={`为第${i+1}镜添加自有片段`} onClick={()=>setPickRow(i)}>添加自有素材片段</button>
      </td></tr>
      <tr className="script-detail-row"><td colSpan={5}><details className="script-shot-details"><summary>镜头详情与依据<span>第 {i+1} 镜</span></summary><div className="script-shot-detail-body">
       <div className="form-grid"><label className="field">起点（秒）<input aria-label={`第${i+1}行起点`} type="number" min="0" step="0.1" value={r.start_ms/1000} onChange={e=>editRow(i,{start_ms:Math.round(Number(e.target.value)*1000)})}/></label><label className="field">终点（秒）<input aria-label={`第${i+1}行终点`} type="number" min="0" step="0.1" value={r.end_ms/1000} onChange={e=>editRow(i,{end_ms:Math.round(Number(e.target.value)*1000)})}/></label><label className="field">语气<input value={r.tone||''} onChange={e=>editRow(i,{tone:e.target.value})}/></label><label className="field">制作说明<input value={r.notes||''} onChange={e=>editRow(i,{notes:e.target.value})}/></label><label className="field">镜头调度<input value={r.camera||''} onChange={e=>editRow(i,{camera:e.target.value})}/></label><label className="field">道具（以逗号分隔）<input value={(r.props||[]).join('，')} onChange={e=>editRow(i,{props:e.target.value.split(/[,，]/).filter(Boolean)})}/></label><label className="field">计划补拍时长（秒）<input aria-label={`第${i+1}镜计划补拍时长`} type="number" min="0" step="0.1" value={r.reshoot_duration_ms==null?'':r.reshoot_duration_ms/1000} onChange={e=>editRow(i,{reshoot_duration_ms:e.target.value===''?null:Math.round(Number(e.target.value)*1000)})}/></label></div>
       {r.references?.length>0&&<section aria-label={`第${i+1}镜参考片段`}><h4>参考依据</h4>{r.references.map((ref:Item,n:number)=><p className="fine" key={n}>{referenceLink(ref,`回看参考${n+1}`)}{ref.observation&&<span> · {ref.observation}</span>}</p>)}</section>}
       <div className="own-fragment-editor">{(r.own_references||[]).map((ref:Item,n:number)=><div className="own-fragment" key={n}><h4>自有片段 {n+1}</h4><label className="field">自有素材<select aria-label={`第${i+1}镜自有片段${n+1}素材`} value={ref.asset_id} onChange={e=>{const a=ownAssets.find(v=>v.id===e.target.value);if(a)editOwnRef(i,n,ownRef(a))}}>{!ownAssets.some(a=>a.id===ref.asset_id)&&<option value={ref.asset_id}>原素材（目前不可重新选用）</option>}{ownAssets.map(a=><option key={a.id} value={a.id}>{a.title}</option>)}</select></label>{ref.start_ms!=null&&<div className="form-grid"><label className="field">源片段起点（秒）<input type="number" min="0" step="0.1" aria-label={`第${i+1}镜自有片段${n+1}起点`} value={ref.start_ms/1000} onChange={e=>editOwnRef(i,n,{start_ms:Math.round(Number(e.target.value)*1000)})}/></label><label className="field">源片段终点（秒）<input type="number" min="0" step="0.1" aria-label={`第${i+1}镜自有片段${n+1}终点`} value={ref.end_ms/1000} onChange={e=>editOwnRef(i,n,{end_ms:Math.round(Number(e.target.value)*1000)})}/></label></div>}<div className="form-grid">{ref.start_ms!=null?<label className="field">播放方式<select aria-label={`第${i+1}镜自有片段${n+1}播放方式`} value={ref.usage_mode||'normal'} onChange={e=>editOwnRef(i,n,{usage_mode:e.target.value})}>{[['normal','正常速度'],['freeze_frame','定格'],['slow_motion','慢放'],['loop','循环']].map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label>:<p className="fine">图片按整图展示。</p>}<label className="field">计划使用时长（秒）<input aria-label={`第${i+1}镜自有片段${n+1}计划使用时长`} type="number" min="0.001" step="0.1" value={ref.use_duration_ms==null?'':ref.use_duration_ms/1000} onChange={e=>editOwnRef(i,n,{use_duration_ms:e.target.value===''?null:Math.round(Number(e.target.value)*1000)})}/></label></div><label className="field">播放安排说明<input aria-label={`第${i+1}镜自有片段${n+1}播放安排说明`} value={ref.usage_note||''} onChange={e=>editOwnRef(i,n,{usage_note:e.target.value})}/></label>{ref.start_ms!=null&&<p className="fine">定格、慢放或循环时，填写使用时长和播放安排。</p>}<label className="field">原片选材说明<input value={ref.observation||''} onChange={e=>editOwnRef(i,n,{observation:e.target.value})}/></label><button className="quiet" onClick={()=>editRow(i,{own_references:r.own_references.filter((_:Item,j:number)=>j!==n)})}>移除此自有片段</button></div>)}{!ownAssets.length&&<p className="fine">当前没有可复用的自有素材，可以先写补拍动作。</p>}</div>
       {r.claim_ids?.length>0&&<div className="claim-evidence"><h4>商品依据</h4>{r.claim_ids.map((id:string)=>{const fact=draft.product_facts_snapshot?.claims?.find((c:Item)=>c.fact_id===id);return <p className="fine" key={id}>{fact?`${fact.statement} · 来源：${fact.source||'尚未填写'}${fact.valid_until?' · 有效期：'+fact.valid_until:''}`:'这条卖点还没有确认，补充资料后再使用。'}</p>})}</div>}
       {(r.factual_claims||[]).map((claim:Item,n:number)=><div className="semantic-claim" key={n}><label className="field">需要核对的说法<input aria-label={`第${i+1}镜说法${n+1}`} value={claim.statement||''} onChange={e=>editRow(i,{factual_claims:r.factual_claims.map((c:Item,j:number)=>j===n?{...c,statement:e.target.value,reviewed:false}:c)})}/></label>{(claim.fact_ids||[]).map((id:string)=>{const fact=draft.product_facts_snapshot?.claims?.find((c:Item)=>c.fact_id===id);return <p key={id} className="fine">{fact?`${fact.statement} · 来源：${fact.source||'尚未填写'}`:'这条卖点还没有确认，补充资料后再使用。'}</p>})}<label className="check-label"><input type="checkbox" checked={!!claim.reviewed} onChange={e=>editRow(i,{factual_claims:r.factual_claims.map((c:Item,j:number)=>j===n?{...c,reviewed:e.target.checked}:c)})}/>我已核对这条说法与依据</label><p className="fine">核对内容、数值和有效期后再确认。</p></div>)}
       <button className="quiet" disabled={draft.rows.length<=1} onClick={()=>edit({rows:draft.rows.filter((_:Item,j:number)=>j!==i)})}>移除此镜</button>
      </div></details></td></tr>
     </Fragment>)}
    </tbody></table></div>
    <button className="quiet bordered script-add-shot" onClick={()=>{const start=draft.rows.at(-1)?.end_ms||0;edit({rows:[...draft.rows,{...row(),start_ms:start,end_ms:start+3000}]})}}>添加镜头</button>
    <details className="script-preparation"><summary>拍摄准备<span>已有素材 {footage.length} · 还需补拍 {reshoots.length} · 需要确认 {unreviewedClaims.length}</span></summary><div className="script-preparation-grid"><section><h3>已有素材（{footage.length}处）</h3>{footage.length?footage.map(({row,...ref}:Item,n:number)=><p className="fine" key={n}>{referenceLink(ref,`回看第${row}镜自有片段`,true)}</p>):<p className="fine">尚未选择自有片段。</p>}</section><section><h3>还需补拍（{reshoots.length}处）</h3>{reshoots.length?reshoots.map((item:Item,n:number)=><p className="fine" key={n}>第{item.row}镜 · {item.text}</p>):<p className="fine">尚未记录补拍安排。</p>}</section><section><h3>需要确认（{unreviewedClaims.length}处）</h3>{unreviewedClaims.length?unreviewedClaims.map((item:Item,n:number)=><p className="fine" key={n}>第{item.row}镜 · {item.text}</p>):<p className="fine">没有已记录的待确认说法，请继续核对台词。</p>}</section></div></details>
    <details className="script-version-details"><summary>脚本信息与版本</summary><div className="form-grid"><label className="field">脚本采用状态<select aria-label="脚本采用状态" value={draft.usage_status||'planned'} onChange={e=>edit({usage_status:e.target.value})}>{[['planned','计划试拍'],['adopted','已采用'],['shot','已拍摄'],['rejected','已放弃']].map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label><label className="field">采用反馈<input value={draft.feedback_reason||''} onChange={e=>edit({feedback_reason:e.target.value})}/></label></div><label className="field">制作备注（每行一条）<textarea aria-label="制作备注" value={(draft.production_notes||[]).join('\n')} onChange={e=>edit({production_notes:e.target.value.split('\n')})}/></label>{draft.source&&<p className="fine">来自第{draft.source.turn_index+1}轮分析，原始结论和素材依据随版本保留。</p>}{draft.source&&<details><summary>来源详情</summary><p className="fine">分析记录：{draft.source.session_id}</p>{draft.source.binding_id&&<p className="fine">模型：{draft.source.binding_id}</p>}</details>}<details><summary>版本历史 · {(draft.revisions||[]).length}</summary>{(draft.revisions||[]).map((v:Item)=><div key={v.version}><b>v{v.version} · {v.confirmed?'已确认':'草稿'}</b><p>{v.rows.map((r:Item)=>r.dialogue).join(' / ')}</p></div>)}</details></details>
   </fieldset>
  </section>}
  {pickRow!=null&&<FootagePicker base={base} assets={ownAssets} onClose={()=>setPickRow(null)} onChoose={ref=>{editRow(pickRow,{own_references:[...(draft!.rows[pickRow].own_references||[]),ref]});setPickRow(null)}}/>}
  {tasks.length>0&&<details className="script-production-history"><summary>历史制作记录 · {tasks.length}</summary>{tasks.map(t=><div className="production-row" key={t.id}><b>{t.script_snapshot?.title||'历史脚本'} · v{t.script_version}</b><p className="fine">{t.assignee||'未分配'} · {({planned:'待制作',in_progress:'制作中',in_review:'待评审',approved:'已通过',cancelled:'已取消'} as Item)[t.status]||t.status}{t.finished_asset_id?' · 已关联成片':''}</p></div>)}</details>}
 </div>
}
