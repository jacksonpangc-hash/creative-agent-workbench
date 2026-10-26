import {useEffect,useRef,useState} from 'react';
import {Download} from 'lucide-react';
import {request,teamMode} from '../api';
import ModelServiceSettings from './ModelServiceSettings';
import AnalysisPreferences from './AnalysisPreferences';
import LicenseSettings from './LicenseSettings';
import BrowserConnection from './BrowserConnection';
import {ProjectBackup,BillReconciliation} from './LocalDataSettings';
import {CmmSettings} from '../industry/CmmWorkspace';

type Item=Record<string,any>;
export type SettingsSection='models'|'references'|'usage'|'account'|'local';
type Props={base:string,data:{project:Item,budget:Item,models:Item[],jobs?:Item[]},catalog:Item[],run:(fn:()=>Promise<void>)=>unknown,reload:()=>Promise<unknown>,onPreferences:(p:Item)=>void,initialSection?:SettingsSection,onJobs?:()=>void};
const sections:[SettingsSection,string][]=[['models','模型连接'],['references','参考来源'],['usage','用量与费用'],['account','账号'],['local','本地资料']];

export default function SettingsWorkspace(props:Props){return <ProjectSettings key={props.base} {...props}/>}
function ProjectSettings({base,data,catalog,run,reload,onPreferences,initialSection='models',onJobs}:Props){
 const [section,setSection]=useState<SettingsSection>(initialSection);
 const [budget,setBudget]=useState(data.project.budget??'');
 const [budgetEdited,setBudgetEdited]=useState(false);
 const budgetEditRevision=useRef(0);
 useEffect(()=>{if(!budgetEdited)setBudget(data.project.budget??'')},[data.project.budget,budgetEdited]);
 const [message,setMessage]=useState('');
 useEffect(()=>setSection(initialSection),[initialSection]);
 const currency=data.project.currency||'CNY';
 const amount=(value:unknown)=>value==null?'未确认':`${value} ${currency}`;
 return <div className="settings-workspace">
  <nav className="settings-navigation" aria-label="设置分区">{sections.map(([id,label])=><button key={id} aria-current={section===id?'page':undefined} aria-controls="settings-content" onClick={()=>{setSection(id);setMessage('')}}>{label}</button>)}</nav>
  <div id="settings-content" className="settings-content" role="region" aria-label={sections.find(([id])=>id===section)?.[1]+'设置'} tabIndex={0}>
   {section==='models'&&<><ModelServiceSettings base={base} models={data.models} catalog={catalog} run={run} reload={reload}/><AnalysisPreferences base={base} models={data.models} currency={currency} onSaved={onPreferences}/></>}
   {section==='references'&&<><h3>参考来源</h3><p className="muted">使用自己的收藏、平台网页和已连接的数据服务。</p><BrowserConnection base={base}/><details><summary>导入链接或表格</summary><p className="fine">在“找参考”面板选择“添加参考”，可粘贴分享链接或导入 CSV、JSON。先预览，再保存到当前项目。</p></details><CmmSettings base={base}/></>}
   {section==='usage'&&<><h3>用量与费用</h3>{onJobs&&<button className="quiet bordered" onClick={onJobs}>查看处理记录</button>}<p className="muted">模型费用与蝉妈妈积分分别记账。待确认的费用以服务商账单为准。</p><section className="panel inset"><h3>费用提醒（可选）</h3><p className="fine">无需设置即可开始创作。已有金额会继续保留；清空并保存可关闭本地金额限制。</p><label className="field"><span>费用上限 · {currency}</span><input aria-label="费用上限" value={budget} onChange={e=>{setBudget(e.target.value);budgetEditRevision.current++;setBudgetEdited(true);setMessage('')}} inputMode="decimal"/></label><button className="primary" onClick={()=>run(async()=>{const submittedRevision=budgetEditRevision.current;if(teamMode&&!window.confirm(`确认将项目费用上限修改为 ${budget||'未设置'} ${currency}？`))return;await request(base,{method:'PUT',headers:{'Content-Type':'application/json',...(teamMode?{'X-Confirm-Budget-Change':'true'}:{})},body:JSON.stringify({name:data.project.name,budget:budget.trim()||null,currency:data.project.currency})});await reload();if(budgetEditRevision.current===submittedRevision){setBudgetEdited(false);setMessage('费用上限已保存')}else setMessage('此前提交的费用上限已保存，当前修改尚未保存')})}>保存费用提醒</button><p className="fine">已知计价时按此金额检查，未知计价会单独标记。这不是服务商的实际扣费上限。</p><dl className="settings-usage"><div><dt>待确认占用</dt><dd>{amount(data.budget.reserved)}</dd></div><div><dt>已确认费用</dt><dd>{amount(data.budget.spent)}</dd></div><div><dt>费用未知的任务</dt><dd>{data.budget.unknown_jobs||0} 笔</dd></div></dl>{data.budget.reserved!=null&&Number(data.budget.reserved)>0&&<p className="fine">有一笔费用还在确认中，可用额度暂未恢复。</p>}</section>{Object.entries(data.budget.by_currency||{}).filter(([unit])=>unit!==currency).map(([unit,raw])=>{const totals=raw as Item;return <p className="fine" key={unit}>{unit}：已确认 {totals.spent} · 待确认占用 {totals.reserved} · 费用未知 {totals.unknown_jobs} 笔</p>})}<BillReconciliation base={base} jobs={data.jobs||[]} currency={currency} onSaved={reload}/><CmmSettings base={base} view="usage"/></>}
   {section==='account'&&<LicenseSettings/>}
   {section==='local'&&<><ProjectBackup base={base}/><button className="primary" onClick={()=>run(async()=>{const exported=await request(base+'/export');const url=URL.createObjectURL(new Blob([JSON.stringify(exported,null,2)],{type:'application/json'}));try{const a=document.createElement('a');a.href=url;a.download='project.json';a.click();setMessage('创作记录已导出')}finally{URL.revokeObjectURL(url)}})}><Download size={16}/>导出创作记录</button><p className="fine">不包含视频文件和密钥。</p></>}
   {message&&<p role="status">{message}</p>}
  </div>
 </div>
}
