export function localBase(value){
 const base=new URL(value);
 if(base.protocol!=='http:'||!['127.0.0.1','localhost'].includes(base.hostname)||base.username||base.password||base.pathname!=='/'||base.search||base.hash)throw Error('请使用创作台提供的本机应用地址。');
 return base.origin;
}
export function parseConnection(text){
 let value;try{value=JSON.parse(text)}catch{throw Error('连接信息无法识别，请从创作台重新复制。')}
 if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).some(key=>!['base_url','code','project_id','expires_at'].includes(key)))throw Error('连接信息含不支持的字段，请从创作台重新复制。');
 if(typeof value.code!=='string'||!/^[A-Z2-9]{8,40}$/.test(value.code.trim().toUpperCase()))throw Error('请填写创作台提供的配对码。');
 if(value.expires_at!=null&&(!Number.isFinite(value.expires_at)||value.expires_at<=Date.now()))throw Error('连接信息已过期，请回到创作台重新连接。');
 if(value.project_id!=null&&(typeof value.project_id!=='string'||value.project_id.length>100))throw Error('连接的项目无法识别，请重新复制连接信息。');
 return {...value,base_url:localBase(value.base_url),code:value.code.trim().toUpperCase()};
}
export const workbenchMatch=base=>localBase(base)+'/*';
export async function focusWorkbench(browser,base){
 const origin=localBase(base);
 const tabs=await browser.tabs.query({url:workbenchMatch(origin)});
 const tab=tabs.find(item=>{try{return item.id&&new URL(item.url).origin===origin}catch{return false}});
 if(!tab)return false;
 await browser.tabs.update(tab.id,{active:true});
 if(tab.windowId!=null)await browser.windows.update(tab.windowId,{focused:true});
 return true;
}
export function receiptText(result){
 const receipt=result.receipt;
 if(receipt&&['created','updated','skipped','failed'].every(key=>Number.isInteger(receipt[key])&&receipt[key]>=0))return `新增 ${receipt.created} 条 · 更新 ${receipt.updated} 条 · 跳过 ${receipt.skipped} 条 · 未完成 ${receipt.failed} 条`;
 if(Array.isArray(result.references))return `已保存 ${new Set(result.references.map(ref=>ref.id)).size} 条参考；新增与更新数量未提供。`;
 return '服务没有返回保存结果，请回到创作台刷新收藏确认。';
}
