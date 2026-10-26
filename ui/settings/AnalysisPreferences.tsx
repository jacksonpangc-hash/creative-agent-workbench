import {useEffect,useRef,useState} from 'react';
import {post,request} from '../api';
type Item=Record<string,any>;
const initialPreferences=()=>({default_binding_id:'',allow_external_references:false,version:0});
export default function AnalysisPreferences({base,models,currency,onSaved}:{base:string,models:Item[],currency:string,onSaved:(p:Item)=>void}){
 const scope=useRef({base}),mounted=useRef(true);if(scope.current.base!==base)scope.current={base};
 const [prefs,setPrefs]=useState<Item>(initialPreferences),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
 useEffect(()=>{let active=true;const at=scope.current;setPrefs(initialPreferences());setMessage('');setBusy(false);request(base+'/preferences').then(p=>{if(active&&scope.current===at)setPrefs({...initialPreferences(),...p})}).catch(e=>{if(active&&scope.current===at)setMessage(e.message)});return()=>{active=false}},[base]);
 const save=async()=>{const at=scope.current;const current=()=>mounted.current&&scope.current===at;setBusy(true);setMessage('');try{const next=await post(base+'/preferences',{default_binding_id:prefs.default_binding_id,allow_external_references:prefs.allow_external_references,expected_version:prefs.version||0},'PUT');if(current()){setPrefs({...initialPreferences(),...next});onSaved(next);setMessage('创作偏好已保存')}}catch(e){if(current())setMessage((e as Error).message)}finally{if(current())setBusy(false)}};
 const choices=models.filter(m=>['strategy','video_input','image_input'].includes(m.role));
 return <section className="analysis-preferences"><h3>默认使用的服务</h3><p className="muted">在当前项目中沿用所选服务。能力不足时提示你选择，不自动切换收费账号。</p><label className="field">默认分析模型<select aria-label="默认分析模型" value={prefs.default_binding_id} onChange={e=>setPrefs({...prefs,default_binding_id:e.target.value})}><option value="">执行前选择服务</option>{prefs.default_binding_id&&!choices.some(m=>m.id===prefs.default_binding_id)&&<option value={prefs.default_binding_id}>此前选择的服务（当前不可用）</option>}{choices.map(m=><option key={m.id} value={m.id}>{m.label}</option>)}</select></label><label className="check-label"><input type="checkbox" checked={!!prefs.allow_external_references} onChange={e=>setPrefs({...prefs,allow_external_references:e.target.checked})}/>本地参考不够时，允许补充已连接的数据来源</label><p className="fine">仅使用已经确认的权限和积分上限。普通改稿不新增查询；没有可用额度时继续使用本地资料。</p><button className="primary" disabled={busy} onClick={save}>保存偏好</button>{message&&<p role="status">{message}</p>}</section>
}
