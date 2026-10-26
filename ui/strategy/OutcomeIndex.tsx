import {useEffect,useState} from 'react';
import {request} from '../api';
type Item=Record<string,any>;
const labels:Record<string,string>={session:'分析',parse:'拆解',script:'脚本',note:'笔记'};
export default function OutcomeIndex({base,onOpen}:{base:string,onOpen:(row:Item)=>void}){
 const [items,setItems]=useState<Item[]>([]),[kind,setKind]=useState(''),[query,setQuery]=useState(''),[error,setError]=useState('');
 useEffect(()=>{let active=true;request(base+'/outcomes').then(r=>{if(active)setItems(r.items||[])}).catch(e=>{if(active)setError(e.message)});return()=>{active=false}},[base]);
 const shown=items.filter(r=>(!kind||r.kind===kind)&&(!query||r.title?.includes(query)));
 return <section className="view-body" aria-label="全部成果"><div className="inline"><label className="field">搜索成果<input value={query} onChange={e=>setQuery(e.target.value)}/></label><label className="field">成果类型<select value={kind} onChange={e=>setKind(e.target.value)}><option value="">全部类型</option>{Object.entries(labels).map(([id,text])=><option key={id} value={id}>{text}</option>)}</select></label></div>{error&&<p role="alert">{error}</p>}<div className="outcome-grid">{shown.map(row=><article className="script-row" key={row.kind+':'+row.id}><h3>{row.title}</h3><p className="fine">{labels[row.kind]} · v{row.version} · {row.created_at?.slice(0,10)||'历史成果'}</p><button className="quiet bordered" aria-label={'打开'+row.title} onClick={()=>onOpen(row)}>打开{labels[row.kind]}</button></article>)}</div>{!shown.length&&!error&&<p className="fine">还没有符合条件的成果，保存后会在这里找到。</p>}</section>
}
