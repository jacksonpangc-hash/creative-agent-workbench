import {useEffect,useId,useRef,useState,type ReactNode} from 'react';
import {API,blob,post,request,teamMode} from '../api';
import './LinkedMediaDetails.css';
type Item=Record<string,any>;
type FindingReference={fragment:Fragment,label:string,accessible:string};
type AnalysisFinding={key:string,text:string,type:string,evidence:Item[],scope:Item,turn:number,observation?:Item,legacy:boolean,origin?:string};
export type Fragment={asset_id:string,scene_id?:string,start_ms:number,end_ms:number,observation:string,provenance:'subtitle'|'sampled_frames'|'native_video'};
const clock=(ms:number)=>`${Math.floor(ms/60000).toString().padStart(2,'0')}:${Math.floor(ms/1000%60).toString().padStart(2,'0')}`;
const exactClock=(ms:number)=>`${clock(ms)}.${Math.floor(ms%1000).toString().padStart(3,'0')}`;
const stamp=(ms:number)=>`${Math.floor(ms/3600000).toString().padStart(2,'0')}:${clock(ms%3600000)},${Math.floor(ms%1000).toString().padStart(3,'0')}`;
const tabs=[['analysis','分析'],['transcript','台词'],['scenes','分镜']] as const;
const visual=(e:Item)=>e.observation?['visual_observation','screen_text'].includes(e.observation.claim_kind):['sampled_frames','native_video','image'].includes(e.provenance);
const spoken=(e:Item)=>e.observation?['spoken_content','transcript_content','spoken_evidence'].includes(e.observation.claim_kind):['subtitle','local_asr'].includes(e.provenance);
const claimLabels:Record<string,string>={visual_observation:'画面观察',screen_text:'画面文字',spoken_content:'台词内容',transcript_content:'台词内容',spoken_evidence:'已校对台词',creative_intent:'创意目的',creative_proposal:'创意提案',product_fact:'商品陈述',provider_content:'来源内容',acoustic_observation:'声音信息'};
function findingLabel(finding:AnalysisFinding){
 if(finding.legacy)return '旧版依据';
 const observation=finding.observation;
 if(observation?.interpretation==='proposed')return '创意提案';
 if(observation?.interpretation==='inferred')return '推断，待验证';
 if(observation?.interpretation==='unknown')return '暂不确定';
 return claimLabels[observation?.claim_kind]||'已有观察';
}
function storedTranscriptPolicy(asset:Item):Item{
 const supplied=asset.transcript_policy||asset.metadata?.transcript_policy;
 if(supplied)return supplied;
 const saved=asset.metadata?.transcription||{};
 return {allowed:false,quality_state:saved.quality_state||'unknown',execution_state:saved.execution_state||(asset.metadata?.subtitles?.length?'succeeded':'pending'),review_state:saved.review_state||(saved.reviewed?'corrected':'unreviewed')};
}
function savedFindings(state:Item,asset:Item):AnalysisFinding[]{
 const assetId=asset.id,results:AnalysisFinding[]=[];
 const scoped=(scope:Item,start:number,end:number)=>{
  const ranges=(scope.selected_segments||[]).filter((r:Item)=>r.asset_id===assetId);
  return !ranges.length||ranges.some((r:Item)=>r.start_ms<=start&&end<=r.end_ms);
 };
 const granular=(carrier:Item,scope:Item,key:string,turn:number,origin?:string):AnalysisFinding[]=>{
  const manifest=carrier.input_manifest;
  return (Array.isArray(carrier.observations)?carrier.observations:[]).flatMap((observation:Item,index:number)=>{
   if(observation.asset_id!==assetId||!observation.input_manifest_id||!manifest||manifest.id!==observation.input_manifest_id||manifest.asset_id!==assetId||
      (asset.sha256&&observation.asset_sha256!==asset.sha256)||typeof observation.statement!=='string'||!Array.isArray(observation.support))return [];
   const currentPolicy=storedTranscriptPolicy(asset);
   if(observation.support.some((support:Item)=>support.transcript_segment_id)&&currentPolicy.transcript_revision&&manifest.transcript?.transcript_revision&&currentPolicy.transcript_revision!==manifest.transcript.transcript_revision)return [];
   const supported=observation.support.every((support:Item)=>{
    if(support.frame_id){const frame=manifest.frames?.find((f:Item)=>f.frame_id===support.frame_id);return frame&&frame.time_ms===support.time_ms&&scoped(scope,frame.time_ms,frame.time_ms+1)}
    if(support.transcript_segment_id){const segment=manifest.transcript?.segments?.find((s:Item)=>s.segment_id===support.transcript_segment_id);return segment&&segment.start_ms===support.start_ms&&segment.end_ms===support.end_ms&&scoped(scope,segment.start_ms,segment.end_ms)}
    return support.type==='native_video'&&manifest.native_video&&support.start_ms>=manifest.native_video.start_ms&&support.end_ms<=manifest.native_video.end_ms&&scoped(scope,support.start_ms,support.end_ms);
   });
   if(!supported)return [];
   return [{key:`${key}-observation-${observation.observation_id||index}`,text:observation.statement,type:observation.interpretation==='inferred'?'hypothesis':'fact',evidence:[{...carrier,observation}],scope,turn,observation,legacy:false,origin}];
  });
 };
 for(const session of Array.isArray(state.sessions)?state.sessions:[]){
  const scope=session.scope||{};
  if(!Array.isArray(scope.asset_ids)||!scope.asset_ids.includes(assetId))continue;
  const found:AnalysisFinding[]=[];
  for(const [index,turn] of (session.turns||[]).entries()){
   for(const carrier of (turn.evidence||[]).filter((e:Item)=>e.asset_id===assetId))found.push(...granular(carrier,scope,`${session.id}-${index}`,index+1));
   for(const [n,finding] of (turn.answer?.findings||[]).entries()){
    const ids=Array.isArray(finding.evidence_ids)?finding.evidence_ids:[];
    const evidence=(turn.evidence||[]).filter((e:Item)=>ids.includes(e.id)&&e.asset_id===assetId&&!e.observations?.length&&
      (!(scope.selected_segments||[]).some((r:Item)=>r.asset_id===assetId)||Number.isFinite(e.start_ms)&&Number.isFinite(e.end_ms)&&e.end_ms>e.start_ms&&scoped(scope,e.start_ms,e.end_ms)));
    if(evidence.length&&typeof finding.text==='string'&&finding.text.trim())found.push({key:`${session.id}-${index}-${n}`,text:finding.text,type:finding.type,evidence,scope,turn:index+1,legacy:true});
   }
  }
  const latest=Math.max(0,...found.map(f=>f.turn));results.push(...found.filter(f=>f.turn===latest));
 }
 for(const record of Array.isArray(state.parses)?state.parses:[]){
  if(!record.asset_ids?.includes(assetId))continue;
  for(const [index,row] of (record.rows||[]).entries()){
   if(row.asset_id!==assetId)continue;
   const scope=record.source_context?.scope||{asset_ids:record.asset_ids};
   results.push(...granular(row,scope,`parse-${record.id}-${index}`,0,'字段拆解'));
   if(!row.observations?.length&&!row.input_manifest_id){
    for(const [name,value] of Object.entries(row.fields||{}))if(typeof value==='string'&&value.trim())results.push({key:`legacy-parse-${record.id}-${index}-${name}`,text:`${name}：${value}`,type:'fact',evidence:[row],scope,turn:0,legacy:true,origin:'字段拆解'});
   }
  }
 }
 const seen=new Set<string>();
 return results.filter(f=>{const id=f.observation?.observation_id||f.key;if(seen.has(id))return false;seen.add(id);return true});
}
export default function LinkedMediaDetails({base,asset,reload,onEvidence,initialFragment,transcriptActions,onWriteScript,onAnalyze}:{base:string,asset:Item,reload:()=>Promise<unknown>,onEvidence?:(fragment:Fragment)=>void,initialFragment?:Fragment|null,transcriptActions?:ReactNode,onWriteScript?:()=>void,onAnalyze?:()=>void}){
 const player=useRef<HTMLVideoElement>(null),tabId=useId();
 const [playbackRevision,setPlaybackRevision]=useState(0);
 const [results,setResults]=useState<Item[]>([]),[resultId,setResultId]=useState(''),[sceneLimit,setSceneLimit]=useState(12);
 const [tab,setTab]=useState('analysis'),[findings,setFindings]=useState<AnalysisFinding[]>([]),[analysisState,setAnalysisState]=useState<'loading'|'ready'|'failed'>('loading');
 const [url,setUrl]=useState(''),[scenes,setScenes]=useState<Item[]>([]),[sceneUrls,setSceneUrls]=useState<Record<string,string>>({}),[current,setCurrent]=useState(0),[selected,setSelected]=useState<Fragment|null>(null),[editing,setEditing]=useState(false),[lines,setLines]=useState<Item[]>(asset.metadata?.subtitles||[]),[message,setMessage]=useState(''),[saving,setSaving]=useState(false),[policy,setPolicy]=useState<Item>(()=>storedTranscriptPolicy(asset)),[selectedAllowed,setSelectedAllowed]=useState(true);
 useEffect(()=>{let active=true;setFindings([]);setAnalysisState('loading');
  request<Item>(base+`/assets/${asset.id}/analysis-results`).then(view=>{if(active){const items=Array.isArray(view.results)?view.results:[];setResults(items);setResultId(items[0]?.id||'');if(view.asset)setPolicy(storedTranscriptPolicy(view.asset));setAnalysisState('ready')}}).catch(()=>{if(active)setAnalysisState('failed')});
  return()=>{active=false};
 },[base,asset.id,asset.sha256,asset.metadata?.transcript,asset.transcript_policy?.transcript_revision]);
 useEffect(()=>{const item=results.find(r=>r.id===resultId);setFindings(item?savedFindings({sessions:item.kind==='session'?[item.record]:[],parses:item.kind==='parse'?[item.record]:[]},asset).map(f=>({...f,turn:item.kind==='session'?(item.turn_index||0)+1:f.turn})):[])},[results,resultId,asset]);
 useEffect(()=>{setPolicy(storedTranscriptPolicy(asset))},[asset]);
 useEffect(()=>{setTab('analysis');setCurrent(0);setEditing(false);setMessage('')},[base,asset.id]);
 useEffect(()=>{let active=true;const urls:string[]=[];setUrl('');setScenes([]);setSceneUrls({});setSelected(null);setLines(asset.metadata?.subtitles||[]);
  void (async()=>{if(asset.media_type==='video'&&!teamMode){const ticket=await post(base+`/media-ticket/${asset.id}`,{});if(ticket.streaming===true&&typeof ticket.path==='string'&&/^\/api\/media-streams\/[A-Za-z0-9_-]+$/.test(ticket.path)){if(active)setUrl(API+ticket.path);return}if(ticket.streaming!==false)throw new Error('播放连接无效')}const b=await blob(base+`/assets/${asset.id}/file`);const value=URL.createObjectURL(b);if(active){urls.push(value);setUrl(value)}else URL.revokeObjectURL(value)})().catch(()=>{if(active)setMessage('原始素材暂时无法读取，请重新连接播放。')});
  if(asset.inspection==='ready')request<Item[]>(base+`/assets/${asset.id}/scenes`).then(async values=>{if(!active)return;const next=Array.isArray(values)?values:[];setScenes(next);setSceneLimit(12)}).catch(()=>{if(active)setMessage('分镜暂时无法读取，请稍后重试。')});
  return()=>{active=false;urls.forEach(u=>URL.revokeObjectURL(u))};
 },[base,asset.id,asset.inspection,asset.metadata?.transcript,asset.transcript_policy?.transcript_revision,playbackRevision]);
 useEffect(()=>{if(tab!=='scenes')return;let active=true;const urls:string[]=[];void (async()=>{for(const scene of scenes.slice(0,sceneLimit)){try{const b=await blob(base+`/assets/${asset.id}/scene/${scene.id}`);if(!active)break;const u=URL.createObjectURL(b);urls.push(u);setSceneUrls(v=>({...v,[scene.id]:u}))}catch{}}})();return()=>{active=false;urls.forEach(u=>URL.revokeObjectURL(u));setSceneUrls({})}},[base,asset.id,tab,scenes,sceneLimit]);
 const seek=(start:number,end:number,observation:string,provenance:Fragment['provenance'],scene_id?:string,allowed=true)=>{if(player.current)player.current.currentTime=start/1000;setCurrent(start);setSelectedAllowed(allowed);setSelected({asset_id:asset.id,start_ms:start,end_ms:end,observation,provenance,...(scene_id?{scene_id}:{})})};
 const applyInitial=()=>{if(initialFragment&&initialFragment.asset_id===asset.id)seek(initialFragment.start_ms,initialFragment.end_ms,initialFragment.observation,initialFragment.provenance,initialFragment.scene_id)};
 useEffect(()=>{if(player.current&&player.current.readyState>=1)applyInitial()},[initialFragment,url]);
 const evidenceFragment=(e:Item,scope:Item,text:string):Fragment|null=>{
  const {start_ms:start,end_ms:end}=e,duration=asset.metadata?.duration_ms;
  if(asset.media_type!=='video'||!['native_video','sampled_frames','subtitle','local_asr'].includes(e.provenance)||!Number.isFinite(start)||!Number.isFinite(end)||start<0||end<=start||(Number.isFinite(duration)&&end>duration))return null;
  const ranges=(scope.selected_segments||[]).filter((r:Item)=>r.asset_id===asset.id);
  if(ranges.length&&!ranges.some((r:Item)=>r.start_ms<=start&&r.end_ms>=end))return null;
  const provenance:Fragment['provenance']=e.provenance==='native_video'?'native_video':e.provenance==='sampled_frames'?'sampled_frames':'subtitle';
  return {asset_id:asset.id,start_ms:start,end_ms:end,observation:text,provenance,...(e.scene_id?{scene_id:e.scene_id}:{})};
 };
 const save=async()=>{setSaving(true);setMessage('');try{const text=lines.map((s,i)=>`${i+1}\n${stamp(s.start_ms)} --> ${stamp(s.end_ms)}\n${s.text}`).join('\n\n');await post(base+`/assets/${asset.id}/subtitles`,{text,reviewed:true,...(policy.transcript_revision?{expected_transcript_revision:policy.transcript_revision}:{})});await reload();setEditing(false);setMessage('台词校正已保存。')}catch(e){setMessage((e as Error).message)}finally{setSaving(false)}};
 const needsReview=['suspect','unusable','unknown'].includes(policy.quality_state)||!!asset.metadata?.transcription?.needs_review||!!asset.metadata?.transcription?.warnings?.length;
 const executionLabel:Record<string,string>={pending:'等待识别',running:'正在识别',succeeded:'识别完成',failed:'识别失败',cancelled:'已取消识别'};
 const qualityLabel:Record<string,string>={unknown:'台词质量待确认',usable:'台词可用',suspect:'台词有疑点',unusable:'台词不可用'};
 const policyStatus=`${executionLabel[policy.execution_state]||'识别状态待确认'} · ${qualityLabel[policy.quality_state]||'台词质量待确认'} · ${policy.review_state==='corrected'?'已人工校对':'未校对'}`;
 const supportFragments=(finding:AnalysisFinding):FindingReference[]=>{
  if(!finding.observation)return finding.evidence.flatMap(e=>{const fragment=evidenceFragment(e,finding.scope,finding.text);return fragment?[{fragment,label:`${clock(fragment.start_ms)}–${clock(fragment.end_ms)}`,accessible:`定位片段 ${clock(fragment.start_ms)}–${clock(fragment.end_ms)}`}]:[]});
  return finding.observation.support.flatMap((support:Item)=>{
   const point=Number.isFinite(support.time_ms),start=point?support.time_ms:support.start_ms,end=point?Math.min(start+1,asset.metadata?.duration_ms||start+1):support.end_ms;
   if(!Number.isFinite(start)||!Number.isFinite(end)||start<0||end<=start||end>asset.metadata?.duration_ms)return [];
   const type:Fragment['provenance']=support.frame_id?'sampled_frames':support.transcript_segment_id?'subtitle':'native_video';
   const label=point?`画面 ${exactClock(start)}`:support.transcript_segment_id?`台词 ${exactClock(start)}–${exactClock(end)}`:`原片 ${exactClock(start)}–${exactClock(end)}`;
   const accessible=point?`回看画面 ${exactClock(start)}`:support.transcript_segment_id?`核对台词 ${exactClock(start)}–${exactClock(end)}`:`回看片段 ${exactClock(start)}–${exactClock(end)}`;
   return [{fragment:{asset_id:asset.id,start_ms:start,end_ms:end,observation:finding.text,provenance:type} as Fragment,label,accessible}];
  });
 };
 const transcriptOnly=findings.length>0&&findings.every(f=>f.evidence.every(spoken));
 return <div className="linked-media linked-media-053">
  <div className="linked-player">
   {asset.media_type==='video'?<video ref={player} aria-label="素材播放器" controls src={url||undefined} onError={()=>{if(url)setMessage('播放连接已失效，请重新连接播放。')}} onLoadedMetadata={applyInitial} onTimeUpdate={e=>setCurrent(e.currentTarget.currentTime*1000)}/>:url?<img src={url} alt={asset.title}/>:<p>正在读取素材…</p>}
   {message.includes('连接播放')&&<button className="quiet bordered" onClick={()=>{setMessage('');setPlaybackRevision(n=>n+1)}}>重新连接播放</button>}
   <p className="linked-timing">{clock(current)} / {Number.isFinite(asset.metadata?.duration_ms)?clock(asset.metadata.duration_ms):'时长待确认'}</p>
   <button className="primary" disabled={!selected||!onEvidence||!selectedAllowed} onClick={()=>selected&&onEvidence?.(selected)}>将所选片段带入对话</button>
   {selected&&!selectedAllowed&&<p className="fine">{selected.provenance==='subtitle'?'这条依据需要先核对，校正保存后可带入对话。':'这条观察仍需核对，暂不能直接带入对话。'}</p>}
   {selected&&<p className="fine">已选 {clock(selected.start_ms)}–{clock(selected.end_ms)} · {selected.observation||'所选镜头画面'}</p>}
  </div>
  <div className="linked-observations">
   {message&&<p role="status" className="notice">{message}</p>}
   <div className="media-detail-tabs" role="tablist" aria-label="视频内容">{tabs.map(([id,label])=><button key={id} id={`${tabId}-${id}`} role="tab" aria-selected={tab===id} aria-controls={`${tabId}-${id}-panel`} tabIndex={tab===id?0:-1} onClick={()=>setTab(id)} onKeyDown={e=>{const index=tabs.findIndex(([value])=>value===id);const next=e.key==='ArrowRight'?(index+1)%tabs.length:e.key==='ArrowLeft'?(index+tabs.length-1)%tabs.length:e.key==='Home'?0:e.key==='End'?tabs.length-1:-1;if(next>=0){e.preventDefault();setTab(tabs[next][0]);document.getElementById(`${tabId}-${tabs[next][0]}`)?.focus()}}}>{label}</button>)}</div>
   <section className="media-detail-panel" role="tabpanel" id={`${tabId}-analysis-panel`} aria-labelledby={`${tabId}-analysis`} hidden={tab!=='analysis'}>
    {results.length>0&&<label className="field">分析成果<select aria-label="分析成果" value={resultId} onChange={e=>setResultId(e.target.value)}>{results.map(r=><option key={r.id} value={r.id}>{r.title} · v{r.version} · {r.kind==='parse'?'拆解':'分析'}</option>)}</select></label>}
    {analysisState==='loading'?<p className="muted" role="status">正在读取已有分析…</p>:analysisState==='failed'?<div className="media-analysis-empty"><h3>已有分析暂时无法读取。</h3><p>关闭后重新打开这条素材，或在创作记录里查看。</p></div>:findings.length?<>
     {needsReview&&findings.some(f=>f.evidence.some(visual))&&<p className="media-transcript-warning" role="status">画面已分析，台词需要核对。</p>}<div className="media-analysis-heading"><h3>已有分析</h3><p className="fine">{transcriptOnly?'仅依据台词，尚未包含画面分析。':'点击结论下的时间，回到原片核对。'}</p></div>
     {findings.map(finding=>{const references=supportFragments(finding);return <article className="media-saved-finding" key={finding.key}>
      <div className="media-finding-meta"><span className="media-finding-label">{findingLabel(finding)}</span>{finding.observation&&finding.observation.quality_state!=='usable'&&<span className="media-finding-quality">{finding.observation.quality_state==='suspect'?'内容待核对':finding.observation.quality_state==='unusable'?'依据不可用':'依据待确认'}</span>}</div>
      <p>{finding.text.replace(/(?:自动(?:台词|转写)[^，；。]{0,6})?needs_review=true[、,\s]+reviewed=false/g,'自动识别的台词需要核对')}</p>
      <div className="media-finding-citations">{references.map(({fragment,label,accessible},n)=><button className="text-button" key={n} aria-label={accessible} onClick={()=>seek(fragment.start_ms,fragment.end_ms,fragment.observation,fragment.provenance,fragment.scene_id,(!finding.observation||finding.observation.quality_state==='usable')&&(fragment.provenance!=='subtitle'||policy.allowed===true))}>{label}</button>)}
       {!references.length&&<span className="fine">{asset.media_type==='image'?'依据当前图片':finding.observation?.interpretation==='proposed'?'新的拍摄建议，原片中未发生':'这条依据没有可定位的片段'}</span>}
       <span className="fine">{finding.origin||`第 ${finding.turn} 轮分析`}</span>
      </div>{finding.legacy&&<p className="media-legacy-note">旧记录只保留素材或片段引用，尚未逐条核对具体画面与台词。</p>}
     </article>})}
    </>:<div className="media-analysis-empty"><h3>还没有这条{asset.media_type==='image'?'图片':'视频'}的分析。</h3><p>可以把这条素材直接带入当前创作，继续提问或拆解。</p><p className="fine">可以先查看台词和分镜。</p></div>}
    {onAnalyze&&<button className="primary media-write-script" onClick={onAnalyze}>在对话中分析这条素材</button>}
    {onWriteScript&&<button className="quiet bordered media-write-script" onClick={onWriteScript}>结合我的素材写脚本</button>}
   </section>
   <section className="media-detail-panel" role="tabpanel" id={`${tabId}-transcript-panel`} aria-labelledby={`${tabId}-transcript`} hidden={tab!=='transcript'}>
    <div className="inline between"><h3>台词</h3>{lines.length>0&&<button className="quiet" disabled={saving} onClick={()=>{if(editing)setLines(asset.metadata?.subtitles||[]);setEditing(!editing)}}>{editing?'取消校正':'校正文字稿'}</button>}</div>
    <p className="media-transcript-status" role="status">{policyStatus}</p>
    {needsReview&&<p className="media-transcript-warning" role="status">台词需要核对，画面分析可以先看。</p>}
    {policy.quality_state==='usable'&&policy.review_state!=='corrected'&&<p className="fine">自动台词可用于找主题和参考表达；逐字引用前请先校对。</p>}
    {lines.length?<div className="transcript-lines">{lines.map((s,i)=>editing?<label className="field" key={i}><span>{clock(s.start_ms)}–{clock(s.end_ms)}</span><textarea disabled={saving} aria-label={`第${i+1}句台词`} value={s.text} onChange={e=>setLines(v=>v.map((line,n)=>n===i?{...line,text:e.target.value}:line))}/></label>:<button key={i} aria-current={current>=s.start_ms&&current<s.end_ms?'true':undefined} className="transcript-line" onClick={()=>seek(s.start_ms,s.end_ms,s.text,'subtitle',undefined,policy.allowed===true)}><time>{clock(s.start_ms)}</time><span>{s.text}</span></button>)}</div>:asset.metadata?.transcript?<><p className="fine">这份台词没有逐句时间，补充带时间的字幕后即可定位。</p><p className="untimed-transcript">{asset.metadata.transcript}</p></>:<p className="muted">还没有台词，可以提取口播或导入字幕。</p>}
    {editing&&<button className="primary transcript-save" disabled={saving||lines.some(s=>!s.text.trim())} onClick={save}>{saving?'正在保存…':'保存文字稿校正'}</button>}
    {transcriptActions&&<div className="media-transcript-actions">{transcriptActions}</div>}
   </section>
   <section className="media-detail-panel" role="tabpanel" id={`${tabId}-scenes-panel`} aria-labelledby={`${tabId}-scenes`} hidden={tab!=='scenes'}>
    <h3>分镜</h3>{scenes.length?<div className="linked-scenes">{(tab==='scenes'?scenes.slice(0,sceneLimit):[]).map((s,i)=><button className="linked-scene" key={s.id} onClick={()=>seek(s.start_ms,s.end_ms,s.description||'', 'sampled_frames',s.id)} aria-label={`镜头 ${i+1} · ${clock(s.start_ms)}`}>{sceneUrls[s.id]?<img src={sceneUrls[s.id]} alt={`镜头 ${i+1}`}/>:<span>画面加载中</span>}<time>{clock(s.start_ms)}–{clock(s.end_ms)}</time></button>)}</div>:<p className="muted">还没有可查看的分镜，视频处理完成后再试。</p>}{sceneLimit<scenes.length&&<button className="quiet bordered" onClick={()=>setSceneLimit(n=>n+12)}>查看更多分镜</button>}<p className="fine">这些画面用于定位片段，具体分析以已保存的结论为准。</p>
   </section>
  </div>
 </div>
}
