export type OidcConfig={issuer:string;client_id:string;authorization_endpoint:string;token_endpoint:string;mode?:string;enabled?:boolean};
const key='creative-oidc-pkce';
const encode=(bytes:Uint8Array)=>btoa(String.fromCharCode(...bytes)).replaceAll('+','-').replaceAll('/','_').replace(/=+$/,'');
function checked(config:OidcConfig,redirect:string){
 for(const url of [config.issuer,config.authorization_endpoint,config.token_endpoint,redirect]){
  const parsed=new URL(url);if(parsed.protocol!=='https:'||parsed.username||parsed.password)throw new Error('企业登录必须使用 HTTPS 地址');
 }
 if(!config.client_id)throw new Error('管理员尚未配置企业登录');
}
export async function beginLogin(config:OidcConfig,redirect:string){
 checked(config,redirect);
 const verifier=encode(crypto.getRandomValues(new Uint8Array(32))),state=encode(crypto.getRandomValues(new Uint8Array(32)));
 const challenge=encode(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(verifier))));
 sessionStorage.setItem(key,JSON.stringify({verifier,state,redirect,issuer:config.issuer,client:config.client_id,created:Date.now()}));
 const url=new URL(config.authorization_endpoint);
 for(const [k,v] of Object.entries({client_id:config.client_id,redirect_uri:redirect,response_type:'code',scope:'openid profile',state,code_challenge:challenge,code_challenge_method:'S256'}))url.searchParams.set(k,v);
 return url.toString();
}
export async function completeLogin(config:OidcConfig,params:URLSearchParams,redirect:string,fetcher:typeof fetch=fetch){
 checked(config,redirect);
 const saved=sessionStorage.getItem(key);sessionStorage.removeItem(key);
 if(!saved)throw new Error('登录状态已过期，请重新登录');
 const pending=JSON.parse(saved);
 if(pending.state!==params.get('state')||pending.redirect!==redirect||pending.issuer!==config.issuer||pending.client!==config.client_id||Date.now()-pending.created>600000||Date.now()<pending.created)throw new Error('登录回调校验失败，请重新登录');
 if(params.has('error')||!params.get('code'))throw new Error('企业登录未完成');
 const body=new URLSearchParams({grant_type:'authorization_code',client_id:config.client_id,code:params.get('code')!,redirect_uri:redirect,code_verifier:pending.verifier});
 const response=await fetcher(config.token_endpoint,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body,credentials:'omit',redirect:'error'});
 if(!response.ok)throw new Error('身份服务未接受登录，请重新登录');
 const data=await response.json();
 if(typeof data.access_token!=='string'||data.token_type?.toLowerCase()!=='bearer')throw new Error('身份服务返回无效访问凭证');
 return data.access_token as string;
}
