import {afterEach,expect,test,vi} from 'vitest';
import {render,screen,waitFor,fireEvent,cleanup,within} from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import App from './App';
import {version} from '../package.json';

afterEach(()=>{cleanup();sessionStorage.clear();localStorage.clear();vi.restoreAllMocks()});
test('new workspace exposes setup and core capabilities without fabricated sales',async()=>{
 vi.stubGlobal('fetch',vi.fn(async(input:string)=>new Response(JSON.stringify(input.endsWith('/projects')?[]:{providers:[{id:'deepseek',name:'DeepSeek',country:'中国',capabilities:['strategy','image_input']}]}),{status:200,headers:{'Content-Type':'application/json'}})));
 render(<App/>);
 await waitFor(()=>expect(screen.getByRole('dialog',{name:'创建项目'})).toBeInTheDocument());
 expect(screen.queryByRole('heading',{name:'今天想拍什么？'})).not.toBeInTheDocument();
 expect(screen.getByRole('navigation',{name:'主导航'})).toHaveTextContent('创作台我的素材');
 expect(screen.getByText('体验版')).toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'视频仿拍'})).not.toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'高频片段'})).not.toBeInTheDocument();
 expect(screen.queryByText(/ROI.*[0-9]/)).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'关闭'}));
 expect(screen.getByRole('button',{name:'全部创作'})).toBeInTheDocument();
});

test('late response from previous project cannot replace the current workspace',async()=>{
 let resolveA!:(r:Response)=>void;
 const late=new Promise<Response>(resolve=>{resolveA=resolve});
 const state=(id:string,title:string)=>({project:{id,name:id,currency:'CNY'},budget:{},models:[],assets:[],notes:[],parses:[],templates:[],jobs:[],groups:[],instructions:[],sessions:[{id,title,scope:{asset_ids:[]},mode:'directed',turns:[]}]});
 const response=(v:unknown)=>new Response(JSON.stringify(v),{status:200,headers:{'Content-Type':'application/json'}});
 const fetcher=vi.fn(async(input:string)=>input.endsWith('/projects')?response([{id:'A',name:'A'},{id:'B',name:'B'}]):input.endsWith('/catalog')?response({providers:[]}):input.includes('/A/state')?late:response(state('B','B 的分析')));
 vi.stubGlobal('fetch',fetcher);
 render(<App/>);
 await waitFor(()=>expect(fetcher.mock.calls.some(([u])=>u.includes('/A/state'))).toBe(true));
 fireEvent.change(screen.getByRole('combobox',{name:'当前项目'}),{target:{value:'B'}});
 await screen.findByRole('button',{name:'B 的分析'});
 resolveA(response(state('A','A 的私有分析')));
 await new Promise(resolve=>setTimeout(resolve,20));
 expect(screen.queryByText('A 的私有分析')).not.toBeInTheDocument();
 expect(screen.getByRole('button',{name:'B 的分析'})).toBeInTheDocument();
});

test('uncertain submission retry uses the same paid operation key and both groups',async()=>{
 const attempts:any[]=[];
 const workspace={project:{id:'A',name:'A',currency:'CNY'},budget:{},notes:[],parses:[],templates:[],jobs:[],instructions:[],sessions:[],
  models:[{id:'model',role:'strategy',has_key:true,label:'策略',availability:{image_input:{available:true},video_input:{available:false,reason:'不支持视频分析'}}}],
  assets:[{id:'a',title:'商品A',source_kind:'own',media_type:'image',metadata:{width:8,height:8},inspection:'ready'},{id:'b',title:'商品B',source_kind:'own',media_type:'image',metadata:{width:8,height:8},inspection:'ready'}],
  groups:[{id:'g1',title:'A组',asset_ids:['a']},{id:'g2',title:'B组',asset_ids:['b']}]};
 const response=(v:any)=>new Response(JSON.stringify(v),{status:200});
 vi.stubGlobal('fetch',vi.fn(async(input:string,init:any)=>{
  if(input.endsWith('/projects'))return response([{id:'A',name:'A'}]);
  if(input.endsWith('/catalog'))return response({providers:[]});
  if(input.endsWith('/search'))return response(workspace.assets);
  if(input.endsWith('/actions')){attempts.push(JSON.parse(init.body));if(attempts.length===1)throw new Error('network lost');return response({id:'job'})}
  return response(workspace);
 }));
 render(<App/>);
 await openSources();
 await waitFor(()=>expect(screen.getByRole('listbox',{name:'分析素材分组'})).toBeInTheDocument());
 await screen.findByRole('option',{name:'A组 · 1'});
 const list=screen.getByRole('listbox',{name:'分析素材分组'}) as HTMLSelectElement;
 Array.from(list.options).forEach(o=>{o.selected=true});fireEvent.change(list);
 fireEvent.change(screen.getByRole('textbox',{name:'创作目标'}),{target:{value:'比较两组'}});
 fireEvent.click(screen.getByRole('button',{name:'关闭素材面板'}));
 const send=await screen.findByRole('button',{name:'发送'});
 await waitFor(()=>expect(send).toBeEnabled());fireEvent.click(send);
 await screen.findByText('network lost');
 fireEvent.click(send);
 await waitFor(()=>expect(attempts).toHaveLength(2));
 expect(attempts[0].idempotency_key).toBeTruthy();
 expect(attempts[1].idempotency_key).toEqual(attempts[0].idempotency_key);
 expect(attempts[0].groups.map((g:any)=>g.id)).toEqual(['g1','g2']);
 expect(attempts[0].asset_ids).toEqual(['a','b']);
});

const yes={available:true,reason:''}, no={available:false,reason:'此型号不支持视频分析'};
const selectionCatalog=[
 {id:'deepseek',name:'DeepSeek',country:'中国',models:[{id:'deepseek-flash',name:'DeepSeek Flash',availability:{strategy:yes,image_input:yes,video_input:no,video_analysis:{...yes,route:'sampled_frames'},video_generation:no,embedding:no}}]},
 {id:'minimax',name:'MiniMax',country:'中国',models:[
  {id:'MiniMax-M3',name:'MiniMax M3',availability:{strategy:yes,image_input:yes,video_input:yes,video_analysis:{...yes,route:'native_video'},video_generation:no,embedding:no}},
  {id:'MiniMax-M2.7',name:'MiniMax M2.7',availability:{strategy:yes,image_input:no,video_input:no,video_analysis:no,video_generation:no,embedding:no}},
  {id:'MiniMax-H3',name:'MiniMax H3',availability:{image_input:no,video_input:no,video_generation:yes,embedding:no}}]}
];
async function openSources(){await screen.findByRole('textbox',{name:'创作目标'});fireEvent.click(screen.getByText('添加',{exact:true}));fireEvent.click(screen.getByRole('button',{name:'从素材库选择'}));}
function openFilters(){fireEvent.click(screen.getByText('筛选素材'));}
async function openContextParsing(){fireEvent.click(await screen.findByRole('button',{name:'模型安全历史'}));fireEvent.click(screen.getByRole('button',{name:'拆解画面'}));}
function setupSelection(models:any[]=[],assets:any[]=[],searchResults?:any[],parseIds?:string[]){
 const response=(v:unknown)=>new Response(JSON.stringify(v),{status:200});
 const fetcher=vi.fn(async(input:string)=>response(input.endsWith('/projects')?[{id:'A',name:'A'}]:input.endsWith('/catalog')?{providers:selectionCatalog}:input.endsWith('/search')?searchResults||assets:{project:{id:'A',name:'A',currency:'CNY'},budget:{},models,assets,notes:[],parses:[],templates:[],jobs:[],groups:[],instructions:[],sessions:[{id:'safety-session',title:'模型安全历史',mode:'directed',scope:{asset_ids:parseIds||assets.map(a=>a.id)},turns:[{question:'历史问题',answer:{summary:'历史回答',findings:[]},evidence:assets.filter(a=>!parseIds||parseIds.includes(a.id)).map(a=>({id:a.id,asset_id:a.id,title:a.title,provenance:a.media_type==='image'?'image':'sampled_frames'}))}]}]}));
 vi.stubGlobal('fetch',fetcher);render(<App/>);return fetcher;
}

test('settings retains usable analysis providers and retires generation configuration',async()=>{
 const fetcher=setupSelection();await screen.findByRole('textbox',{name:'创作目标'});fireEvent.click(screen.getByRole('button',{name:'设置'}));fireEvent.click(screen.getByRole('button',{name:'添加模型服务'}));
 expect(screen.queryByRole('combobox',{name:'用途'})).not.toBeInTheDocument();
 const providers=screen.getByRole('combobox',{name:'服务商'});expect(providers).toHaveValue('deepseek');
 expect(screen.queryByRole('option',{name:/MiniMax M2.7/})).not.toBeInTheDocument();
 expect(screen.getByRole('combobox',{name:'模型'})).toHaveValue('deepseek-flash');
 fireEvent.change(screen.getByLabelText('API 密钥 · 保存到系统密钥库'),{target:{value:'test-key'}});
 fireEvent.click(screen.getByRole('button',{name:'保存模型设置'}));await waitFor(()=>expect(fetcher.mock.calls.some(([u])=>u.endsWith('/models'))).toBe(true));
 expect(screen.queryByRole('tab',{name:'视频生成'})).not.toBeInTheDocument();
 expect(screen.queryByRole('option',{name:/MiniMax H3/})).not.toBeInTheDocument();
});

test('adding video invalidates an image-only analysis choice in both analysis and parsing',async()=>{
 setupSelection([{id:'img',role:'image_input',has_key:true,label:'图片模型',availability:{image_input:yes,video_input:no,video_analysis:no}}],[
  {id:'a',title:'测试图片',source_kind:'own',media_type:'image',metadata:{},inspection:'ready'},
  {id:'v',title:'测试视频',source_kind:'own',media_type:'video',metadata:{},inspection:'ready'}]);
 await openSources();fireEvent.click(await screen.findByRole('checkbox',{name:'选择素材：测试图片'}));
 fireEvent.click(screen.getByRole('button',{name:'关闭素材面板'}));
 fireEvent.change(screen.getByRole('textbox',{name:'创作目标'}),{target:{value:'比较开头'}});
 await waitFor(()=>expect(screen.getByRole('button',{name:'发送'})).toBeEnabled());
 await openSources();fireEvent.click(screen.getByRole('checkbox',{name:'选择素材：测试视频'}));fireEvent.click(screen.getByRole('button',{name:'关闭素材面板'}));
 expect(screen.getByRole('button',{name:'发送'})).toBeDisabled();
 expect(screen.getByText('此型号不支持视频分析')).toBeInTheDocument();
 expect(screen.queryByRole('checkbox',{name:'原生视频观察'})).not.toBeInTheDocument();
 await openContextParsing();expect(screen.getByRole('button',{name:'开始拆解'})).toBeDisabled();
});

test('editing a model for another provider requires its own key',async()=>{
 setupSelection([{id:'old',provider:'deepseek',model_id:'deepseek-flash',role:'image_input',has_key:true,label:'旧图片模型',availability:{image_input:yes,video_input:no,video_analysis:no}}]);
 await screen.findByRole('textbox',{name:'创作目标'});fireEvent.click(screen.getByRole('button',{name:'设置'}));
 fireEvent.click(await screen.findByRole('button',{name:'更换'}));
 expect(screen.getByRole('combobox',{name:'服务商'})).toBeDisabled();
 fireEvent.click(screen.getByRole('button',{name:'取消编辑'}));fireEvent.click(screen.getByRole('button',{name:'添加模型服务'}));
 fireEvent.change(screen.getByRole('combobox',{name:'服务商'}),{target:{value:'minimax'}});
 expect(screen.getByLabelText('API 密钥 · 保存到系统密钥库')).toHaveValue('');
 expect(screen.getByRole('button',{name:'保存模型设置'})).toBeDisabled();
});

test('parsing uses selected assets even if strategy discovery includes videos',async()=>{
 setupSelection([{id:'img',role:'image_input',has_key:true,label:'图片模型',availability:{image_input:yes,video_input:no,video_analysis:no}}],[
  {id:'a',title:'测试图片',source_kind:'own',media_type:'image',metadata:{},inspection:'ready'},
  {id:'v',title:'测试视频',source_kind:'own',media_type:'video',metadata:{},inspection:'ready'}],undefined,['a']);
 await openSources();openFilters();
 await screen.findByRole('checkbox',{name:'选择素材：测试图片'});
 fireEvent.click(screen.getByRole('checkbox',{name:'在当前筛选范围发现素材'}));
 fireEvent.click(screen.getByRole('checkbox',{name:'选择素材：测试图片'}));
 await openContextParsing();
 await waitFor(()=>expect(screen.getByRole('button',{name:'开始拆解'})).toBeEnabled());
});

test('discovery bases video eligibility on the resolved scope rather than the whole project',async()=>{
 const image={id:'a',title:'测试图片',source_kind:'own',media_type:'image',metadata:{},inspection:'ready'};
 const fetcher=setupSelection([{id:'img',role:'image_input',has_key:true,label:'图片模型',availability:{image_input:yes,video_input:no,video_analysis:no}}],[
  image,{id:'v',title:'范围外的视频',source_kind:'own',media_type:'video',metadata:{},inspection:'ready'}],[image]);
 await openSources();openFilters();
 await screen.findByRole('checkbox',{name:'选择素材：测试图片'});
 fireEvent.change(screen.getByRole('textbox',{name:'搜索素材'}),{target:{value:'测试图片'}});
 fireEvent.click(screen.getByRole('checkbox',{name:'在当前筛选范围发现素材'}));
 fireEvent.click(screen.getByRole('button',{name:'关闭素材面板'}));fireEvent.change(screen.getByRole('textbox',{name:'创作目标'}),{target:{value:'比较所选图片'}});await waitFor(()=>expect(screen.getByRole('button',{name:'发送'})).toBeEnabled());
 expect(fetcher.mock.calls.some(([u])=>u.endsWith('/search'))).toBe(true);
});

test('mode drafts are separate and presets never send or widen the selected scope',async()=>{
 const fetcher=setupSelection();const input=await screen.findByRole('textbox',{name:'创作目标'});
 fireEvent.change(input,{target:{value:'写稿草稿'}});fireEvent.click(screen.getByRole('button',{name:'拆视频'}));expect(input).toHaveValue('');
 fireEvent.click(screen.getByRole('button',{name:'看看这条视频的开头为什么抓人。'}));fireEvent.change(input,{target:{value:(input as HTMLTextAreaElement).value+' 口腔护理'}});
 expect(fetcher.mock.calls.filter(([u])=>u.endsWith('/actions'))).toHaveLength(0);
 fireEvent.click(screen.getByRole('button',{name:'写脚本'}));expect(input).toHaveValue('写稿草稿');
 fireEvent.click(screen.getByRole('button',{name:'拆视频'}));expect(input).toHaveValue('看看这条视频的开头为什么抓人。 口腔护理');
});

test('drafts and proposed scope do not leak across projects',async()=>{
 const response=(v:any)=>new Response(JSON.stringify(v),{status:200});
 vi.stubGlobal('fetch',vi.fn(async(input:string)=>response(input.endsWith('/projects')?[{id:'A',name:'A'},{id:'B',name:'B'}]:input.endsWith('/catalog')?{providers:[]}:{project:{id:input.includes('/B/')?'B':'A',currency:'CNY'},budget:{},models:[],assets:[],notes:[],parses:[],templates:[],jobs:[],groups:[],instructions:[],sessions:[]})));
 render(<App/>);await screen.findByRole('textbox',{name:'创作目标'});
 fireEvent.change(screen.getByRole('textbox',{name:'创作目标'}),{target:{value:'A私有草稿'}});
 fireEvent.change(screen.getByRole('combobox',{name:'当前项目'}),{target:{value:'B'}});
 await waitFor(()=>expect(screen.getByRole('textbox',{name:'创作目标'})).toHaveValue(''));
 fireEvent.change(screen.getByRole('textbox',{name:'创作目标'}),{target:{value:'B草稿'}});
 fireEvent.change(screen.getByRole('combobox',{name:'当前项目'}),{target:{value:'A'}});
 await waitFor(()=>expect(screen.getByRole('textbox',{name:'创作目标'})).toHaveValue('A私有草稿'));
});

test('selecting a mention updates actual scope and date basis sent to search',async()=>{
 const fetcher=setupSelection();await screen.findByRole('textbox',{name:'创作目标'});
 const input=screen.getByRole('textbox',{name:'创作目标'});
 fireEvent.change(input,{target:{value:'@成',selectionStart:2}});
 fireEvent.keyDown(input,{key:'Enter'});
 await waitFor(()=>expect(fetcher.mock.calls.some(([u,init]:any)=>u.endsWith('/search')&&JSON.parse(init.body).scope.source_kinds.join(',')==='finished')).toBe(true));
 expect(screen.getByRole('button',{name:'移除范围：成片'})).toBeInTheDocument();
 await openSources();openFilters();
 fireEvent.change(screen.getByRole('combobox',{name:'日期依据'}),{target:{value:'imported'}});
 await waitFor(()=>expect(fetcher.mock.calls.some(([u,init]:any)=>u.endsWith('/search')&&JSON.parse(init.body).scope.date_basis==='imported')).toBe(true));
 expect(screen.getByRole('button',{name:'收藏参考'})).toBeEnabled();
 expect(screen.getByRole('option',{name:'行业素材'})).toBeEnabled();
});

test('removing last source mention leaves an empty scope instead of all-project discovery',async()=>{
 setupSelection();await screen.findByRole('textbox',{name:'创作目标'});
 const input=screen.getByRole('textbox',{name:'创作目标'});
 fireEvent.change(input,{target:{value:'@成',selectionStart:2}});fireEvent.keyDown(input,{key:'Enter'});
 fireEvent.click(await screen.findByRole('button',{name:'移除范围：成片'}));
 await openSources();openFilters();
 expect(screen.getByRole('checkbox',{name:'在当前筛选范围发现素材'})).not.toBeChecked();
});

test('source plus group uses resolved intersection for modality and submitted groups',async()=>{
 const response=(v:any)=>new Response(JSON.stringify(v),{status:200});const calls:any[]=[];
 const a={id:'a',title:'成片图片',source_kind:'finished',media_type:'image',metadata:{},inspection:'ready'};
 const v={id:'v',title:'分组内范围外视频',source_kind:'raw',media_type:'video',metadata:{},inspection:'ready'};
 const state={project:{id:'A',currency:'CNY'},budget:{},models:[{id:'img',role:'image_input',has_key:true,label:'图片模型',availability:{image_input:yes,video_input:no,video_analysis:no}}],assets:[a,v],groups:[{id:'g',title:'混合组',asset_ids:['a','v']}],sessions:[],notes:[],parses:[],templates:[],jobs:[],instructions:[]};
 vi.stubGlobal('fetch',vi.fn(async(input:string,init:any)=>{if(input.endsWith('/projects'))return response([{id:'A',name:'A'}]);if(input.endsWith('/catalog'))return response({providers:[]});if(input.endsWith('/search'))return response([a]);if(input.endsWith('/actions')){calls.push(JSON.parse(init.body));return response({id:'j'})}return response(state)}));
 render(<App/>);await openSources();await screen.findByRole('option',{name:'混合组 · 2'});
 const input=screen.getByRole('textbox',{name:'创作目标'});
 fireEvent.change(input,{target:{value:'@成',selectionStart:2}});fireEvent.keyDown(input,{key:'Enter'});
 fireEvent.change(input,{target:{value:'@混',selectionStart:2}});fireEvent.keyDown(input,{key:'Enter'});
 fireEvent.click(screen.getByRole('button',{name:'关闭素材面板'}));await waitFor(()=>expect(screen.getByRole('button',{name:'发送'})).toBeEnabled());
 fireEvent.click(screen.getByRole('button',{name:'发送'}));
 await waitFor(()=>expect(calls).toHaveLength(1));
 expect(calls[0].groups[0].asset_ids).toEqual(['a']);
 expect(calls[0].scope.group_ids).toEqual(['g']);
 expect(calls[0].scope.source_kinds).toEqual(['finished']);
});

function setupHistoryWorkspace(){
 const response=(v:any)=>new Response(JSON.stringify(v),{status:200});const calls:any[]=[];
 const a={id:'a',title:'会话素材',source_kind:'own',media_type:'image',metadata:{},inspection:'ready'};
 const b={id:'b',title:'独立素材',source_kind:'own',media_type:'image',metadata:{},inspection:'ready'};
 const state={project:{id:'A',currency:'CNY'},budget:{},models:[{id:'img',role:'image_input',has_key:true,label:'图片模型',availability:{image_input:yes,video_input:no,video_analysis:no}}],assets:[a,b],groups:[],sessions:[{id:'s',title:'历史会话',mode:'directed',scope:{asset_ids:['a']},turns:[{question:'old',answer:{summary:'old',findings:[{type:'hypothesis',text:'H',evidence_ids:['a']}]},evidence:[{id:'a',asset_id:'a',title:'会话素材',provenance:'image'}]}]}],notes:[],parses:[],templates:[],jobs:[],instructions:[]};
 vi.stubGlobal('fetch',vi.fn(async(input:string,init:any)=>{if(input.endsWith('/projects'))return response([{id:'A',name:'A'}]);if(input.endsWith('/catalog'))return response({providers:[]});if(input.endsWith('/actions')){calls.push(JSON.parse(init.body));return response({id:'job'})}return response(state)}));
 render(<App/>);return calls;
}

test('contextual parsing preserves the originating conversation and selected evidence',async()=>{
 const calls=setupHistoryWorkspace();await screen.findByRole('button',{name:'历史会话'},{timeout:5000});
 fireEvent.click(screen.getByRole('button',{name:'历史会话'}));fireEvent.click(screen.getByRole('button',{name:'拆解画面'}));
 fireEvent.change(screen.getByRole('combobox',{name:'解析模板'}),{target:{value:'preset:beauty'}});
 expect((screen.getByRole('textbox',{name:'解析字段'}) as HTMLTextAreaElement).value).toContain('肤质人群');
 await waitFor(()=>expect(screen.getByRole('button',{name:'开始拆解'})).toBeEnabled());fireEvent.click(screen.getByRole('button',{name:'开始拆解'}));
 await waitFor(()=>expect(calls).toHaveLength(1));
 expect(calls[0].asset_ids).toEqual(['a']);expect(calls[0].source_session_id).toBe('s');expect(calls[0].source_turn_index).toBe(0);
});

test('history and primary navigation cannot overwrite a saved mode draft',async()=>{
 setupHistoryWorkspace();await screen.findByRole('button',{name:'历史会话'},{timeout:5000});
 fireEvent.change(screen.getByRole('textbox',{name:'创作目标'}),{target:{value:'不能丢失的草稿'}});
 fireEvent.click(screen.getByRole('button',{name:'历史会话'}));
 fireEvent.click(screen.getByRole('button',{name:'拆解画面'}));fireEvent.click(screen.getByRole('button',{name:'关闭'}));
 fireEvent.click(screen.getByRole('button',{name:'全部创作'}));fireEvent.click(screen.getByRole('button',{name:'创作台'}));
 fireEvent.click(screen.getByRole('button',{name:'拆视频'}));fireEvent.click(screen.getByRole('button',{name:'写脚本'}));
 expect(screen.getByRole('textbox',{name:'创作目标'})).toHaveValue('不能丢失的草稿');
});

test('retired creation cannot expose generation controls or submit paid jobs',async()=>{
 const fetcher=setupSelection([{id:'gen1',role:'video_generation',has_key:true,label:'历史生成模型',availability:{video_generation:yes}}]);
 await screen.findByRole('option',{name:'A'});
 expect(screen.queryByRole('button',{name:'视频仿拍'})).not.toBeInTheDocument();
 expect(screen.queryByLabelText('视频生成模型')).not.toBeInTheDocument();
 expect(screen.queryByLabelText('客户授权的参考片段 HTTPS 链接')).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'设置'}));
 expect(screen.queryByRole('tab',{name:'视频生成'})).not.toBeInTheDocument();
 expect(fetcher.mock.calls.filter(([u])=>u.endsWith('/actions'))).toHaveLength(0);
});

test('team budget update requires explicit confirmation and sends the confirmation header',async()=>{
 const {setTeamMode}=await import('./api');setTeamMode(true);
 const confirm=vi.spyOn(window,'confirm').mockReturnValue(false);
 const fetcher=setupSelection();
 try{
  await screen.findByRole('option',{name:'A'});
  fireEvent.click(screen.getByRole('button',{name:'设置'}));
  fireEvent.click(screen.getByRole('button',{name:'用量与费用'}));
  fireEvent.change(screen.getByLabelText('费用上限'),{target:{value:'25'}});
  fireEvent.click(screen.getByRole('button',{name:'保存费用提醒'}));
  await waitFor(()=>expect(confirm).toHaveBeenCalled());
  expect(fetcher.mock.calls.filter(([u,init]:any)=>u.endsWith('/projects/A')&&init?.method==='PUT')).toHaveLength(0);
  confirm.mockReturnValue(true);
  fireEvent.click(screen.getByRole('button',{name:'保存费用提醒'}));
  await waitFor(()=>expect(fetcher.mock.calls.some(([u,init]:any)=>u.endsWith('/projects/A')&&init?.method==='PUT')).toBe(true));
  const call=fetcher.mock.calls.find(([u,init]:any)=>u.endsWith('/projects/A')&&init?.method==='PUT') as any;
  expect(new Headers(call[1].headers).get('X-Confirm-Budget-Change')).toBe('true');
 }finally{setTeamMode(false)}
});

test('migrated archived history stays visible without pending count or replay controls',async()=>{
 const response=(v:any)=>new Response(JSON.stringify(v),{status:200});
 const state={project:{id:'A',currency:'CNY'},budget:{},models:[],assets:[],groups:[],sessions:[],notes:[],parses:[],templates:[],instructions:[],jobs:[
  {id:'archived-task',kind:'generation',state:'archived',original_state:'submitting',replay_allowed:false,payload:{},quote:{amount:'1',currency:'CNY'},actual_cost:null,result:{url:'https://output.example/old.mp4'}}]};
 vi.stubGlobal('fetch',vi.fn(async(input:string)=>response(input.endsWith('/projects')?[{id:'A',name:'A'}]:input.endsWith('/catalog')?{providers:[]}:state)));
 render(<App/>);
 await screen.findByRole('textbox',{name:'创作目标'});fireEvent.click(screen.getByRole('button',{name:'设置'}));fireEvent.click(screen.getByRole('button',{name:'用量与费用'}));fireEvent.click(screen.getByRole('button',{name:'查看处理记录'}));
 expect(await screen.findByText('历史归档 · 不可重放')).toBeInTheDocument();
 expect(screen.getByText('archived-task')).toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'再次创作'})).not.toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'恢复未执行任务'})).not.toBeInTheDocument();
});

test('successful transfer followed by decoder failure is shown as failed in upload progress',async()=>{
 const response=(v:any)=>new Response(JSON.stringify(v),{status:200});
 const a={id:'failed',title:'bad.mp4',source_kind:'own',media_type:'video',inspection:'failed',metadata:{processing:{state:'failed'}}};
 const state={project:{id:'A',currency:'CNY'},budget:{},assets:[a],models:[],sessions:[],notes:[],instructions:[],parses:[],templates:[],jobs:[],groups:[]};
 vi.stubGlobal('fetch',vi.fn(async(input:string)=>response(input.endsWith('/projects')?[{id:'A',name:'A'}]:input.endsWith('/catalog')?{providers:[]}:input.includes('/assets?background=true')?a:state)));
 const {container}=render(<App/>);await screen.findByRole('textbox',{name:'创作目标'});
 fireEvent.change(container.querySelector('input[type=file][multiple]')!,{target:{files:[new File(['invalid video'],'bad.mp4',{type:'video/mp4'})]}});
 await screen.findByText('处理失败，在素材详情重试');
});
