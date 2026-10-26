import {afterEach,expect,test,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,within,waitFor} from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import App from './App';

afterEach(()=>{cleanup();sessionStorage.clear();localStorage.clear();vi.unstubAllGlobals();vi.restoreAllMocks()});
const video={id:'v',title:'洗衣液开头实拍',source_kind:'own',media_type:'video',inspection:'ready',metadata:{duration_ms:12000,reuse_allowed:false}};
const modeFor=(task:string)=>task==='拆视频'?'directed':task==='找参考'?'exploration':'inspiration';
const model={id:'m',provider:'deepseek',role:'strategy',has_key:true,label:'测试连接',availability:{strategy:{available:true},video_analysis:{available:true},image_input:{available:true}}};
function setup(options:{assets?:any[],models?:any[],drafts?:any[],active?:string}={}){
 const calls:{path:string,body:any}[]=[];let drafts=options.drafts||[];
 const state={project:{id:'A',name:'项目A',currency:'CNY'},budget:{},models:options.models||[],assets:options.assets||[video],
  groups:[{id:'current-group',title:'当前素材组',asset_ids:['current']}],instructions:[],jobs:[],parses:[],templates:[],
  product_facts:{product:'洗衣液',product_id:'product',sku_id:'500ml'},notes:[{id:'n',title:'已保存笔记',markdown:'我的真实笔记'}],
  scripts:[{id:'s',title:'已保存脚本',version:2,product_id:'product',sku_id:'500ml',rows:[]}],
  sessions:[{id:'h',title:'上次分析',mode:'directed',scope:{asset_ids:[]},turns:[{question:'之前的问题',answer:{summary:'历史回答',findings:[]},evidence:[]}]}]};
 const response=(value:any)=>new Response(JSON.stringify(value));
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{
  const path=String(input);const body=init?.body?JSON.parse(String(init.body)):null;calls.push({path,body});
  if(path.endsWith('/outcomes'))return response({items:[...state.notes.map(row=>({...row,kind:'note',version:1})),...state.scripts.map(row=>({...row,kind:'script'}))]});
  if(path.endsWith('/projects'))return response([{id:'A',name:'项目A'}]);
  if(path.endsWith('/catalog'))return response({providers:[]});
  if(path.endsWith('/preferences'))return response({default_binding_id:state.models[0]?.id||''});
  if(path.endsWith('/state'))return response(state);
  if(path.endsWith('/drafts'))return response({drafts,active_identity:options.active||null});
  if(path.includes('/drafts/')&&body){const saved={identity:path.split('/').at(-1),version:body.expected_version+1,content:body.content};drafts=[saved,...drafts.filter(d=>d.identity!==saved.identity)];return response(saved)}
  if(path.endsWith('/search')){const scope=body.scope;return response(state.assets.filter(a=>(!scope.source_kinds?.length||scope.source_kinds.includes(a.source_kind))&&(scope.discovery||scope.asset_ids.includes(a.id)||scope.group_ids.includes('current-group')&&a.id==='current')))}
  if(path.endsWith('/actions'))return response({id:'job',state:'queued'});
  if(path.endsWith('/scripts'))return response(state.scripts);
  if(path.endsWith('/production')||path.endsWith('/references')||path.endsWith('/plans')||/\/(scenes|frames)$/.test(path))return response([]);
  if(path.endsWith('/cmm'))return response({connection:null,capabilities:{},usage:{},jobs:[],tariffs:{}});
  if(path.endsWith('/browser-bridge/connections'))return response({connections:[]});
  return response({});
 }));render(<App/>);return calls;
}
async function ready(){await screen.findByRole('textbox',{name:'创作目标'});expect(screen.getByRole('button',{name:'上次分析'})).toBeInTheDocument()}
async function library(){fireEvent.click(screen.getByText('添加',{exact:true}));fireEvent.click(screen.getByRole('button',{name:'从素材库选择'}));await screen.findByRole('dialog',{name:'选择本次素材'})}

test('workbench has two primary destinations, task entries and editable product context',async()=>{
 setup();await ready();expect(within(screen.getByRole('navigation',{name:'主导航'})).getAllByRole('button').map(b=>b.textContent)).toEqual(['创作台','我的素材']);
 for(const name of ['找参考','拆视频','写脚本'])expect(screen.getByRole('button',{name})).toBeInTheDocument();
 expect(screen.getByText('当前商品：洗衣液')).toBeInTheDocument();expect(screen.getByRole('button',{name:'编辑商品'})).toBeEnabled();
 expect(screen.getByRole('combobox',{name:'分析方式'})).toBeInTheDocument();expect(screen.queryByLabelText('分析模型')).not.toBeInTheDocument();expect(screen.queryByLabelText('分析费用预留')).not.toBeInTheDocument();
});

test('missing model offers a direct connection and material organization remains available',async()=>{
 setup();await ready();fireEvent.click(screen.getByRole('button',{name:'连接模型'}));expect(screen.getByRole('button',{name:'模型连接'})).toHaveAttribute('aria-current','page');
 fireEvent.click(screen.getByRole('button',{name:'关闭'}));fireEvent.click(screen.getByRole('button',{name:'我的素材'}));
 expect(await screen.findByRole('checkbox',{name:'选择素材：洗衣液开头实拍'})).toBeEnabled();expect(screen.getByRole('checkbox',{name:'自有可用素材：洗衣液开头实拍'})).toBeDisabled();
});

test('material page and composer share selection, count, removal and preview',async()=>{
 setup();await ready();fireEvent.click(screen.getByRole('button',{name:'我的素材'}));fireEvent.click(await screen.findByRole('checkbox',{name:'选择素材：洗衣液开头实拍'}));
 fireEvent.click(screen.getByRole('button',{name:'创作台'}));const strip=screen.getByRole('region',{name:'本次素材'});expect(strip).toHaveTextContent('已选 1 条');
 fireEvent.click(within(strip).getByRole('button',{name:'查看素材：洗衣液开头实拍'}));expect(screen.getByRole('dialog',{name:'洗衣液开头实拍'})).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'关闭'}));fireEvent.click(screen.getByRole('button',{name:'移除素材：洗衣液开头实拍'}));expect(screen.queryByRole('region',{name:'本次素材'})).not.toBeInTheDocument();
});

test('task drafts and editable presets never send or replace an explicit goal without consent',async()=>{
 const calls=setup();await ready();const input=screen.getByRole('textbox',{name:'创作目标'});fireEvent.change(input,{target:{value:'手写目标'}});
 fireEvent.click(screen.getByRole('button',{name:'拆视频'}));expect(input).toHaveValue('');fireEvent.click(screen.getByRole('button',{name:'看看这条视频的开头为什么抓人。'}));
 expect(input).toHaveValue('看看这条视频的开头为什么抓人。');fireEvent.click(screen.getByRole('button',{name:'写脚本'}));expect(input).toHaveValue('手写目标');
 expect(calls.some(c=>c.path.endsWith('/actions'))).toBe(false);expect(screen.queryByRole('region',{name:'本次素材'})).not.toBeInTheDocument();
});

test('saved notes and scripts remain reachable and navigation restores the unsent goal',async()=>{
 setup();await ready();fireEvent.change(screen.getByRole('textbox',{name:'创作目标'}),{target:{value:'待继续的草稿'}});
 fireEvent.click(screen.getByRole('button',{name:'全部创作'}));expect(await screen.findByText('已保存笔记')).toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:'脚本'}));
 expect(await screen.findByRole('button',{name:'已保存脚本 · v2'})).toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:'创作台'}));expect(screen.getByRole('textbox',{name:'创作目标'})).toHaveValue('待继续的草稿');
});

test('opening different-mode history does not replace the unsent draft',async()=>{
 setup();await ready();fireEvent.change(screen.getByRole('textbox',{name:'创作目标'}),{target:{value:'不能被历史覆盖'}});fireEvent.click(screen.getByRole('button',{name:'上次分析'}));
 expect(screen.getByText('历史回答')).toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:'创作台'}));expect(screen.getByRole('textbox',{name:'创作目标'})).toHaveValue('不能被历史覆盖');
});

test('removing a discovered material fixes the remaining explicit scope without widening it',async()=>{
 const second={...video,id:'v2',title:'另一条实拍'};const calls=setup({assets:[video,second]});await ready();await library();fireEvent.click(screen.getByText('筛选素材'));
 fireEvent.change(screen.getByRole('combobox',{name:'素材来源'}),{target:{value:'own'}});fireEvent.click(screen.getByRole('checkbox',{name:'在当前筛选范围发现素材'}));fireEvent.click(screen.getByRole('button',{name:'关闭素材面板'}));
 await waitFor(()=>expect(screen.getByRole('region',{name:'本次素材'})).toHaveTextContent('已选 2 条'));fireEvent.click(screen.getByRole('button',{name:'移除素材：洗衣液开头实拍'}));
 await waitFor(()=>expect(screen.getByRole('region',{name:'本次素材'})).toHaveTextContent('已选 1 条'));
 expect(calls.filter(c=>c.path.endsWith('/search')).at(-1)!.body.scope).toMatchObject({asset_ids:['v2'],discovery:false,source_kinds:['own']});
});

test.each([['写脚本',false],['写脚本',true],['找参考',false],['找参考',true],['拆视频',false],['拆视频',true]] as const)('task %s preserves current material context; previous target draft=%s',async(task,previous)=>{
 const current={...video,id:'current',title:'当前商品原片',metadata:{duration_ms:12000,reuse_allowed:true}},other={...video,id:'other',title:'旧任务原片'};
 const target=modeFor(task),start=target==='directed'?'inspiration':'directed';
 const context={selected:['current'],selectedSegments:[{asset_id:'current',start_ms:1000,end_ms:4000,observation:'选中台词',provenance:'subtitle'}],ownAssetIds:['current'],referenceIds:['ref-current'],activeGroups:['current-group'],scopeSources:['own'],source:'own',dateBasis:'imported',startDate:'2026-10-01',endDate:'2026-10-20',account:'current-account',minSpend:'5',minRoas:'2',useHistory:true};
 const drafts=[{identity:start,version:1,content:{...context,analysisMode:start,question:'当前任务的草稿'}},...(previous?[{identity:target,version:1,content:{analysisMode:target,question:'目标任务原来的草稿',selected:['other'],referenceIds:[],activeGroups:[]}}]:[])];
 const calls=setup({assets:[current,other],models:[model],drafts,active:start});await ready();await waitFor(()=>expect(screen.getByRole('region',{name:'本次素材'})).toHaveTextContent('已选 2 条'));
 fireEvent.click(screen.getByRole('button',{name:task}));expect(screen.getByRole('textbox',{name:'创作目标'})).toHaveValue(previous?'目标任务原来的草稿':'');
 expect(screen.getByRole('region',{name:'本次素材'})).toHaveTextContent('当前商品原片');expect(screen.queryByRole('button',{name:'查看素材：旧任务原片'})).not.toBeInTheDocument();
 fireEvent.change(screen.getByRole('textbox',{name:'创作目标'}),{target:{value:'比较当前选定素材'}});
 if(task==='找参考'){
  fireEvent.click(screen.getByRole('button',{name:'搜索收藏'}));await screen.findByRole('tab',{name:'我的收藏'});expect(calls.some(c=>c.path.endsWith('/actions'))).toBe(false);
 }else{
  const send=screen.getByRole('button',{name:'发送'});await waitFor(()=>expect(send).toBeEnabled());fireEvent.click(send);await waitFor(()=>expect(calls.some(c=>c.path.endsWith('/actions'))).toBe(true));
  expect(calls.find(c=>c.path.endsWith('/actions'))!.body).toMatchObject({asset_ids:['current'],own_asset_ids:['current'],history_script_ids:['s'],groups:[expect.objectContaining({id:'current-group'})],scope:{reference_ids:['ref-current'],date_basis:'imported',start_date:'2026-10-01',end_date:'2026-10-20',account_ids:['current-account'],min_spend:'5',min_roas:'2',selected_segments:[context.selectedSegments[0]]}});
 }
 expect(calls.some(c=>c.path.includes('/cmm/searches')||c.path.includes('/cmm/jobs'))).toBe(false);
});

test('restored manual revision keeps its saved script identity when sent',async()=>{const calls=setup({assets:[],models:[model],drafts:[{identity:'inspiration',version:1,content:{analysisMode:'inspiration',question:'换个开头',revisionContext:{script_id:'manual',version:4},selected:[]}}],active:'inspiration'});await ready();const send=screen.getByRole('button',{name:'发送'});await waitFor(()=>expect(send).toBeEnabled());fireEvent.click(send);await waitFor(()=>expect(calls.some(c=>c.path.endsWith('/actions'))).toBe(true));expect(calls.find(c=>c.path.endsWith('/actions'))!.body.script_revision).toEqual({script_id:'manual',version:4});});

test('choosing a new task clears a restored revision baseline',async()=>{const calls=setup({assets:[],models:[model],drafts:[{identity:'inspiration',version:1,content:{analysisMode:'inspiration',question:'换个开头',revisionContext:{script_id:'manual',version:4},selected:[]}}],active:'inspiration'});await ready();fireEvent.click(screen.getByRole('button',{name:'写脚本'}));fireEvent.change(screen.getByRole('textbox',{name:'创作目标'}),{target:{value:'解释一下这个问题'}});const send=screen.getByRole('button',{name:'发送'});await waitFor(()=>expect(send).toBeEnabled());fireEvent.click(send);await waitFor(()=>expect(calls.some(c=>c.path.endsWith('/actions'))).toBe(true));expect(calls.find(c=>c.path.endsWith('/actions'))!.body.script_revision).toBeNull();});
