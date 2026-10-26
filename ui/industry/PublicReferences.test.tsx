import {afterEach,expect,test,vi} from 'vitest';
import {render,screen,fireEvent,cleanup,waitFor} from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import PublicReferences from './PublicReferences';
afterEach(()=>{cleanup();vi.unstubAllGlobals()});
test('creates durable public search plan and opens official links without paid calls',async()=>{
 const calls:{url:string,body:any}[]=[];let plans:any[]=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string,options?:RequestInit)=>{
  calls.push({url,body:options?.body?JSON.parse(options.body as string):null});
  if(options?.method==='POST')plans=[{id:'plan',query:'洗衣液',links:[{platform:'douyin',url:'https://www.douyin.com/search/test'}]}];
  return new Response(JSON.stringify(url.endsWith('/plans')?plans:[]),{status:200});
 }));
 render(<PublicReferences base="/projects/A"/>);
 await screen.findByText('还没有参考视频，先收藏一条或添加链接');fireEvent.click(screen.getByRole('tab',{name:'平台网页'}));
 fireEvent.change(screen.getByLabelText('搜索关键词'),{target:{value:'洗衣液'}});
 fireEvent.click(screen.getByRole('button',{name:'生成免费搜索计划'}));
 const link=await screen.findByRole('link',{name:'去抖音搜索'});
 expect(link).toHaveAttribute('href','https://www.douyin.com/search/test');
 expect(calls.filter(c=>c.body)[0].body).toEqual({query:'洗衣液',platforms:['douyin','kuaishou']});
 expect(calls.every(c=>c.url.includes('/discovery/')||c.url.includes('/browser-bridge/')||c.url.endsWith('/state'))).toBe(true);
});
test('missing media blocks selection; paired code stays in project and existing file can be linked',async()=>{
 let references:any[]=[{id:'ref',title:'演示参考',url:'https://www.douyin.com/video/123',platform:'douyin',media_state:'missing',can_visually_analyze:false,observations:[]}];
 const onSelect=vi.fn();
 vi.stubGlobal('fetch',vi.fn(async(url:string,options?:RequestInit)=>{
  let data:any=url.endsWith('/references')?references:[];
  if(url.endsWith('/pairing'))data={code:'ABCDEFGH23',project_id:'A',expires_in:120};
  if(url.endsWith('/browser-bridge/status'))data={state:'ready'};
  if(url.endsWith('/browser-bridge/connections'))data={connections:[]};
  if(url.endsWith('/attach')){references=[{...references[0],asset_id:'asset',media_state:'available',can_visually_analyze:true}];data=references[0]}
  if(url.endsWith('/assets'))data=[];
  if(url.endsWith('/state'))data={assets:[{id:'asset',title:'本地视频'}]};
  return new Response(JSON.stringify(data),{status:200});
 }));
 render(<PublicReferences base="/projects/A" onSelect={onSelect}/>);
 await screen.findByText('演示参考');
 expect(screen.getByRole('button',{name:'加入本次创作'})).toBeDisabled();
 fireEvent.click(screen.getByRole('tab',{name:'平台网页'}));fireEvent.click(screen.getByText('开发与排障：单次导入'));fireEvent.click(screen.getByRole('button',{name:'生成本次导入连接'}));
 const info=JSON.parse((await screen.findByLabelText('本次连接信息')).getAttribute('value')||(screen.getByLabelText('本次连接信息') as HTMLTextAreaElement).value);
 expect(info.code).toBe('ABCDEFGH23');expect(info.project_id).toBe('A');
 expect(screen.getByText(/2 分钟内/)).toBeInTheDocument();
 expect(info.base_url).toBe('http://127.0.0.1:8765');
 fireEvent.click(screen.getByRole('tab',{name:'我的收藏'}));fireEvent.click(screen.getByText('补充已有视频文件'));fireEvent.change(screen.getByLabelText('关联已有文件：演示参考'),{target:{value:'asset'}});
 fireEvent.click(screen.getByRole('button',{name:'关联文件'}));
 await waitFor(()=>expect(screen.getByRole('button',{name:'加入本次创作'})).not.toBeDisabled());
 fireEvent.click(screen.getByRole('button',{name:'加入本次创作'}));
 expect(onSelect).toHaveBeenCalledWith('asset');
});

test('previews multiline import without saving, explains ignored fields and confirms explicitly',async()=>{
 const posts:{url:string,body:any}[]=[];let references:any[]=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string,options?:RequestInit)=>{
  const body=options?.body?JSON.parse(options.body as string):null;
  if(options?.method==='POST')posts.push({url,body});
  let data:any=url.endsWith('/references')?references:[];
  const ref={id:'imported',title:'导入实测',author:'小李',platform:'douyin',url:'https://www.douyin.com/video/123',media_state:'missing',observations:[{source:'user_import',captured_at:'2026-10-23T01:00:00Z',metrics:[{name:'likes',raw:'1万–2万',basis:'user_import',as_of:'2026-10-23T01:00:00Z'}]}]};
  if(url.endsWith('/preview'))data={rows:[{row:1,reference:ref,errors:[],duplicate:null}],ignored_columns:['销量'],can_confirm:true};
  if(url.endsWith('/import')){references=[ref];data=references}
  if(url.endsWith('/state'))data={assets:[]};
  return new Response(JSON.stringify(data),{status:200});
 }));
 render(<PublicReferences base="/projects/A"/>);
 fireEvent.click(screen.getByRole('button',{name:'添加参考'}));fireEvent.change(screen.getByLabelText('分享文案或链接（每行一条）'),{target:{value:'分享 https://v.douyin.com/ABC/\nhttps://v.kuaishou.com/DEF/'}});
 fireEvent.click(screen.getByRole('button',{name:'预览导入'}));
 await screen.findByRole('heading',{name:'导入预览'});
 expect(posts).toHaveLength(1);
 expect(posts[0].url).toContain('/projects/A/discovery/references/preview');
 expect(screen.getByText(/未支持的字段将忽略：销量/)).toBeInTheDocument();
 expect(screen.getByRole('button',{name:'确认导入'})).toBeDisabled();
 fireEvent.click(screen.getByLabelText('确认忽略以上字段'));
 fireEvent.click(screen.getByRole('button',{name:'确认导入'}));
 await screen.findByText('已导入 1 条参考；相同作品继续使用原记录。');
 expect(posts[1].body.ignore_unknown).toBe(true);
 expect(screen.getByText(/1万–2万/)).toBeInTheDocument();
 expect(screen.getByRole('button',{name:'加入本次创作'})).toBeDisabled();
});

test('project switch clears import preview and ignores a late preview response',async()=>{
 let resolvePreview:(value:Response)=>void=()=>{};
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
  if(url.endsWith('/preview'))return new Promise<Response>(resolve=>{resolvePreview=resolve});
  return new Response(JSON.stringify(url.endsWith('/state')?{assets:[]}:[]),{status:200});
 }));
 const view=render(<PublicReferences base="/projects/A"/>);
 fireEvent.click(screen.getByRole('button',{name:'添加参考'}));fireEvent.change(screen.getByLabelText('分享文案或链接（每行一条）'),{target:{value:'https://v.douyin.com/ABC/'}});
 fireEvent.click(screen.getByRole('button',{name:'预览导入'}));
 await waitFor(()=>expect(vi.mocked(fetch).mock.calls.some(c=>String(c[0]).endsWith('/preview'))).toBe(true));
 view.rerender(<PublicReferences base="/projects/B"/>);
 fireEvent.click(screen.getByRole('button',{name:'添加参考'}));
 resolvePreview(new Response(JSON.stringify({can_confirm:true,ignored_columns:[],rows:[{row:1,errors:[],reference:{title:'旧项目参考'}}]}),{status:200}));
 await waitFor(()=>expect(screen.getByLabelText('分享文案或链接（每行一条）')).toHaveValue(''));
 expect(screen.queryByText('旧项目参考')).not.toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'确认导入'})).not.toBeInTheDocument();
});

test('invalid rows block confirmation and editing removes stale preview',async()=>{
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>new Response(JSON.stringify(url.endsWith('/preview')?{can_confirm:false,ignored_columns:[],rows:[{row:2,reference:null,errors:['缺少链接']}]}:url.endsWith('/state')?{assets:[]}:[]),{status:200})));
 render(<PublicReferences base="/projects/A"/>);
 fireEvent.click(screen.getByRole('button',{name:'添加参考'}));fireEvent.change(screen.getByLabelText('分享文案或链接（每行一条）'),{target:{value:'bad'}});
 fireEvent.click(screen.getByRole('button',{name:'预览导入'}));
 await screen.findByText(/缺少链接/);
 expect(screen.getByRole('button',{name:'确认导入'})).toBeDisabled();
 fireEvent.change(screen.getByLabelText('分享文案或链接（每行一条）'),{target:{value:'https://v.douyin.com/ABC/'}});
 expect(screen.queryByRole('button',{name:'确认导入'})).not.toBeInTheDocument();
});

test('local video supplement attaches the existing reference and selection reports its media',async()=>{
 let ref:any={id:'ref',title:'原始参考',author:'作者',url:'https://www.douyin.com/video/123',platform:'douyin',can_visually_analyze:false,observations:[]};
 const onSelect=vi.fn(),calls:{url:string,body:any}[]=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string,options?:RequestInit)=>{
  calls.push({url,body:options?.body});
  let data:any=url.endsWith('/references')?[ref]:url.endsWith('/state')?{assets:[]}:[];
  if(url.includes('/assets?'))data={id:'local'};
  if(url.endsWith('/ref/attach')){ref={...ref,asset_id:'local',can_visually_analyze:true};data=ref}
  return new Response(JSON.stringify(data),{status:200});
 }));
 render(<PublicReferences base="/projects/A" onSelect={onSelect}/>);
 await screen.findByText('原始参考');
 fireEvent.click(screen.getByText('补充已有视频文件'));
 fireEvent.change(screen.getByLabelText('导入有权使用的文件'),{target:{files:[new File(['local'], 'clip.mp4', {type:'video/mp4'})]}});
 await waitFor(()=>expect(screen.getByRole('button',{name:'加入本次创作'})).not.toBeDisabled());
 expect(calls.find(c=>c.url.endsWith('/ref/attach'))?.body).toBe(JSON.stringify({asset_id:'local'}));
 expect(calls.filter(c=>c.url.endsWith('/references')&&c.body)).toHaveLength(0);
 fireEvent.click(screen.getByRole('button',{name:'加入本次创作'}));
 expect(onSelect).toHaveBeenCalledWith('local');
 expect(screen.getByRole('status')).toHaveTextContent('已加入本次创作：原始参考');
});

test('card uses actual local duration and cover and can reflect parent selection',async()=>{
 const NativeURL=URL;
 vi.stubGlobal('URL',class extends NativeURL{static createObjectURL=vi.fn(()=>'blob:local-cover');static revokeObjectURL=vi.fn()});
 const onSelect=vi.fn();
 const ref={id:'ref',title:'有文件的参考',url:'https://www.douyin.com/video/123',platform:'douyin',asset_id:'asset',can_visually_analyze:true,observations:[]};
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>url.includes('poster=true')?new Response('local poster',{status:200}):new Response(JSON.stringify(url.endsWith('/references')?[ref]:url.endsWith('/state')?{assets:[{id:'asset',inspection:'ready',metadata:{duration_ms:32500}}]}:[]),{status:200})));
 const view=render(<PublicReferences base="/projects/A" onSelect={onSelect}/>);
 expect(await screen.findByText(/32.5 秒/)).toBeInTheDocument();
 expect(await screen.findByAltText('有文件的参考的本地封面')).toHaveAttribute('src','blob:local-cover');
 fireEvent.click(screen.getByRole('button',{name:'加入本次创作'}));
 expect(onSelect).toHaveBeenCalledWith('asset');
 // Parent-owned selection can later clear the reference without stale local feedback.
 view.rerender(<PublicReferences base="/projects/A" onSelect={onSelect} selectedAssetIds={[]}/>);
 expect(screen.queryByText('已加入本次创作：有文件的参考')).not.toBeInTheDocument();
});
