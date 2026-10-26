import {useEffect, useState, type FormEvent} from 'react';
import {request, post} from '../api';

type Status = {
  mode: 'internal'|'commercial'; can_work: boolean; device_id?: string;
  license_id?: string; expires_at?: number; offline_until?: number;
  reason?: string; last_error?: string; features?: string[];
};
const featureNames:Record<string,string>={'workspace.use':'工作空间','product.manage':'商品管理','assets.import':'素材导入','assets.analyze':'素材分析','discovery.public':'公开参考发现','discovery.paid':'商业数据查询','video.breakdown':'视频拆解','creative.generate':'创意分析','script.generate':'脚本创作','script.export':'脚本导出','providers.configure':'模型与数据配置'};
const date = (value?:number) => value ? new Date(value*1000).toLocaleString('zh-CN') : '尚未激活';

export default function LicenseSettings() {
  const [status,setStatus]=useState<Status|null>(null);
  const [licenseId,setLicenseId]=useState('');
  const [password,setPassword]=useState('');
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  useEffect(()=>{
    let active=true;
    const load=()=>request<Status>('/license/status').then(value=>{if(active)setStatus(value)}).catch(e=>{if(active)setError(e.message)});
    void load(); const timer=window.setInterval(load,60000);
    return ()=>{active=false;window.clearInterval(timer)};
  },[]);
  async function activate(event:FormEvent) {
    event.preventDefault();setBusy(true);setError('');
    try {setStatus(await post<Status>('/license/login',{license_id:licenseId.trim(),password}));}
    catch(e){setError((e as Error).message)}
    finally{setPassword('');setBusy(false)}
  }
  async function refresh(action: 'refresh'|'release'='refresh'){
    setBusy(true);setError('');
    try{setStatus(await post<Status>('/license/'+action,{}))}
    catch(e){setError((e as Error).message);try{setStatus(await request<Status>('/license/status'))}catch{}}
    finally{setBusy(false)}
  }
  return <section className="settings-section" aria-label="软件授权">
    <h3>软件授权</h3>
    {error&&<p role="alert">{error}</p>}
    {!status&&<p>正在读取授权状态…</p>}
    {status?.mode==='internal'?<><p>内部开发模式</p><p>此运行环境未验证商业授权，不代表正式商业发行。</p></>:status&&<>
      <p><strong>{status.can_work?'授权有效':'需要授权'}</strong></p>
      {status.reason&&<p>{status.reason}</p>}
      {status.last_error&&<p role="status">{status.last_error}</p>}
      {status.features&&<p>已开放功能：<span>{status.features.length?status.features.map(feature=>featureNames[feature]||feature).join('、'):'当前授权未开放新工作功能'}</span></p>}
      <dl><dt>到期时间</dt><dd>{date(status.expires_at)}</dd>
        <dt>离线可用至</dt><dd>{date(status.offline_until)}</dd>
        <dt>本机设备编号</dt><dd style={{overflowWrap:'anywhere',userSelect:'all'}}>{status.device_id}</dd></dl>
      <p>授权按首次激活起算，仅限一台设备。联网时每 60 秒验证，离线最长 24 小时且不超过授权到期时间。</p>
      <form onSubmit={activate}>
        <label htmlFor="license-id">授权编号</label>
        <input id="license-id" value={licenseId} onChange={e=>setLicenseId(e.target.value)} required autoComplete="username" maxLength={100}/>
        <label htmlFor="license-password">授权密码</label>
        <input id="license-password" type="password" value={password} onChange={e=>setPassword(e.target.value)} required autoComplete="current-password" maxLength={512}/>
        <div style={{display:'flex',gap:8,marginTop:12}}>
          <button type="submit" disabled={busy}>{busy?'正在验证…':'登录并激活'}</button>
          <button type="button" onClick={()=>refresh()} disabled={busy}>刷新授权</button>
          <button type="button" onClick={()=>refresh('release')} disabled={busy}>释放本机授权</button>
        </div>
      </form>
      <p>到期后仍可查看和导出已有本地内容。正常换机可先释放本机授权，再在新设备登录。旧设备不可用时，请联系管理员迁移，并提供授权编号与新设备编号。</p>
    </>}
  </section>;
}
