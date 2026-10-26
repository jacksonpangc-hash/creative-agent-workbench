import {useEffect,useRef,useState} from 'react';
import {post} from '../api';
type Row=Record<string,any>;
type Format='text'|'csv'|'json';
export default function ReferenceImport({base,onImported}:{base:string;onImported:()=>Promise<void>}){
 const [format,setFormat]=useState<Format>('text'),[content,setContent]=useState(''),[preview,setPreview]=useState<Row|null>(null);
 const [busy,setBusy]=useState(false),[ignored,setIgnored]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState('');
 const live=useRef(true),errorBox=useRef<HTMLParagraphElement>(null);
 useEffect(()=>{live.current=true;return()=>{live.current=false}},[]);
 useEffect(()=>{if(error)errorBox.current?.focus()},[error]);
 function change(value:string,nextFormat:Format=format){setContent(value);setFormat(nextFormat);setPreview(null);setIgnored(false);setMessage('');setError('')}
 async function act(fn:()=>Promise<void>){setBusy(true);setError('');try{await fn()}catch(e){if(live.current)setError(e instanceof Error?e.message:'导入未完成，请检查文件后重新预览')}finally{if(live.current)setBusy(false)}}
 const payload={format,content};
 return <section className="reference-import" aria-label="导入链接或表格">
  <h4>导入已有参考</h4>
  <p className="fine">每批最多 200 条。先预览，再保存到当前项目；链接不会自动下载视频。</p>
  <label className="field">导入格式<select value={format} disabled={busy} onChange={e=>change(content,e.target.value as Format)}><option value="text">分享文案 / 链接</option><option value="csv">CSV 表格</option><option value="json">JSON 记录</option></select></label>
  <label className="field">{format==='text'?'分享文案或链接（每行一条）':format==='csv'?'CSV 表格内容':'JSON 记录内容'}<textarea value={content} disabled={busy} maxLength={1_000_000} onChange={e=>change(e.target.value)} placeholder={format==='text'?'粘贴已选作品的抖音、快手分享文案，每行保留一个链接':format==='csv'?'标题,作者,链接,点赞,发布时间,采集时间':'[{"url":"https://www.douyin.com/video/…","title":"标题","author":"作者"}]'}/></label>
  <label className="field">读取 CSV / JSON 文件<input type="file" accept=".csv,.json,text/csv,application/json" disabled={busy} onChange={e=>{const file=e.target.files?.[0];e.target.value='';if(!file)return;void act(async()=>{if(file.size>1_000_000)throw new Error('文件超过 1 MB，请拆分后重新导入');const type=file.name.toLowerCase().endsWith('.csv')?'csv':file.name.toLowerCase().endsWith('.json')?'json':null;if(!type)throw new Error('文件格式不支持，请选择 CSV 或 JSON');const text=await file.text();if(live.current)change(text,type)})}}/></label>
  <details><summary>表头、时间与指标怎么填写</summary><p className="fine">支持中文或英文列名：标题/title、作者/author、链接/url/source_url、发布时间/published_at、采集时间/captured_at、时长/duration_seconds（秒），以及点赞/likes、评论/comments、分享/shares、收藏/favorites、播放/views、粉丝/followers。发布时间可填日期；采集与观察时间需含时区，例如 2026-10-24T09:00:00+08:00。缺少采集时间时记录本次导入时间。</p><p className="fine">指标保留原文和区间，缺失时留空。JSON 可用 metrics 数组注明 raw、as_of、unit、period、scope、nature、lower、upper。文件字段均标为用户导入，不视为平台已核实数据。未支持的列将在预览中列出，须确认忽略；也可修改表头后重新预览。</p></details>
  <button type="button" disabled={busy||!content.trim()} onClick={()=>void act(async()=>{const result=await post<Row>(`${base}/discovery/references/preview`,payload);if(live.current){setPreview(result);setIgnored(false);setMessage('')}})}>{busy?'处理中…':'预览导入'}</button>
  {error&&<p ref={errorBox} tabIndex={-1} role="alert" className="error">{error}</p>}
  {preview&&<section className="reference-preview" aria-label="导入预览">
   <h4>导入预览</h4><p className="fine">共 {preview.rows.length} 条，尚未保存。相同作品合并到原参考，保留已有文件与历史观察。</p>
   {Object.keys(preview.field_mapping||{}).length>0&&<p className="fine">字段对应：{Object.entries(preview.field_mapping).map(([from,to])=>`${from} → ${to}`).join('；')}</p>}
   {(preview.ignored_columns||[]).length>0&&<><p className="fine">未支持的字段将忽略：{preview.ignored_columns.join('、')}。不会将销量列当作互动数据。</p><label className="check-label"><input type="checkbox" checked={ignored} onChange={e=>setIgnored(e.target.checked)} disabled={busy}/>确认忽略以上字段</label></>}
   <ol className="reference-preview-list">{preview.rows.map((row:Row)=><li key={row.row}>
    <strong>第 {row.row} 条 · {row.reference?.title||'标题未提供'}</strong>
    {row.errors?.length>0?<p role="alert" className="error">{row.errors.join('；')}。请修改这条记录后重新预览。</p>:<>
     <p className="fine">{row.reference.platform==='douyin'?'抖音':'快手'} · {row.reference.author||'作者未提供'} · {row.duplicate==='existing'?'更新已有参考':row.duplicate==='batch'?'本批重复，将合并':'新参考'} · {row.reference.media_state==='available'?'已有本地文件':'待添加视频'}</p>
     <p className="fine reference-url">{row.reference.url}</p>
     <p className="fine">发布时间：{row.reference.published_at||'未提供'} · 采集时间：{row.reference.observations?.[0]?.captured_at||'未提供'}</p>
     <p className="fine">{row.reference.observations?.[0]?.metrics?.length?row.reference.observations[0].metrics.map((m:Row)=>`${m.name}：${m.raw}（用户导入）`).join('；'):'互动指标未提供'}</p>
    </>}
   </li>)}</ol>
   {!preview.can_confirm&&<p role="alert" className="error">有记录无法导入，本批尚未保存。请按上方行号修正后重新预览。</p>}
   <div className="actions"><button type="button" disabled={busy||!preview.can_confirm||((preview.ignored_columns||[]).length>0&&!ignored)} onClick={()=>void act(async()=>{const result=await post<Row[]>(`${base}/discovery/references/import`,{...payload,ignore_unknown:ignored});if(!live.current)return;setPreview(null);setContent('');setMessage(`已导入 ${new Set(result.map(r=>r.id)).size} 条参考；相同作品继续使用原记录。`);await onImported()})}>确认导入</button><button type="button" disabled={busy} onClick={()=>setPreview(null)}>取消预览</button></div>
  </section>}
  {message&&<p role="status">{message}</p>}
 </section>;
}
