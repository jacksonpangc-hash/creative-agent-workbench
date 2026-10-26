// This standalone function is injected only after the user clicks Collect.
export function captureVisiblePage() {
 const allowed=['www.douyin.com','douyin.com','www.iesdouyin.com','iesdouyin.com','v.douyin.com','www.kuaishou.com','kuaishou.com','v.kuaishou.com'];
 if(location.protocol!=='https:'||!allowed.includes(location.hostname))throw Error('请在抖音或快手公开页面主动收集');
 const visible=el=>{
  for(let item=el;item;item=item.parentElement){const s=window.getComputedStyle(item);if(s.display==='none'||s.visibility==='hidden'||item.getAttribute('aria-hidden')==='true')return false;}
  const box=el.getBoundingClientRect();return box.width>0&&box.height>0;
 };
 const text=selectors=>{for(const sel of selectors){const el=Array.from(document.querySelectorAll(sel)).find(visible);if(el?.textContent?.trim())return el.textContent.trim();}return '';};
 let author=text(['[data-e2e="user-title"]','[data-e2e="video-author"]','[data-testid="author-name"]']).slice(0,300),ownerUrl='';
 // The public detail page also has a nearby owner/follower block. Do not
 // mistake visible comment authors or the user's own navigation for the owner.
 if(!author)for(const link of document.querySelectorAll('a[href*="/user/"]')){
  if(!visible(link)||!/\S/.test(link.textContent))continue;
  let block=link.parentElement;
  for(let depth=0;block&&block!==document.body&&block!==document.documentElement&&depth<3;depth++,block=block.parentElement){
   const content=block.textContent.trim();
   if(content.length<500&&content.includes('粉丝')&&content.includes('获赞')){
    author=(link.querySelector('[data-click-from="title"]')||link).textContent.trim().slice(0,300);ownerUrl=link.href;break;
   }
  }
  if(author)break;
 }
 const metrics=[];const as_of=new Date().toISOString();
 const isVideo=/\/(?:video|short-video|share\/video)\/[^/]+/.test(location.pathname)||new URLSearchParams(location.search).has('modal_id');
 if(isVideo)for(const [name,selectors] of [
  ['likes',['[data-e2e="like-count"]','[data-testid="like-count"]']],
  ['comments',['[data-e2e="comment-count"]','[data-testid="comment-count"]']],
  ['shares',['[data-e2e="share-count"]','[data-testid="share-count"]']],
  ['favorites',['[data-e2e="collect-count"]','[data-testid="favorite-count"]']]
 ]) {const raw=text(selectors);if(raw&&raw.length<=100)metrics.push({name,raw,basis:'visible_page',as_of});}
 // Versioned fallback for the actually observed four-action detail strip.
 // Require its share marker, order and icon geometry; a changed layout is
 // unknown rather than an invitation to infer labels from arbitrary numbers.
 if(isVideo&&!metrics.length&&location.hostname.endsWith('douyin.com')){
  const share=Array.from(document.querySelectorAll('[data-e2e="video-share-icon-container"]')).find(visible);
  const actions=share?Array.from(share.parentElement.children).filter(visible):[];
  const geometry=['0 0 90 94','0 0 99 99','0 0 100 100','0 0 99 99'];
  if(actions.length===4&&actions[3]===share&&actions.every((el,i)=>el.querySelector('svg')?.getAttribute('viewBox')===geometry[i])){
   const values=actions.map(el=>Array.from(new Set(Array.from(el.querySelectorAll('span')).filter(visible).map(s=>s.textContent.trim()).filter(raw=>/^\d[\d.,]*(?:万|亿|w|k)?\+?$/i.test(raw)))));
   if(values.every(v=>v.length===1))['likes','comments','favorites','shares'].forEach((name,i)=>metrics.push({name,raw:values[i][0],basis:'visible_page',as_of}));
  }
 }
 const links=[];const seen=new Set();
 for(const anchor of document.querySelectorAll('a[href]')){
  if(!visible(anchor))continue;
  let url;try{url=new URL(anchor.href,location.href)}catch{continue}
  if(url.protocol!=='https:'||!allowed.includes(url.hostname)||url.username||url.password)continue;
  if(!/\/(?:video|short-video|share\/video|user|profile)\/[^/]+/.test(url.pathname)&&!url.hostname.startsWith('v.'))continue;
  if(/\/(?:user|profile)\//.test(url.pathname)&&(url.pathname.endsWith('/self')||!ownerUrl||url.href!==ownerUrl))continue;
  if(seen.has(url.href))continue;seen.add(url.href);
  let cardTitle=(anchor.textContent||'公开参考链接').trim(),cardAuthor='';
  // Observed public card structure, anchored to the media marker rather than hashed CSS names.
  // A missing or changed structure falls back to visible link text and never guesses metrics.
  const media=anchor.querySelector('[data-home-video-id]');
  const info=media?.nextElementSibling?.firstElementChild;
  const titleNode=info?.children[0],byline=info?.children[1]?.children[0];
  if(titleNode&&byline&&visible(titleNode)&&visible(byline)&&byline.textContent.trim().startsWith('@')){
   cardTitle=titleNode.textContent.trim();cardAuthor=byline.textContent.trim().replace(/^@\s*/,'');
  }
  if(ownerUrl===url.href&&author)cardTitle=author;
  links.push({url:url.href,title:cardTitle.slice(0,2000),author:cardAuthor.slice(0,300),metrics:[]});
  if(links.length===49)break;
 }
 return {page:{url:location.href,title:(text(['h1'])||document.title||'公开页面').slice(0,2000),author,metrics},links};
}
