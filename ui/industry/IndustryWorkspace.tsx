import {useEffect, useRef, useState} from 'react';

type Item = Record<string, any>;
type Api = <T = any>(path: string, options?: RequestInit) => Promise<T>;
type Props = {project: string; api: Api; onChanged?: () => void};
const rightsLabels = [
  ['search', '查询来源'], ['retain_metadata', '留存数据'], ['download_media', '下载媒体'],
  ['retain_media', '留存媒体'], ['model_process', '模型处理'], ['derivative_create', '衍生创作'],
  ['export_metadata', '导出数据'], ['export_media', '导出媒体'],
] as const;
const blankRights = () => Object.fromEntries([...rightsLabels.map(([key]) => [key, false]), ['expires_at', ''], ['evidence', '']]) as Item;
const today = () => new Date().toISOString().slice(0, 10);
const monthAgo = () => new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);
const dateInput = (value: string) => value ? new Date(value).toLocaleString('sv-SE').replace(' ', 'T').slice(0, 16) : '';
const isoDate = (value: string) => {
  if (!value || Number.isNaN(new Date(value).getTime())) throw new Error('请填写有效的授权到期时间');
  return new Date(value).toISOString();
};
const validRights = (rights: Item | undefined, keys: string[]) => !!rights && new Date(rights.expires_at).getTime() > Date.now() && keys.every(key => rights[key] === true);
const statusLabels: Item = {queued: '已排队', running: '查询中', succeeded: '查询完成', failed: '查询失败', needs_attention: '需要核对', cancelled: '已取消'};
const metricLabels: Item = {likes: '点赞', comments: '评论', shares: '分享', views: '播放', sales: '销量', revenue: '销售额', audience: '覆盖人数', index: '热度指数'};
const metricText = (metric: Item) => {
  if (metric.kind === 'missing') return '缺失';
  const value = metric.kind === 'range' ? `${metric.lower}–${metric.upper}` : metric.value;
  return `${metric.kind === 'estimated' ? '估算 ' : metric.kind === 'index' ? '指数 ' : ''}${value ?? '缺失'}${metric.unit ? ' ' + metric.unit : ''}`;
};

function RightsFields({prefix, value, onChange}: {prefix: string; value: Item; onChange: (value: Item) => void}) {
  return <fieldset className="script-row"><legend>{prefix}的内容使用授权</legend>
    <div className="form-grid">{rightsLabels.map(([key, label]) => <label className="check-label" key={key}>
      <input type="checkbox" aria-label={`${prefix}：${label}`} checked={!!value[key]} onChange={e => onChange({...value, [key]: e.target.checked})}/>{label}
    </label>)}</div>
    <div className="form-grid"><label className="field">授权到期时间<input aria-label={`${prefix}授权到期时间`} type="datetime-local" value={dateInput(value.expires_at)} onChange={e => onChange({...value, expires_at: e.target.value})}/></label>
      <label className="field">授权依据<textarea aria-label={`${prefix}授权依据`} value={value.evidence} onChange={e => onChange({...value, evidence: e.target.value})} placeholder="合同条款、权利人许可及适用范围"/></label></div>
    <p className="fine">API 会员或可调用权限不能代替下载、留存、模型处理与衍生授权。</p>
  </fieldset>;
}

export default function IndustryWorkspace({project, api, onChanged}: Props) {
  const base = `/projects/${encodeURIComponent(project)}/industry`;
  const scope = useRef(project); scope.current = project;
  const [overview, setOverview] = useState<Item>({catalog: [], connections: [], search_jobs: [], references: []});
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [message, setMessage] = useState('');
  const [source, setSource] = useState({operation: '', label: '', api_key: '', api_authorized: false, authorization_evidence: '', mapping: ''});
  const [newRights, setNewRights] = useState<Item>(blankRights);
  const [rightsId, setRightsId] = useState(''), [editedRights, setEditedRights] = useState<Item>(blankRights);
  const [query, setQuery] = useState<Item>({connection_id: '', query: '', category: '', start_date: monthAgo(), end_date: today(), target_count: 20, max_calls: 1, quote: '', currency: 'USD', allowed_actions: ['search', 'retain_metadata']});
  const [pending, setPending] = useState<Item | null>(null), [approved, setApproved] = useState(false);
  const [job, setJob] = useState<Item | null>(null), [results, setResults] = useState<Item[]>([]), [selected, setSelected] = useState<string[]>([]);
  const [reason, setReason] = useState(''), [importApproved, setImportApproved] = useState(false);
  const importAttempt = useRef({fingerprint: '', key: ''});
  const connections: Item[] = overview.connections;
  const activeConnection = connections.find(c => c.id === query.connection_id);
  const selectedOperation = overview.catalog.find((c: Item) => `${c.provider}:${c.operation}` === source.operation);
  const changeQuery = (patch: Item) => {setQuery(q => ({...q, ...patch})); setPending(null); setApproved(false)};
  const load = async () => {
    const data = await api<Item>(base);
    if (scope.current !== project) return;
    setOverview(data);
    const refreshed = data.search_jobs.find((value: Item) => value.id === job?.id);
    if (refreshed) {
      setJob(refreshed);
      if (refreshed.content_restricted) {setResults([]); setSelected([]); setImportApproved(false)}
    }
    setSource(s => ({...s, operation: s.operation || (data.catalog[0] ? `${data.catalog[0].provider}:${data.catalog[0].operation}` : '')}));
    setQuery(q => ({...q, connection_id: data.connections.some((c: Item) => c.id === q.connection_id) ? q.connection_id : data.connections.find((c: Item) => c.search_enabled)?.id || ''}));
    setRightsId(id => id || data.connections[0]?.id || '');
  };
  useEffect(() => {
    setOverview({catalog: [], connections: [], search_jobs: [], references: []}); setJob(null); setResults([]); setSelected([]);
    setSource({operation: '', label: '', api_key: '', api_authorized: false, authorization_evidence: '', mapping: ''}); setNewRights(blankRights());
    setRightsId(''); setEditedRights(blankRights()); setPending(null); setApproved(false); setError(''); setMessage(''); setReason(''); setImportApproved(false); setBusy(false);
    setQuery({connection_id: '', query: '', category: '', start_date: monthAgo(), end_date: today(), target_count: 20, max_calls: 1, quote: '', currency: 'USD', allowed_actions: ['search', 'retain_metadata']});
    void load().catch(e => {if (scope.current === project) setError(e.message)});
  }, [project, api]);
  useEffect(() => {const c = connections.find(c => c.id === rightsId); if (c) setEditedRights({...c.rights})}, [rightsId, overview.connections]);
  const run = async (action: () => Promise<void>) => {
    if (busy) return; setBusy(true); setError(''); setMessage('');
    try {await action()} catch (e) {if (scope.current === project) setError(e instanceof Error ? e.message : '操作失败，请核对后重试')}
    finally {if (scope.current === project) setBusy(false)}
  };
  const write = <T,>(path: string, body: unknown, method = 'POST') => api<T>(path, {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const loadJob = async (id: string) => {
    setResults([]); setSelected([]); setImportApproved(false);
    const status = await api<Item>(`${base}/searches/${encodeURIComponent(id)}`);
    if (scope.current !== project) return;
    setJob(status);
    if (status.content_restricted) {setResults([]); setSelected([]); return}
    const rows = await api<Item[]>(`${base}/searches/${encodeURIComponent(id)}/results`);
    if (scope.current !== project) return;
    setResults(rows); setSelected(ids => ids.filter(id => rows.some(r => r.id === id)));
  };
  useEffect(() => {
    if (!job || !['queued', 'running', 'submitted'].includes(job.state)) return;
    const timer = setTimeout(() => {void loadJob(job.id).catch(e => {if (scope.current === project) setError(e.message)})}, 2500);
    return () => clearTimeout(timer);
  }, [job, project]);
  const saveSource = () => run(async () => {
    if (!selectedOperation || !source.label.trim() || !source.api_key || !source.api_authorized || !source.authorization_evidence.trim() || !newRights.evidence.trim()) throw new Error('请填写来源、凭证与授权依据，并确认正式 API 权限');
    let mapping: Item | undefined;
    if (source.mapping.trim()) {
      try {mapping = JSON.parse(source.mapping)} catch {throw new Error('CMM 映射必须是已核对的有效 JSON 对象')}
      if (!mapping || Array.isArray(mapping) || mapping.reviewed !== true || typeof mapping.evidence !== 'string' || !mapping.evidence.trim() || ['items_path', 'id_path', 'title_path', 'published_path'].some(key => typeof mapping![key] !== 'string' || !mapping![key].trim())) throw new Error('CMM 映射需要明确核对并填写依据、列表、身份、标题与发布时间路径');
    }
    await write(`${base}/connections`, {provider: selectedOperation.provider, operation: selectedOperation.operation, label: source.label.trim(), api_key: source.api_key,
      api_authorized: source.api_authorized, authorization_evidence: source.authorization_evidence.trim(), rights: {...newRights, expires_at: isoDate(newRights.expires_at)}, ...(mapping ? {response_mapping: mapping} : {})});
    if (scope.current !== project) return;
    setSource(s => ({...s, api_key: ''})); await load(); setMessage('来源配置已保存；真实账户能力仍待验收'); onChanged?.();
  });
  const preview = () => {
    setError(''); setMessage('');
    const price = activeConnection?.provider === 'datax' ? activeConnection.operation === 'douyin.videos.search' ? 0.02 : 0.002 : null;
    if (!activeConnection?.search_enabled || !validRights(activeConnection.rights, ['search', 'retain_metadata'])) {setError('请选择授权有效且已启用的来源'); return}
    if (!query.query.trim() || !query.start_date || !query.end_date || query.start_date > query.end_date || !Number.isInteger(Number(query.target_count)) || Number(query.target_count) < 1 || Number(query.target_count) > 1000 || !Number.isInteger(Number(query.max_calls)) || Number(query.max_calls) < 1 || Number(query.max_calls) > 100 || !Number.isFinite(Number(query.quote)) || Number(query.quote) <= 0) {setError('请填写关键词、有效日期、目标数量、最大调用次数与正数费用上限'); return}
    if (price && (query.currency !== 'USD' || Number(query.quote) + 1e-10 < price * Number(query.max_calls))) {setError(`该 DataX 操作文档单次上限为 USD ${price}，费用上限需覆盖最大调用次数`); return}
    setPending({...query, query: query.query.trim(), target_count: Number(query.target_count), max_calls: Number(query.max_calls), allowed_actions: [...query.allowed_actions], idempotency_key: crypto.randomUUID()}); setApproved(false);
  };
  const confirmSearch = () => run(async () => {
    if (!pending || !approved) return;
    const created = await write<Item>(`${base}/searches`, pending);
    if (scope.current !== project) return;
    setJob(created); setResults([]); setSelected([]); setReason(''); setImportApproved(false); setPending(null); setApproved(false);
    await load(); onChanged?.();
  });
  const chooseJob = (item: Item) => run(async () => {setJob(item); setResults([]); setSelected([]); setImportApproved(false); await loadJob(item.id)});
  const jobPlan = job?.plan || job?.payload?.plan;
  const jobConnection = connections.find(c => c.id === jobPlan?.connection_id);
  const selectedRows = results.filter(r => selected.includes(r.id));
  const retryingImport = overview.references.some((r: Item) => r.job_id === job?.id && selected.includes(r.result_id) && r.status === 'import_failed');
  const mediaActions = ['download_media', 'retain_media', 'model_process'];
  const canImport = selectedRows.length > 0 && mediaActions.every(k => jobPlan?.allowed_actions?.includes(k)) && validRights(jobConnection?.rights, mediaActions) && selectedRows.every(r => r.media_url && validRights(r.rights_snapshot, mediaActions));
  const selectResults = (importMedia: boolean) => run(async () => {
    if (!job || !selected.length || !reason.trim() || (importMedia && (!canImport || !importApproved))) return;
    if (importMedia && retryingImport && !window.confirm('请先核对上次导入状态。确认再次下载这些已授权媒体？若此前请求已接受，将复用同一任务；不会重新查询供应商。')) return;
    const payload: Item = {result_ids: selected, reason: reason.trim(), import_media: importMedia};
    if (importMedia) {
      const fingerprint = JSON.stringify({project, job: job.id, ...payload});
      if (importAttempt.current.fingerprint !== fingerprint || !importAttempt.current.key) importAttempt.current = {fingerprint, key: crypto.randomUUID()};
      payload.attempt_key = importAttempt.current.key;
    }
    const selectedResult = await write<Item>(`${base}/searches/${encodeURIComponent(job.id)}/select`, payload);
    if (scope.current !== project) return;
    if (importMedia) importAttempt.current = {fingerprint: '', key: ''};
    setSelected([]); setImportApproved(false); await load(); await loadJob(job.id);
    setMessage(importMedia ? (selectedResult?.kind==='industry_import'?(selectedResult.state==='succeeded'?'媒体导入任务已完成':'媒体导入任务状态：'+(statusLabels[selectedResult.state]||selectedResult.state)+'，请查看任务记录'):'实际媒体已导入素材库') : '已保存元数据参考，尚未导入媒体'); onChanged?.();
  });

  return <div className="view-body script-workspace industry-workspace" aria-label="行业参考工作台" style={{display: 'grid', gap: 20}}>
    <div className="inline between"><div><h2>行业参考</h2><p className="muted">使用自己的商业数据账号查找参考，先确认权限与费用，再保存值得研究的素材。</p></div><button className="quiet bordered" disabled={busy} onClick={() => run(load)}>刷新来源与任务</button></div>
    <p className="notice">来源连接尚未通过真实账户验收。保存设置与查看历史不会调用付费查询；每次查询都需确认费用上限。</p>
    {error && <p role="alert" className="notice">{error}</p>}{message && <p role="status" className="notice">{message}</p>}
    <details open={!connections.length}><summary>接入商业数据来源</summary><section className="panel" aria-label="新增来源连接">
      <div className="form-grid"><label className="field">来源操作<select aria-label="来源操作" value={source.operation} onChange={e => setSource(s => ({...s, operation: e.target.value, mapping: ''}))}>{overview.catalog.map((c: Item) => <option key={`${c.provider}:${c.operation}`} value={`${c.provider}:${c.operation}`}>{c.label}</option>)}</select></label>
        <label className="field">来源名称<input aria-label="来源名称" value={source.label} onChange={e => setSource(s => ({...s, label: e.target.value}))}/></label>
        <label className="field">API 密钥<input aria-label="来源 API 密钥" type="password" autoComplete="off" value={source.api_key} onChange={e => setSource(s => ({...s, api_key: e.target.value}))}/></label>
        <label className="field">API 权限依据<input aria-label="API 权限依据" value={source.authorization_evidence} onChange={e => setSource(s => ({...s, authorization_evidence: e.target.value}))}/></label></div>
      <label className="check-label"><input aria-label="已确认正式 API 调用权限" type="checkbox" checked={source.api_authorized} onChange={e => setSource(s => ({...s, api_authorized: e.target.checked}))}/>已确认正式 API 调用权限</label>
      <RightsFields prefix="新连接" value={newRights} onChange={setNewRights}/>
      {selectedOperation?.response_mapping_required && <div><label className="field">CMM 响应字段映射<textarea aria-label="CMM 响应字段映射" rows={8} value={source.mapping} onChange={e => setSource(s => ({...s, mapping: e.target.value}))}/></label><p className="fine">高级配置：仅填写已核对的真实响应路径与依据。没有映射可以保存设置，但检索保持禁用，不代表已接通。</p></div>}
      <button className="primary" disabled={busy} onClick={saveSource}>保存来源连接</button>
    </section></details>
    {!!connections.length && <details><summary>查看与调整来源授权</summary><section className="panel"><label className="field">授权管理来源<select aria-label="授权管理来源" value={rightsId} onChange={e => setRightsId(e.target.value)}>{connections.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}</select></label>
      <RightsFields prefix="调整权限" value={editedRights} onChange={setEditedRights}/><button className="quiet bordered" disabled={busy} onClick={() => run(async () => {await write(`${base}/connections/${encodeURIComponent(rightsId)}/rights`, {...editedRights, expires_at: isoDate(editedRights.expires_at)}, 'PATCH'); if (scope.current !== project) return; await load(); setPending(null); setApproved(false); setMessage('授权已更新，旧任务也会重新核对当前权限'); onChanged?.()})}>保存授权变更</button></section></details>}
    <section className="panel" aria-label="行业查询计划"><h3>创建查询计划</h3>
      <div className="form-grid"><label className="field">查询来源<select aria-label="查询来源" value={query.connection_id} onChange={e => changeQuery({connection_id: e.target.value, category: '', allowed_actions: ['search', 'retain_metadata']})}><option value="">选择已授权来源</option>{connections.map(c => <option key={c.id} value={c.id} disabled={!c.search_enabled}>{c.label} · {c.platform}</option>)}</select></label>
        <label className="field">检索关键词<input aria-label="检索关键词" value={query.query} onChange={e => changeQuery({query: e.target.value})}/></label>
        <label className="field">品类<input aria-label="查询品类" disabled={activeConnection?.provider === 'tikhub'} value={query.category} onChange={e => changeQuery({category: e.target.value})}/></label>
        <label className="field">开始日期<input aria-label="查询开始日期" type="date" value={query.start_date} onChange={e => changeQuery({start_date: e.target.value})}/></label>
        <label className="field">结束日期<input aria-label="查询结束日期" type="date" value={query.end_date} onChange={e => changeQuery({end_date: e.target.value})}/></label>
        <label className="field">目标结果数<input aria-label="目标结果数" type="number" min={1} max={1000} value={query.target_count} onChange={e => changeQuery({target_count: e.target.value})}/></label>
        <label className="field">最大调用次数<input aria-label="最大调用次数" type="number" min={1} max={100} value={query.max_calls} onChange={e => changeQuery({max_calls: e.target.value})}/></label>
        <label className="field">费用上限<input aria-label="费用上限" type="number" min="0" step="any" value={query.quote} onChange={e => changeQuery({quote: e.target.value})}/></label>
        <label className="field">币种<select aria-label="查询币种" value={query.currency} onChange={e => changeQuery({currency: e.target.value})}><option value="USD">USD</option><option value="CNY">CNY</option></select></label></div>
      {activeConnection?.provider === 'datax' && <p className="fine">文档单次费用上限：USD {activeConnection.operation === 'douyin.videos.search' ? '0.02' : '0.002'}。抖音视频与 TikTok 广告库分别查询；不会自动换源。</p>}
      {activeConnection?.provider === 'tikhub' && <p className="fine">此操作不支持品类筛选，精确日期在结果中筛选。费用待核清时仅请求一次并停止。</p>}
      {connections.filter(c => !c.search_enabled).map(c => <p className="fine" key={c.id}>{c.label}：{c.disabled_reason || '授权不足或接口映射待核对'}</p>)}
      <fieldset className="script-row"><legend>本次计划允许的操作</legend><div className="form-grid">{rightsLabels.map(([key, label]) => <label className="check-label" key={key}><input type="checkbox" aria-label={`本次计划：${label}`} checked={query.allowed_actions.includes(key)} disabled={['search', 'retain_metadata'].includes(key) || !activeConnection?.rights?.[key]} onChange={e => changeQuery({allowed_actions: e.target.checked ? [...query.allowed_actions, key] : query.allowed_actions.filter((k: string) => k !== key)})}/>{label}</label>)}</div><p className="fine">查询与数据留存是必要操作；下载、模型处理、衍生或导出均需单独允许。确认后不能扩大此计划权限。</p></fieldset>
      <button className="primary" disabled={busy || !activeConnection?.search_enabled} onClick={preview}>核对查询计划</button>
      {pending && <section className="script-row" role="region" aria-label="待确认查询计划"><h3>确认本次有界查询</h3><p>{activeConnection?.label} · {activeConnection?.platform} · {pending.query}{pending.category ? ` · 品类 ${pending.category}` : ''}</p><p>{pending.start_date} 至 {pending.end_date}；目标 {pending.target_count} 条，最多 {pending.max_calls} 次调用。</p><p>本次费用上限：{pending.currency} {pending.quote}。实际费用未核清时保留预留，不以零元代替。</p><p className="fine">允许操作：{rightsLabels.filter(([key]) => pending.allowed_actions.includes(key)).map(([, label]) => label).join('、')}</p>
        <label className="check-label"><input aria-label="我确认本次费用上限和允许操作" type="checkbox" checked={approved} onChange={e => setApproved(e.target.checked)}/>我确认本次费用上限和允许操作</label><div className="inline"><button className="primary" disabled={busy || !approved} onClick={confirmSearch}>确认上限并开始查询</button><button className="quiet" disabled={busy} onClick={() => {setPending(null); setApproved(false)}}>取消</button></div></section>}
    </section>
    <section className="panel"><h3>查询任务</h3>{!overview.search_jobs.length && <p className="muted">确认查询后，真实进度与结果会保存在本项目。</p>}<div className="script-list">{overview.search_jobs.map((j: Item) => <button className="quiet bordered" key={j.id} disabled={busy} aria-label={`查看查询 ${j.plan?.query || j.payload?.plan?.query || '受限任务'} ${j.id}`} onClick={() => chooseJob(j)}>{j.plan?.query || j.payload?.plan?.query || '授权已失效'} · {statusLabels[j.state] || j.state}</button>)}</div>
      {job && <div><div className="inline between"><h4>{statusLabels[job.state] || job.state}</h4><button className="quiet bordered" disabled={busy} onClick={() => run(() => loadJob(job.id))}>刷新查询结果</button></div><p className="fine">调用 {job.progress?.calls_attempted ?? 0} 次 · 收到 {job.progress?.rows_received ?? 0} 行 · 保存 {job.progress?.results_saved ?? 0} 条 · 已选 {job.progress?.selected ?? 0} 条 · 实际媒体 {job.progress?.media_imported ?? 0} 份</p>
        {job.stop_reason === 'billing_unknown' && <p className="notice">费用尚未核清，已停止继续请求，预留费用保留待核账；请在来源账户核对，不会自动重试。</p>}
        {job.state === 'needs_attention' && <p className="notice">本次查询需要核对来源响应、授权或账单。可能已计费，保留预留且不会自动重试。</p>}
        {job.content_restricted && <p className="notice">来源授权已失效，内容已限制读取。重新核对有效许可后再继续。</p>}
        <p className="fine">实际费用：{job.actual_cost == null ? '尚未核清' : `${job.quote?.currency || jobPlan?.currency || ''} ${job.actual_cost}`}{job.actual_cost == null && job.quote?.amount ? ` · 保留费用上限 ${job.quote.currency} ${job.quote.amount}` : ''}</p>{job.error && <p className="fine">{job.error}</p>}
      </div>}
    </section>
    {job && !job.content_restricted && <section className="panel" aria-label="行业查询结果"><h3>查询结果</h3><p className="fine">供应商结果需人工判断相关性与电商价值；缺失数据保持缺失，区间与估算不会变成精确销量。</p>{!results.length && <p className="muted">尚无可读取结果。查询完成后刷新，空结果也可能产生来源费用。</p>}
      {results.map(r => <article className="script-row" key={r.id}><label className="check-label"><input type="checkbox" aria-label={`选择 ${r.title || r.platform_id}`} checked={selected.includes(r.id)} onChange={e => {setSelected(ids => e.target.checked ? [...ids, r.id] : ids.filter(id => id !== r.id)); setImportApproved(false)}}/><b>{r.title || r.platform_id}</b></label><p className="fine">{r.platform} · {r.entity?.name || '主体信息缺失'} · 来源编号 {r.platform_id} · {r.published_at || '发布时间缺失'}</p><div className="inline" style={{flexWrap: 'wrap'}}>{Object.entries(r.metrics || {}).map(([name, value]) => <span className="fine" key={name}>{metricLabels[name] || name}：{metricText(value as Item)}</span>)}</div>
        <p className="fine">统计期间：{r.statistics_period?.start || r.statistics_period?.end ? `${r.statistics_period.start || '缺失'} 至 ${r.statistics_period.end || '缺失'}` : '来源未提供具体统计期间'} · 抓取时间：{r.fetched_at || '缺失'} · 来源操作：{r.provenance?.operation || '缺失'}</p>{r.source_url && /^https?:\/\//i.test(r.source_url) && <a href={r.source_url} target="_blank" rel="noreferrer noopener">查看原始来源</a>}{!r.media_url && <p className="fine">此结果没有真实媒体直链，可保存数据参考。</p>}</article>)}
      <label className="field">选择理由<textarea aria-label="选择理由" value={reason} onChange={e => setReason(e.target.value)} placeholder="记录值得保存的具体镜头、钩子或结构"/></label>
      <label className="check-label"><input aria-label="我确认本次媒体下载、留存和模型处理授权" type="checkbox" disabled={!canImport} checked={importApproved} onChange={e => setImportApproved(e.target.checked)}/>我确认本次媒体下载、留存和模型处理授权</label><div className="inline"><button className="primary" disabled={busy || !selected.length || !reason.trim()} onClick={() => selectResults(false)}>保存为参考</button><button className="quiet bordered" disabled={busy || !canImport || !importApproved || !reason.trim()} onClick={() => selectResults(true)}>{retryingImport ? '确认重试媒体导入' : '导入授权媒体'}</button></div><p className="fine">保存参考只留存授权数据。导入需本次计划、当前许可和真实媒体直链同时允许；不会自动分析、仿拍或发布。</p>
    </section>}
    <section className="panel" aria-label="已选行业参考"><h3>已选参考</h3>{!overview.references.length && <p className="muted">选择结果并记录理由后，参考会保存在这里。</p>}{overview.references.map((r: Item) => <article className="production-row" key={r.id}>{r.content_restricted ? <p>授权已失效的参考</p> : <><b>{r.title || r.platform_id}</b><p>{r.selection_reason}</p><p className="fine">{r.asset_id ? '真实媒体已入库' : r.status === 'import_failed' ? '媒体导入失败，未创建已完成素材' : '元数据参考 · 尚未导入媒体'}</p></>}</article>)}</section>
  </div>;
}
