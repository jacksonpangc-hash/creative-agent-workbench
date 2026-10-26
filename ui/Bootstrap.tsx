import {useEffect,useRef,useState} from 'react';
import App from './App';
import {API,setSessionToken,setTeamMode} from './api';
import {beginLogin,completeLogin,type OidcConfig} from './oidc';

export default function Bootstrap(){
 const started=useRef(false);
 const [ready,setReady]=useState(false),[config,setConfig]=useState<OidcConfig|null>(null),[error,setError]=useState('');
 const redirect=location.origin+location.pathname;
 useEffect(()=>{if(started.current)return;started.current=true;
  (async()=>{
   const response=await fetch(API+'/api/team/config',{credentials:'omit'});
   if(response.status===404){setReady(true);return}
   if(!response.ok)throw new Error('无法读取工作空间登录配置');
   const data=await response.json();
   if(data.mode!=='team')throw new Error('工作空间登录配置无效');
   setConfig(data);setTeamMode(true);
   const params=new URLSearchParams(location.search);
   if(params.has('code')||params.has('error')){
    history.replaceState(null,'',location.pathname);
    const token=await completeLogin(data,params,redirect);setSessionToken(token);setReady(true);
   }
  })().catch(e=>setError(e.message));
 },[]);
 useEffect(()=>{const expired=()=>{setSessionToken('');setReady(false);setError('登录已过期，请重新登录')};window.addEventListener('creative-auth-expired',expired);return()=>window.removeEventListener('creative-auth-expired',expired)},[]);
 if(ready)return <App/>;
 return <main className="connection"><h2>{config?'团队工作空间':'正在连接工作空间'}</h2>{error&&<p role="alert">{error}</p>}{config&&<><p>使用企业账户登录，项目权限由团队管理员分配。</p><button className="primary" disabled={!config.enabled} onClick={()=>beginLogin(config,redirect).then(url=>location.assign(url)).catch(e=>setError(e.message))}>企业账户登录</button>{!config.enabled&&<p>管理员尚未配置浏览器登录。</p>}</>}</main>;
}
