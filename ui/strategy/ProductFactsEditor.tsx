import {useEffect,useRef,useState} from 'react';
import {post,request} from '../api';
import './ProductFactsEditor.css';
type Item=Record<string,any>;
const initialFacts=()=>({version:0,product:'',product_id:'',sku_id:'',audience:'',claims:[],constraints:[],price_and_promotion:'',promotion_valid_until:null});
const categoryPrefix='商品类别：',specPrefix='商品规格：';
const note=(facts:Item,prefix:string)=>facts.constraints.find((value:string)=>value.startsWith(prefix))?.slice(prefix.length)||'';
const updateNote=(facts:Item,prefix:string,value:string)=>({...facts,constraints:[...facts.constraints.filter((line:string)=>!line.startsWith(prefix)),...(value.trim()?[prefix+value.trim()]:[])]});
function pastedDraft(facts:Item,text:string){
 let next:Item={...facts,claims:[...facts.claims],constraints:[...facts.constraints]};
 const statements:string[]=[];
 for(const raw of text.split('\n').map(value=>value.trim()).filter(Boolean)){
  const pair=raw.match(/^([^：:]{1,12})[：:]\s*(.+)$/);if(!pair){statements.push(raw);continue}
  const [,key,value]=pair;
  if(['商品名称','产品名称','商品','品名','名称'].includes(key))next.product=value;
  else if(['类别','分类','商品类别'].includes(key))next=updateNote(next,categoryPrefix,value);
  else if(['规格','净含量','款式','商品规格'].includes(key)){if(note(next,specPrefix)!==value)next.sku_id='sku-'+crypto.randomUUID();next=updateNote(next,specPrefix,value)}
  else if(['适合人群','面向人群','适用人群','人群','受众'].includes(key))next.audience=value;
  else if(['价格','当前价格','促销','优惠','价格与促销'].includes(key))next.price_and_promotion=value;
  else if(['限制','拍摄条件','拍摄限制','创作限制'].includes(key))next.constraints.push(value);
  else statements.push(['卖点','特点','功能'].includes(key)?value:raw);
 }
 const source='用户提供的商品介绍：\n'+text;
 for(const statement of statements)next.claims.push({fact_id:crypto.randomUUID(),statement,source,confirmed:false,valid_until:null});
 return next;
}
export default function ProductFactsEditor({base}:{base:string}){
 const scope=useRef({base}),mounted=useRef(true);if(scope.current.base!==base)scope.current={base};
 const [facts,setFacts]=useState<Item>(initialFacts),[message,setMessage]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[loading,setLoading]=useState(true),[description,setDescription]=useState('');
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
 useEffect(()=>{let active=true;const at=scope.current;setFacts(initialFacts());setDescription('');setMessage('');setError('');setBusy(false);setLoading(true);request(base+'/product-facts').then(f=>{if(active&&scope.current===at)setFacts({...initialFacts(),...f})}).catch(e=>{if(active&&scope.current===at)setError(e.message)}).finally(()=>{if(active&&scope.current===at)setLoading(false)});return()=>{active=false}},[base]);
 const claim=(i:number,patch:Item)=>setFacts(f=>({...f,claims:f.claims.map((c:Item,n:number)=>n===i?{...c,...patch,...('confirmed' in patch?{}:{confirmed:false})}:c)}));
 const change=(patch:Item)=>{setFacts(f=>({...f,...patch}));setMessage('')};
 const changeSpec=(value:string)=>{setFacts(f=>({...updateNote(f,specPrefix,value),sku_id:note(f,specPrefix)===value?f.sku_id:value.trim()?'sku-'+crypto.randomUUID():''}));setMessage('')};
 const draft=()=>{setFacts(f=>pastedDraft(f,description.trim()));setMessage('已整理成可编辑草稿。请检查商品信息；卖点需补充说明并确认。');setDescription('')};
 const save=async()=>{const at=scope.current;const current=()=>mounted.current&&scope.current===at;setBusy(true);setMessage('');setError('');try{const {version,id,project_id,created_at,updated_at,revisions,...content}=facts;const next=await post(base+'/product-facts',{...content,claims:content.claims.filter((c:Item)=>c.statement.trim()),expected_version:version},'PUT');if(current()){setFacts({...initialFacts(),...next});setMessage('商品资料已保存，后续创作会使用这一版本。')}}catch(e){if(current())setError((e as Error).message)}finally{if(current())setBusy(false)}};
 return <section className="product-facts" aria-label="商品资料">
  <p className="muted">粘贴商品介绍，或直接填写本次使用的信息。未提供的规格和价格可以留空。</p>
  <details className="product-description" open={!facts.product}><summary>粘贴或导入商品介绍</summary><label className="field">商品介绍<textarea aria-label="商品介绍" value={description} maxLength={4800} disabled={loading||busy} onChange={e=>setDescription(e.target.value)} placeholder="把商品介绍贴在这里，也可以上传资料。"/></label><div className="inline"><button type="button" className="quiet bordered" disabled={loading||busy||!description.trim()} onClick={draft}>整理成草稿</button><label className="product-document-button">导入文字资料<input aria-label="导入商品文字资料" type="file" accept=".txt,.md,text/plain,text/markdown" disabled={busy||loading} onChange={e=>{const file=e.target.files?.[0];e.target.value='';if(!file)return;const at=scope.current;void file.text().then(text=>{if(scope.current!==at||!mounted.current)return;if(text.length>4800){setError('资料超过 4800 字，请分段粘贴后整理。');return}setDescription(text);setMessage('资料已读入，整理后再确认保存。')}).catch(()=>{if(scope.current===at)setError('无法读取这份资料，请复制文字粘贴。')})}}/></label></div><p className="fine">按你提供的字段整理，不调用模型。没有明确字段的文字保留为待确认说法。</p></details>
  {loading&&<p role="status">正在读取商品资料…</p>}{message&&<p role="status">{message}</p>}{error&&<p role="alert">{error}</p>}
  <div className="form-grid"><label className="field">商品名称<input aria-label="商品名称" disabled={loading||busy} value={facts.product||''} onChange={e=>change({product:e.target.value})}/></label><label className="field">商品类别<input aria-label="商品类别" disabled={loading||busy} value={note(facts,categoryPrefix)} onChange={e=>{setFacts(f=>updateNote(f,categoryPrefix,e.target.value));setMessage('')}} placeholder="未提供"/></label><label className="field">面向什么人<input aria-label="面向什么人" disabled={loading||busy} value={facts.audience||''} onChange={e=>change({audience:e.target.value})}/></label><label className="field">本次使用规格<input aria-label="商品规格" disabled={loading||busy} value={note(facts,specPrefix)} onChange={e=>changeSpec(e.target.value)} placeholder={facts.sku_id?'当前规格说明未提供':'未提供'}/></label></div>
  <h3>卖点</h3>{!facts.claims.length&&<p className="fine">还没有填写卖点，可以先保存商品。</p>}
  {facts.claims.map((c:Item,i:number)=><fieldset className="claim-row" key={c.fact_id||i}><legend>卖点 {i+1}</legend><label className="field">卖点内容<input aria-label={`卖点内容 ${i+1}`} value={c.statement} disabled={busy} onChange={e=>claim(i,{statement:e.target.value})}/></label>{!c.confirmed&&<p className="claim-unconfirmed">这条卖点还没有确认，补充资料后再使用。</p>}<details><summary>{c.confirmed?'查看已确认的说明':'补充说明'}</summary><label className="field">依据来源<textarea aria-label={`卖点依据 ${i+1}`} value={c.source} disabled={busy} onChange={e=>claim(i,{source:e.target.value})}/></label><div className="inline"><label className="field">有效期<input type="date" aria-label={`卖点有效期 ${i+1}`} value={c.valid_until?.slice(0,10)||''} disabled={busy} onChange={e=>claim(i,{valid_until:e.target.value||null})}/></label><label className="check-label"><input type="checkbox" checked={!!c.confirmed} disabled={busy||!c.source?.trim()} onChange={e=>claim(i,{confirmed:e.target.checked})}/>已核对这条卖点和依据</label></div><p className="fine">修改说法、依据或有效期后，需要重新确认。</p></details><button type="button" className="quiet" disabled={busy} onClick={()=>setFacts({...facts,claims:facts.claims.filter((_:Item,n:number)=>n!==i)})}>移除此卖点</button></fieldset>)}
  <button type="button" className="quiet bordered" disabled={busy||loading} onClick={()=>setFacts({...facts,claims:[...facts.claims,{fact_id:crypto.randomUUID(),statement:'',source:'',confirmed:false,valid_until:null}]})}>添加卖点</button>
  <div className="form-grid"><label className="field">价格与促销说明<textarea aria-label="价格与促销说明" disabled={busy||loading} value={facts.price_and_promotion||''} onChange={e=>change({price_and_promotion:e.target.value})} placeholder="未提供"/></label><label className="field">促销截止日期<input type="date" disabled={busy||loading} value={facts.promotion_valid_until?.slice(0,10)||''} onChange={e=>change({promotion_valid_until:e.target.value||null})}/></label></div>
  <label className="field">本次拍摄条件和限制<textarea aria-label="本次拍摄条件和限制" disabled={busy||loading} value={facts.constraints.filter((line:string)=>!line.startsWith(categoryPrefix)&&!line.startsWith(specPrefix)).join('\n')} onChange={e=>change({constraints:[...facts.constraints.filter((line:string)=>line.startsWith(categoryPrefix)||line.startsWith(specPrefix)),...e.target.value.split('\n').filter(Boolean)]})} placeholder="例如：只能室内拍摄；不提及未确认的功效"/></label>
  <button type="button" className="primary" disabled={busy||loading||!facts.product.trim()} onClick={()=>void save()}>{busy?'正在保存…':'确认并保存商品'}</button>
 </section>;
}
