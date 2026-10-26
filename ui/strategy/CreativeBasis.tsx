import {useEffect,useState} from 'react';
import {request,post} from '../api';
type Item=Record<string,any>;
export default function CreativeBasis({base,snapshotId,snapshot,onOpen}:{base?:string,snapshotId?:string,snapshot?:Item,onOpen?:(ref:Item)=>void}){
 const [data,setData]=useState<Item|undefined>(snapshot),[message,setMessage]=useState('');
 useEffect(()=>{let active=true;setData(snapshot);setMessage('');if(!snapshot&&base&&snapshotId)request(base+'/contexts/'+encodeURIComponent(snapshotId)).then(d=>{if(active)setData(d)}).catch(e=>{if(active)setMessage(e.message)});return()=>{active=false}},[base,snapshotId,snapshot]);
 if(!snapshotId&&!snapshot)return <p className="fine">旧版依据保留在原创作记录中。</p>;
 if(!data)return <p role="status" className="fine">{message||'正在读取本稿参考资料…'}</p>;
 const refs=data.reference_patterns||[],own=data.own_footage||[],facts=data.product_evidence||[];
 const open=(r:Item)=>{const time=r.start_ms??r.support?.find((v:Item)=>typeof v.time_ms==='number')?.time_ms??0;onOpen?.({asset_id:r.asset_id,start_ms:time,end_ms:r.end_ms>time?r.end_ms:time+1,observation:r.statement||r.text||'',provenance:r.provenance||'sampled_frames'})};
 return <details className="creative-basis"><summary>查看创作依据 · {new Set(refs.map((r:Item)=>r.source_record_id)).size} 条参考 · {own.length} 段已有镜头</summary>
  {data.archived_restored&&<p role="status">这是备份中的历史依据。可回看和手动改稿；再次交给 AI 创作前，需要重新查找当前可用资料。</p>}
  {!refs.length&&<p>暂未找到适合这次的历史参考。这份草稿根据商品资料和拍摄要求提出。</p>}
  {!!facts.length&&<section><h4>商品依据</h4>{facts.map((f:Item)=><p key={f.id}>{f.statement}{f.valid_until?` · 有效至 ${f.valid_until}`:''}</p>)}</section>}
  {!!refs.length&&<section><h4>表达参考</h4>{refs.map((r:Item)=><article key={r.id}><b>{r.title||'参考视频'} · {r.type}</b><p>{r.suitable_reason}</p><p>{r.statement||r.text}</p>{r.asset_id&&onOpen&&<button type="button" className="text-button" onClick={()=>open(r)}>回看参考{r.start_ms!=null?` · ${(r.start_ms/1000).toFixed(1)}秒`:''}</button>}{r.source_url&&!r.asset_id&&<a href={r.source_url} target="_blank" rel="noreferrer">查看原页面</a>}<p className="fine">{r.content_basis} · 表现数据：{typeof r.performance_basis==='string'?r.performance_basis:'见来源记录，口径分别保留'}</p><p className="fine">{r.needs_adjustment?.join('；')}</p>{base&&<button type="button" className="quiet" onClick={async()=>{try{await post(base+'/knowledge/exclude',{source_kind:r.source_kind,source_record_id:r.source_record_id});setMessage('已从以后的创作中排除；本稿历史记录仍保留。')}catch(e){setMessage((e as Error).message)}}}>以后不再用这条参考</button>}</article>)}</section>}
  {!!own.length&&<section><h4>已有镜头</h4>{own.map((r:Item)=><p key={r.id}><button type="button" className="text-button" onClick={()=>open(r)} disabled={!onOpen}>{r.title} · {r.start_ms==null?'整图':`${(r.start_ms/1000).toFixed(1)}–${(r.end_ms/1000).toFixed(1)}秒`}</button></p>)}</section>}
  {!!data.exclusions?.length&&<details><summary>未选入的资料</summary>{data.exclusions.slice(0,15).map((x:Item,i:number)=><p className="fine" key={i}>{x.reason}</p>)}</details>}
  {message&&<p role="status">{message}</p>}
 </details>;
}
