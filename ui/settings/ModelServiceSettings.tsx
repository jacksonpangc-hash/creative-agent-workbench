import {useState} from 'react';
import {Check,Plus,Trash2} from 'lucide-react';
import {post,request} from '../api';
import './Connections.css';
type Item=Record<string,any>;
type Props={base:string,models:Item[],catalog:Item[],run:(fn:()=>Promise<void>)=>unknown,reload:()=>Promise<unknown>};
type Draft={id:string;provider:string;model_id:string;label:string;api_key:string;base_url:string;role:string;max_output_tokens:number;currency:string;price_input_million:string;price_output_million:string;price_video_second:string;unpriced_task_limit:string;region:string;protocol:string;enabled:boolean};
const emptyDraft=():Draft=>({id:'',provider:'',model_id:'',label:'',api_key:'',base_url:'',role:'strategy',max_output_tokens:4096,currency:'CNY',price_input_million:'',price_output_million:'',price_video_second:'',unpriced_task_limit:'',region:'',protocol:'',enabled:true});
const analysisAvailable=(m:Item)=>m.availability?.strategy?.available||m.availability?.video_analysis?.available||m.availability?.image_input?.available;
function capabilities(model?:Item){if(!model)return '型号能力未确认';return [model.availability?.strategy?.available?'写脚本与分析文字':null,model.availability?.video_analysis?.available?'可分析画面':null,model.availability?.embedding?.available?'可整理文字检索':null].filter(Boolean).join(' · ')||'当前型号的能力待确认'}
export default function ModelServiceSettings(props:Props){return <ScopedModelServiceSettings key={props.base} {...props}/>}
function ScopedModelServiceSettings({base,models,catalog,run,reload}:Props){
 const [draft,setDraft]=useState<Draft|null>(null),[replaceKey,setReplaceKey]=useState(false),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
 const embedding=draft?.role==='embedding';
 const eligible=(m:Item)=>embedding?m.availability?.embedding?.available:analysisAvailable(m);
 const suppliers=catalog.filter(c=>c.models?.some(eligible));
 const selected=suppliers.find(c=>c.id===draft?.provider)||(!draft?.provider?suppliers[0]:undefined);
 const options=(selected?.models||[]).filter(eligible);
 const profile=options.find((m:Item)=>m.id===draft?.model_id)||(!draft?.model_id?options[0]:undefined);
 const existing=models.find(m=>m.id===draft?.id);
 const savedKey=!!existing?.has_key&&!replaceKey;
 const change=(patch:Partial<Draft>)=>setDraft(value=>value?{...value,...patch}:value);
 const clear=()=>{setDraft(null);setReplaceKey(false)};
 const add=(role='strategy')=>{setDraft({...emptyDraft(),role});setReplaceKey(false);setMessage('')};
 const edit=(model:Item)=>{setDraft({...emptyDraft(),...Object.fromEntries(Object.keys(emptyDraft()).filter(k=>model[k]!=null).map(k=>[k,model[k]])),api_key:''});setReplaceKey(false);setMessage('')};
 async function save(){if(!draft||!profile||!selected)return;setBusy(true);try{await post(base+'/models',{...draft,provider:selected.id,model_id:profile.id,label:draft.label.trim()||profile.name,role:embedding?'embedding':'strategy',price_input_million:draft.price_input_million===''?null:draft.price_input_million,price_output_million:draft.price_output_million===''?null:draft.price_output_million,price_video_second:draft.price_video_second===''?null:draft.price_video_second,unpriced_task_limit:draft.unpriced_task_limit===''?null:draft.unpriced_task_limit});clear();await reload();setMessage('模型已保存，首次使用时检查连接。符合条件时会作为本项目默认服务。')}finally{setBusy(false)}}
 return <section className="model-services" aria-label="模型服务">
  <div className="setting-heading"><h3>模型连接</h3><p className="muted">使用你自己的模型账号，费用由对应服务商收取。</p></div>
  <div className="model-list">{models.filter(m=>!['video_generation','image_generation'].includes(m.role)).map(m=><div className="model-row" key={m.id}><div><b>{m.label}</b><small>{catalog.find(c=>c.id===m.provider)?.name||m.provider} · {m.model_id}</small><small>{m.has_key?(Object.keys(m.recent_use||{}).length?`最近成功使用：${new Date(Object.values(m.recent_use)[0]&&(Object.values(m.recent_use)[0] as Item).succeeded_at).toLocaleString()}`:'已保存，首次使用时检查连接'):'尚未保存密钥'}{m.enabled===false?' · 已停用':''}</small><small>{capabilities(m)}</small></div><button type="button" className="quiet bordered" onClick={()=>edit(m)}>更换</button><button type="button" className="icon" aria-label={`删除模型${m.label}`} onClick={()=>run(async()=>{await request(base+`/models/${encodeURIComponent(m.id)}`,{method:'DELETE'});if(draft?.id===m.id)clear();await reload()})}><Trash2 size={15}/><span>移除</span></button></div>)}</div>
  {!models.some(m=>!['video_generation','image_generation'].includes(m.role))&&!draft&&<p className="muted">连接一个模型服务后，就能分析视频和写脚本。</p>}
  {!draft&&<button type="button" className="primary" onClick={()=>add()}><Plus size={16}/>添加模型服务</button>}
  {draft&&<form className="model-connection-form" aria-label={draft.id?'更换模型服务':'添加模型服务'} onSubmit={e=>{e.preventDefault();void run(save)}}>
   <h4>{draft.id?'更换模型服务':embedding?'连接搜索增强模型':'添加模型服务'}</h4>
   <div className="form-grid"><label className="field"><span>服务商</span><select aria-label="服务商" value={selected?.id||draft.provider} disabled={!!draft.id} onChange={e=>change({provider:e.target.value,model_id:'',api_key:'',base_url:'',protocol:''})}>{!selected&&draft.provider&&<option value={draft.provider}>原服务商（当前不可用）</option>}{!suppliers.length&&<option value="">暂无可用服务</option>}{suppliers.map(c=><option key={c.id} value={c.id}>{c.name}{c.country?` · ${c.country}`:''}</option>)}</select></label><label className="field"><span>模型</span><select aria-label="模型" value={profile?.id||draft.model_id} onChange={e=>change({model_id:e.target.value})}>{!profile&&draft.model_id&&<option value={draft.model_id}>原型号（当前不可用）</option>}{!options.length&&<option value="">暂无可用型号</option>}{options.map((m:Item)=><option key={m.id} value={m.id}>{m.name}</option>)}</select></label></div>
   <p className="model-capabilities">{capabilities(profile)}</p>
   {profile?.availability?.video_analysis?.available&&<p className="fine">{profile.availability.video_analysis.route==='native_video'?'支持直接提交视频，声音覆盖取决于具体型号。':'可分析抽取的画面；台词是否可用在素材详情中单独显示。'}</p>}
   {savedKey?<div className="inline saved-credential"><span>密钥已保存</span><button type="button" className="quiet bordered" onClick={()=>setReplaceKey(true)}>更换 API Key</button></div>:<label className="field"><span>API Key</span><input aria-label="API 密钥 · 保存到系统密钥库" type="password" autoComplete="new-password" value={draft.api_key} onChange={e=>change({api_key:e.target.value})} placeholder="粘贴该服务商的 API Key" required={!existing?.has_key}/></label>}
   <details className="connection-settings"><summary>高级连接与模型参数</summary><label className="field"><span>显示名称</span><input value={draft.label} onChange={e=>change({label:e.target.value})} placeholder={profile?.name}/></label><label className="field"><span>接口基础地址</span><input value={draft.base_url} onChange={e=>change({base_url:e.target.value})} placeholder="留空使用官方地址"/></label><label className="field"><span>最大输出 Token</span><input aria-label="最大输出 Token" type="number" min={256} max={32768} value={draft.max_output_tokens} onChange={e=>change({max_output_tokens:Number(e.target.value)})}/></label><div className="form-grid">{[['price_input_million','每百万输入 Token 价格'],['price_output_million','每百万输出 Token 价格'],['price_video_second','每秒视频价格']].map(([key,label])=><label className="field" key={key}>{label}<input inputMode="decimal" value={String(draft[key as keyof Draft])} onChange={e=>change({[key]:e.target.value})}/></label>)}<label className="field">计价币种<select value={draft.currency} onChange={e=>change({currency:e.target.value})}><option value="CNY">CNY</option><option value="USD">USD</option></select></label></div><label className="field">可选：沿用此前的单任务提醒金额<input aria-label="单价未知时单任务费用上限" inputMode="decimal" value={draft.unpriced_task_limit} onChange={e=>change({unpriced_task_limit:e.target.value})}/></label><p className="fine">无需填写即可开始。这里的金额用于本地提醒，不保证服务商的实际扣费上限。</p><p className="fine">定价未知时留空。实际可用能力与费用以具体账号、型号和服务商账单为准。</p><label className="check-label"><input type="checkbox" checked={draft.enabled} onChange={e=>change({enabled:e.target.checked})}/>启用此连接</label></details>
   <p className="fine">密钥仅保存到系统密钥库。保存不会调用模型验证；执行时会将所选资料发给该服务。</p>
   <div className="inline"><button type="submit" className="primary" disabled={busy||!profile||(!savedKey&&!draft.api_key.trim())}><Check size={15}/>保存模型设置</button><button type="button" className="quiet" onClick={clear}>取消编辑</button></div>
  </form>}
  <details className="advanced-services"><summary>本地资料查找</summary><p className="fine">默认在本机整理和查找资料，无需额外模型 Key。远程向量服务仅作高级选项。</p><button type="button" className="quiet bordered" onClick={()=>add('embedding')}>添加搜索增强模型</button></details>
  {message&&<p role="status">{message}</p>}
 </section>;
}
