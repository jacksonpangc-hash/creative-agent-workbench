import {useRef,useState,type ReactNode} from 'react';
import Markdown from './Markdown';
import CreativeBasis from './CreativeBasis';
import {ArrowUp,ArrowUpRight,HelpCircle,Layers,ScanLine,Sparkles,X,LoaderCircle} from 'lucide-react';
type Item=Record<string,any>;
export type Choice={id:string,label:string,kind:string,value:string,disabled?:string};
export type Example={text:string,sourceKinds:string[],dateBasis:string,recentDays?:number,filters?:{account?:string,startDate?:string,endDate?:string,minSpend?:string,minRoas?:string}};
export const modeGuides:Record<string,{title:string,purpose:string,input:string,output:string,example:Example}>={
 directed:{title:'定向分析',purpose:'比较开头、卖点或口播，找出值得改的地方。',input:'选择素材或分组，说明要比较的开头、卖点或转化引导。',output:'比较结论、对应素材证据、改进建议和验证方法。',example:{text:'比较 @成片 的前3秒：哪些开头更清楚地呈现产品价值？请标出证据，提出一组开头测试。',sourceKinds:['finished'],dateBasis:'published'}},
 exploration:{title:'策略探索',purpose:'从一批素材里找共性、变化和下一轮选题。',input:'指定素材来源、时间范围和产品背景，允许在选定范围内发现素材。',output:'共性规律、创意差异、机会方向，以及尚缺的证据。',example:{text:'分析近30天发布的 @成片，比较脚本演变、卖点表达、人群需求与营销策略，给出下一轮测试机会。产品背景：请在这里补充。',sourceKinds:['finished'],dateBasis:'published',recentDays:30}},
 inspiration:{title:'灵感激发',purpose:'带上产品和拍摄条件，一起想下一条怎么拍。',input:'说明产品、已确认卖点、拍摄限制和参考素材；缺少关键条件时再补充。',output:'信息充分时直接给出创作方向、脚本大纲和简报；可继续比较与修改。',example:{text:'参考 @灵感素材，为我的产品构思三个新方向。产品：请补充；卖点：请补充；拍摄限制：请补充。先讨论各方向的测试假设。',sourceKinds:['inspiration'],dateBasis:'published'}}
};
export function ModeGuide({mode,onExample,compact=false}:{mode:string,onExample:(e:Example)=>void,compact?:boolean}){
 const guide=modeGuides[mode];
 return <section className={'mode-guide '+(compact?'compact':'')} aria-label={guide.title+'使用指引'}><p>{guide.purpose}</p>{compact&&<div className="guide-example"><p>{guide.example.text}</p><button className="quiet" onClick={()=>onExample(guide.example)}>使用示例<ArrowUpRight size={14}/></button></div>}</section>

}
export function ScopePicker({choices,active,onChoose}:{choices:Choice[],active:number,onChoose:(c:Choice)=>void}){
 return <div className="mention-picker" id="strategy-mentions" role="listbox" aria-label="素材范围候选">{choices.length?choices.map((c,i)=><button id={'mention-'+i} key={c.id} role="option" aria-selected={i===active} disabled={!!c.disabled} tabIndex={-1} onMouseDown={e=>e.preventDefault()} onClick={()=>onChoose(c)}><span>{c.label}</span><small>{c.disabled||({source:'来源',group:'分组',asset:'素材'} as Item)[c.kind]}</small></button>):<p>没有匹配的范围；可在素材面板导入或建立分组。</p>}</div>
}
export function StrategyComposer({value,onChange,onPick,onSubmit,disabled,children,choices,onUpload,onLinks,onReferences,onInstructions,onConnectModel,missingModel=false,actionLabel,blocker,status,onSettings,task='script',onLibrary,serviceLabel,analysisKind,onAnalysisKind}:{value:string,onChange:(s:string)=>void,onPick:(c:Choice)=>void,onSubmit:()=>void,disabled:boolean,models?:ReactNode,binding?:string,onBinding?:(s:string)=>void,quote?:string,onQuote?:(s:string)=>void,currency?:string,status:string,choices:Choice[],children?:ReactNode,analysisKind?:string,onAnalysisKind?:(kind:string)=>void,onUpload?:()=>void,onLinks?:()=>void,onReferences?:()=>void,onFacts?:()=>void,onInstructions?:()=>void,modeControls?:ReactNode,advancedContext?:ReactNode,onConnectModel?:()=>void,missingModel?:boolean,selectedCount?:number,actionLabel?:string,blocker?:string,onSettings?:()=>void,task?:string,onLibrary?:()=>void,serviceLabel?:string}){
 const text=useRef<HTMLTextAreaElement>(null),composing=useRef(false),add=useRef<HTMLDetailsElement>(null);
 const [mention,setMention]=useState<{start:number,end:number,query:string}|null>(null),[active,setActive]=useState(0);
 const candidates=choices.filter(c=>!mention?.query||c.label.toLowerCase().includes(mention.query.toLowerCase()));
 const inspect=(input:HTMLTextAreaElement)=>{const before=input.value.slice(0,input.selectionStart);const match=/(?:^|\s)@([^\s@]*)$/.exec(before);setMention(match?{start:before.lastIndexOf('@'),end:input.selectionStart,query:match[1]}:null);setActive(0)};
 const pick=(c:Choice)=>{if(c.disabled||!mention)return;onPick(c);onChange(value.slice(0,mention.start)+'@'+c.label+' '+value.slice(mention.end));setMention(null);text.current?.focus()};
 const choose=(fn?:()=>void)=>{if(add.current)add.current.open=false;fn?.()};
 const placeholder=task==='video'?'添加一条视频，看看它的开头、卖点和镜头安排。':task==='references'?'想找哪类视频？试试商品、场景或同行账号。':'说说这次想表达什么，也可以添加商品和现有素材。';
 const recover=missingModel&&task!=='references';
 return <section className="composer strategy-composer task-composer" aria-label="创作输入">
  {children}
  <div className="composer-input"><textarea ref={text} aria-label="创作目标" aria-haspopup="listbox" aria-expanded={!!mention} aria-controls={mention?'strategy-mentions':undefined} aria-activedescendant={mention&&candidates.length?'mention-'+active:undefined} placeholder={placeholder} value={value}
   onCompositionStart={()=>{composing.current=true}} onCompositionEnd={e=>{composing.current=false;inspect(e.currentTarget)}}
   onChange={e=>{onChange(e.target.value);if(!composing.current)inspect(e.target)}} onClick={e=>inspect(e.currentTarget)}
   onKeyDown={e=>{if(composing.current||e.nativeEvent.isComposing||e.keyCode===229)return;
    if(mention&&e.key==='Escape'){e.preventDefault();setMention(null);return}
    if(mention&&['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();const enabled=candidates.map((c,i)=>c.disabled?-1:i).filter(i=>i>=0);if(enabled.length){const n=enabled.indexOf(active);setActive(enabled[(n+(e.key==='ArrowDown'?1:-1)+enabled.length)%enabled.length])}return}
    if(mention&&e.key==='Enter'){e.preventDefault();if(candidates[active])pick(candidates[active]);return}
    if(e.key==='Enter'&&(e.metaKey||e.ctrlKey)){e.preventDefault();if(!disabled&&!recover)onSubmit()}
   }}/>{mention&&<ScopePicker choices={candidates} active={active} onChoose={pick}/>}</div>
  <div className="task-action-bar"><details ref={add} className="add-material-menu"><summary>添加</summary><div role="group" aria-label="添加材料"><button onClick={()=>choose(onUpload)}>添加视频</button><button onClick={()=>choose(onLinks)}>粘贴链接</button><button onClick={()=>choose(onLibrary)}>从素材库选择</button>{onInstructions&&<button onClick={()=>choose(onInstructions)}>常用指令</button>}</div></details>
   {task!=='references'&&onAnalysisKind&&<select className="analysis-method" aria-label="分析方式" value={analysisKind||'video'} onChange={e=>onAnalysisKind(e.target.value)}><option value="video">视频分析</option><option value="spoken">口播分析</option></select>}<span className="task-service">{task!=='references'&&serviceLabel&&<button className="text-button" onClick={onSettings}>{serviceLabel}</button>}</span>
   <button className="primary creation-submit" disabled={!recover&&disabled} onClick={recover?onConnectModel:onSubmit}>{recover?'连接模型':actionLabel||'发送'}</button>
  </div>
  {(recover||blocker||status)&&<div className="task-status" role="status">{recover?'连接一个模型服务后，就能分析视频和写脚本。':blocker||status}</div>}
 </section>
}
export function ScopeTags({tags,onRemove,onInspect,onAdjust}:{tags:{id:string,label:string}[],onRemove:(id:string)=>void,onInspect?:(id:string)=>void,onAdjust:()=>void}){
 return <div className="scope-tags" aria-label="当前分析范围">{tags.length?tags.map(t=><span className="scope-chip" key={t.id}><button onClick={()=>onInspect?.(t.id)}>{t.label}</button><button aria-label={'移除范围：'+t.label} onClick={()=>onRemove(t.id)}><X size={12}/></button></span>):<span className="muted">选择本次要分析的素材</span>}<button className="text-button" onClick={onAdjust}>添加分析对象</button></div>
}
export function EvidencePanel({children,open}:{children:ReactNode,open:boolean}){return <aside className={'sources '+(open?'open':'')}>{children}</aside>}
const sectionNames:Item={comparisons:'素材比较',recommendations:'改进建议',patterns:'共性规律',differences:'创意差异',opportunities:'策略机会',evidence_gaps:'还缺哪些信息',questions:'继续讨论',directions:'创作方向',script_outline:'脚本大纲'};
function ModeResult({answer}:{answer:Item}){return answer.result?<div className="mode-result">{answer.result.phase&&<span className="tag">{({diverge:'方向发散',compare:'方案比较',converge:'方案收敛'} as Item)[answer.result.phase]}</span>}{Object.entries(sectionNames).map(([k,label])=>Array.isArray(answer.result[k])&&answer.result[k].length>0?<section key={k}><h4>{label}</h4><ul>{answer.result[k].map((s:string,i:number)=><li key={i}>{s}</li>)}</ul></section>:null)}{answer.result.brief&&<section><h4>简报建议</h4><p>{answer.result.brief.objective}</p><p className="fine">{answer.result.brief.hypotheses.join('；')}</p></section>}</div>:null}
function findingLabel(finding:Item,evidence:Item[]){
 const product=finding.evidence_ids?.some((id:string)=>evidence.some(e=>e.id===id&&e.evidence_kind==='product_fact'));
 const cited=(finding.evidence_ids||[]).map((id:string)=>evidence.find(e=>e.id===id));
 if(finding.type==='fact'&&!product){if(cited.some((e:Item)=>e?.claim_kind==='provider_content'||e?.provider==='cmm'))return '来源提供的内容';if(cited.some((e:Item)=>['transcript_content','spoken_content','spoken_evidence'].includes(e?.claim_kind)))return '台词内容';if(cited.some((e:Item)=>e?.legacy_basis))return '旧版依据';}
 return finding.type==='fact'&&product?'商品资料':({fact:'观察事实',hypothesis:'待验证假设',missing:'数据缺失'} as Item)[finding.type];
}
function ProductCitation({evidence}:{evidence:Item}){
 return <details className="product-citation"><summary>商品资料</summary><p>{evidence.statement}</p><p>依据：{evidence.source}</p><p>v{evidence.facts_version} · {evidence.snapshot_at}</p><p>商品：{evidence.product_id} · SKU：{evidence.sku_id||'未标明'}</p>{evidence.valid_until&&<p>有效至：{evidence.valid_until}</p>}</details>
}
function Citation({evidence,id,onAsset}:{evidence:Item|undefined,id:string,onAsset:(id:string,startMs?:number,endMs?:number)=>void}){
 if(evidence?.evidence_kind==='product_fact')return <ProductCitation evidence={evidence}/>;
 return <button onClick={()=>onAsset(evidence?.asset_id||id,evidence?.start_ms,evidence?.end_ms)}>{evidence?.title||'素材证据'}</button>
}
export function ResultActions({turn,onBrief,onParse,onReport,onReview}:{turn:Item,onBrief:(indexes:number[])=>void,onParse:()=>void,onReport:()=>void,onReview:()=>void}){
 return <div className="result-actions"><div className="answer-actions"><button className="quiet" onClick={onReport}>保存到我的笔记</button><button className="quiet" onClick={onParse}>拆解画面</button><button className="quiet" onClick={onReview}>对照投放表现</button></div></div>
}

export default function StrategyWorkspace({mode,onMode,session,question,onQuestion,onExample,pending,onApplyExample,onDiscardExample,composer,onAsset,onExcerpt,onBrief,onParse,onReport,onReview,onScript,onInstructions,onTask,taskIntent}:{mode:string,onMode:(s:string)=>void,session:Item|undefined,question:string,onQuestion:(s:string)=>void,onExample:(e:Example)=>void,pending:Example|null,onApplyExample:()=>void,onDiscardExample:()=>void,composer:ReactNode,onAsset:(id:string,startMs?:number,endMs?:number)=>void,onExcerpt:(i:number,n:number)=>void,onBrief:(i:number,indices:number[])=>void,onParse:(i:number)=>void,onReport:(i:number)=>void,onReview:(i:number)=>void,onScript?:(i:number,scriptIndex?:number)=>void,onInstructions?:()=>void,onTask?:(task:string)=>void,taskIntent?:string}){
 const task=taskIntent||(mode==='inspiration'?'script':mode==='exploration'?'references':'video');
 const input=composer;
 const samples=task==='video'?['看看这条视频的开头为什么抓人。','拆解这条视频怎样展示卖点。','比较这些视频的镜头安排。']:task==='references'?['为这款商品找几个不同的拍摄角度。','找一些居家使用场景的参考。','看看同行如何展示产品细节。']:['用现有素材写一条30秒的商品介绍。','为这款商品写三个不同的开头。','减少补拍，用已有镜头安排一条视频。'];
 return <>

  <div className={'strategy-content '+(!session?'creation-start':'creation-results')}>
  <div className="creation-task-switch" role="group" aria-label="创作任务">{[['script','写脚本'],['video','拆视频'],['references','找参考']].map(([id,label])=><button key={id} aria-pressed={task===id} onClick={()=>onTask?.(id)}>{label}</button>)}</div>
  {!session?<><div className="strategy-welcome"><h2>今天想拍什么？</h2></div>{input}{!question.trim()&&<div className="task-examples" aria-label="试试这些想法">{samples.map(text=><button key={text} onClick={()=>onExample({text,sourceKinds:[],dateBasis:'imported'})}>{text}</button>)}</div>}</>:<><div className="conversation">{session.turns.map((turn:Item,i:number)=><section className="turn" key={i}><div className="user-question"><span className="avatar">我</span><p>{turn.question}</p></div><div className="answer"><div className="answer-label"><Sparkles size={16}/>{session.local_only?'素材检查':'创作结果'}<span>第 {i+1} 轮</span></div>{!session.local_only&&<CreativeBasis snapshot={turn.creative_basis} snapshotId={turn.context_snapshot_id} onOpen={ref=>onAsset(ref.asset_id,ref.start_ms,ref.end_ms)}/>}<div className="answer-summary"><Markdown text={turn.answer.content||turn.answer.summary}/></div>{session.local_only&&<ul className="local-material-status">{(turn.answer.result?.local_status||[]).map((a:Item)=><li key={a.asset_id}><button className="text-button" onClick={()=>onAsset(a.asset_id)}>{a.title}</button><span>{a.summary}</span></li>)}</ul>}{turn.answer.result?.delivery_progress&&<section className="delivery-progress" aria-label="交付进度"><b>已交付 {turn.answer.result.delivery_progress.completed} / {turn.answer.result.delivery_progress.requested} · {turn.answer.result.delivery_progress.state==='complete'?'已完成':'部分完成'}</b>{turn.answer.result.delivery_progress.state!=='complete'&&<p>在此会话继续输入“继续补齐”，沿用已固定的素材范围。</p>}{(turn.answer.result.duration_issues||[]).map((issue:string,n:number)=><p key={n}>{issue}</p>)}</section>}{!turn.answer.content&&<ModeResult answer={turn.answer}/>}{onScript&&turn.answer.result?.scripts?.map((script:Item,n:number)=><section className="generated-script" key={n}><h4>{script.title}</h4><p>{script.objective}</p><p className="fine">{script.rows?.length||0} 个镜头 · {script.rows?.at(-1)?.end_ms/1000||0} 秒</p><button className="quiet bordered" onClick={()=>onScript(i,n)}>保存并编辑脚本</button></section>)}{!turn.answer.result?.scripts?.length&&turn.answer.result?.script_outline?.length>0&&<p className="fine">这是历史脚本大纲。请继续要求生成完整脚本后再保存。</p>}{(turn.answer.findings||[]).map((finding:Item,n:number)=><article className="finding" key={n}><span className={'tag '+finding.type}>{findingLabel(finding,turn.evidence)}</span><p>{finding.text}</p><button className="quiet" onClick={()=>onExcerpt(i,n)}>保存摘录</button><div className="citations">{finding.evidence_ids?.map((id:string)=><Citation key={id} id={id} evidence={turn.evidence.find((e:Item)=>e.id===id)} onAsset={onAsset}/>)}</div></article>)}{turn.answer.next_tests?.length>0&&<div className="next-tests"><h4>下一轮可以这样测</h4>{turn.answer.next_tests.map((t:string,n:number)=><p key={n}>{n+1}. {t}</p>)}</div>}<details className="evidence-details"><summary>查看素材与观察记录 · {(turn.evidence||[]).length} 条</summary>{turn.evidence.map((e:Item)=>e.evidence_kind==='product_fact'?<ProductCitation key={e.id} evidence={e}/>:<button className="text-button" key={e.id} onClick={()=>{if(e.asset_id)onAsset(e.asset_id);else if(e.source_url)window.open(e.source_url,'_blank','noopener,noreferrer')}}>{e.title} · {e.provenance==='native_video'?'原生视频':e.provenance==='image'?'图片':e.provenance==='subtitle'?'口播文字稿':e.provider==='cmm'?'蝉妈妈提供的内容':e.provenance==='local_asr'?'本地口播识别':'抽样帧'}{e.end_ms>0?` · ${e.start_ms}–${e.end_ms}ms`:''}</button>)}</details>{!session.local_only&&<ResultActions turn={turn} onBrief={ids=>onBrief(i,ids)} onParse={()=>onParse(i)} onReport={()=>onReport(i)} onReview={()=>onReview(i)}/>}</div></section>)}</div></>}
  </div>
  {pending&&<div className="pending-scope" role="status"><div><b>确认这次分析的素材</b><p>{pending.sourceKinds.length?pending.sourceKinds.map(s=>s==='finished'?'成片':s==='industry'?'行业素材':'灵感素材').join('、'):'保留当前所选素材'}{pending.filters?` · 账户 ${pending.filters.account||'未填写'} · ${pending.filters.startDate||'不限'} 至 ${pending.filters.endDate||'不限'} · 按投放统计日期`:''}{pending.recentDays?` · 近${pending.recentDays}天 · 按发布日期（缺失日期的素材排除）`:''}</p></div><button className="quiet" onClick={onDiscardExample}>沿用当前范围</button><button className="primary" onClick={onApplyExample}>使用这个范围</button></div>}
  {session&&input}
 </>
}
