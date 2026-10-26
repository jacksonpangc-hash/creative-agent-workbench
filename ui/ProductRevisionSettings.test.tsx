import {afterEach,expect,test,vi} from 'vitest';
import {act,cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import App from './App';
import SettingsWorkspace from './settings/SettingsWorkspace';
import ModelServiceSettings from './settings/ModelServiceSettings';
import {CmmSettings} from './industry/CmmWorkspace';

const response=(value:unknown)=>new Response(JSON.stringify(value),{status:200});
const available={available:true};
const catalog=[{id:'example',name:'示例服务',country:'测试',models:[{id:'vision',name:'视觉模型',availability:{video_analysis:available,strategy:available}}]}];
const model={id:'binding',provider:'example',model_id:'vision',role:'strategy',has_key:true,label:'我的模型',availability:{video_analysis:available}};
const cmm=(connection:any=null,usage:any={})=>({connection,usage,capabilities:{},jobs:[],tariffs:{}});
const workspace={project:{id:'A',name:'项目A',budget:'50',currency:'CNY'},budget:{limit:'50',reserved:'12',spent:'3',currency:'CNY'},models:[],assets:[],notes:[],parses:[],templates:[],jobs:[],groups:[],instructions:[],sessions:[]};
afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.restoreAllMocks()});

test('settings opens just model services and five named sections reveal their own controls',async()=>{
 const reads:string[]=[];
 vi.stubGlobal('fetch',vi.fn(async(input:string)=>{reads.push(input);return response(input.endsWith('/projects')?[{id:'A',name:'项目A'}]:input.endsWith('/catalog')?{providers:catalog}:input.endsWith('/preferences')?{default_quote:'1',version:2}:input.endsWith('/cmm')?cmm():input.endsWith('/license/status')?{mode:'internal',can_work:true}:workspace)}));
 render(<App/>);await screen.findByRole('option',{name:'项目A'});fireEvent.click(screen.getByRole('button',{name:'设置'}));
 const nav=screen.getByRole('navigation',{name:'设置分区'});
 expect(within(nav).getAllByRole('button').map(b=>b.textContent)).toEqual(['模型连接','参考来源','用量与费用','账号','本地资料']);
 expect(within(nav).getByRole('button',{name:'模型连接'})).toHaveAttribute('aria-current','page');
 expect(screen.getByRole('region',{name:'模型服务'})).toBeInTheDocument();
 expect(screen.queryByRole('region',{name:'行业数据设置'})).not.toBeInTheDocument();
 expect(reads.some(path=>path.endsWith('/license/status')||path.endsWith('/cmm'))).toBe(false);
 fireEvent.click(within(nav).getByRole('button',{name:'参考来源'}));
 expect(await screen.findByRole('region',{name:'行业数据设置'})).toBeInTheDocument();
 expect(screen.queryByRole('region',{name:'模型服务'})).not.toBeInTheDocument();
 fireEvent.click(within(nav).getByRole('button',{name:'用量与费用'}));
 expect(screen.getByRole('textbox',{name:'费用上限'})).toHaveValue('50');
 expect(screen.getByText('待确认占用')).toBeInTheDocument();
 expect(screen.getByText('12 CNY')).toBeInTheDocument();
 expect(screen.getByText('已确认费用')).toBeInTheDocument();
 expect(screen.getByText('3 CNY')).toBeInTheDocument();
 fireEvent.click(within(nav).getByRole('button',{name:'账号'}));
 expect(await screen.findByText('内部开发模式')).toBeInTheDocument();
 fireEvent.click(within(nav).getByRole('button',{name:'本地资料'}));
 expect(screen.getByRole('button',{name:'导出创作记录'})).toBeInTheDocument();
 expect(screen.getByText('不包含视频文件和密钥。')).toBeInTheDocument();
 expect(screen.getByRole('button',{name:'关闭'})).toBeInTheDocument();
});

test('a saved model key hides the empty input until replacement and keeps the binding on save',async()=>{
 const writes:any[]=[];vi.stubGlobal('fetch',vi.fn(async(input:string,init:any)=>{writes.push({input,payload:JSON.parse(init.body)});return response({})}));
 render(<ModelServiceSettings base="/projects/A" models={[model]} catalog={catalog} run={(fn:any)=>fn()} reload={vi.fn()}/>);
 expect(screen.getByText(/已保存，首次使用时检查连接/)).toBeInTheDocument();
 expect(screen.queryByLabelText('API 密钥 · 保存到系统密钥库')).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'更换'}));
 fireEvent.click(screen.getByRole('button',{name:'更换 API Key'}));
 fireEvent.change(screen.getByLabelText('API 密钥 · 保存到系统密钥库'),{target:{value:'fixture-replacement'}});
 fireEvent.click(screen.getByRole('button',{name:'保存模型设置'}));
 await waitFor(()=>expect(writes).toHaveLength(1));
 expect(writes[0]).toEqual({input:expect.stringContaining('/projects/A/models'),payload:expect.objectContaining({id:'binding',provider:'example',api_key:'fixture-replacement'})});
 expect(screen.queryByLabelText('API 密钥 · 保存到系统密钥库')).not.toBeInTheDocument();
 expect(screen.queryByText('已连接')).not.toBeInTheDocument();
});

test('a saved CMM key is hidden until replacement and permissions alone are not connectivity',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>response(cmm({status:'permissions_checked',revision:'current',has_key:true,live_verified:false}))));
 render(<CmmSettings base="/projects/A"/>);
 expect(await screen.findByText('密钥已保存，待验证')).toBeInTheDocument();
 expect(screen.queryByLabelText('蝉妈妈 API Key')).not.toBeInTheDocument();
 expect(screen.queryByText('已连接',{exact:true})).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'更换'}));
 expect(screen.getByLabelText('蝉妈妈 API Key')).toHaveValue('');
});

test('current verified connection remains connected while unknown charges exhaust the local cap',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>response(cmm({status:'permissions_checked',revision:'current',has_key:true,live_verified:false,permission_checked_at:'2026-10-24T01:00:00Z'},{limit_points:'30',reserved_points:'30',remaining_points:'0',actual_points:null,billing_verified:false}))));
 render(<CmmSettings base="/projects/A"/>);
 expect(await screen.findByText('已连接',{exact:true})).toBeInTheDocument();
 expect(screen.getByText('费用确认中，暂时无法发起新查询')).toBeInTheDocument();
 expect(screen.getByText(/本项目累计上限 30 积分/)).toHaveTextContent(/实际扣费.*待核账/);
 expect(screen.getByText(/本项目累计上限 30 积分/)).toHaveTextContent('待确认占用 30 积分 · 可新增暂占额度 0 积分');
 expect(screen.queryByText(/已消费 30|断连/)).not.toBeInTheDocument();
});

test('an old successful receipt cannot verify a replacement key or grant current capabilities',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>response({...cmm({status:'unverified',revision:'new',has_key:true,live_verified:false},{limit_points:'30',reserved_points:'30',remaining_points:'0',actual_points:null}),jobs:[{id:'old',operation:'video_detail_info',state:'succeeded',connection_revision:'old',live_verified:true,calls_attempted:1,max_calls:1,reserved_points:'30'}]})));
 render(<CmmSettings base="/projects/A"/>);
 expect(await screen.findByText('密钥已保存，待验证')).toBeInTheDocument();
 expect(screen.queryByText('已连接',{exact:true})).not.toBeInTheDocument();
 expect(screen.getByText('视频详情 · 未确认')).toBeInTheDocument();
 expect(screen.getByText('费用确认中，暂时无法发起新查询')).toBeInTheDocument();
});

test('missing usage never becomes zero or an exhausted-budget claim',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>response(cmm({status:'unverified',has_key:true},{}))));
 render(<CmmSettings base="/projects/A"/>);await screen.findByText('密钥已保存，待验证');
 fireEvent.click(screen.getByText('积分与使用记录'));
 expect(screen.getByText(/^蝉妈妈 AI 积分单独记账。暂占额度 未确认 积分/)).toBeInTheDocument();
 expect(screen.queryByText(/暂占额度 0 积分/)).not.toBeInTheDocument();
 expect(screen.queryByText('费用确认中，暂时无法发起新查询')).not.toBeInTheDocument();
});

test('switching projects immediately removes previous CMM state and ignores late results',async()=>{
 let finishB!:(value:Response)=>void;const pendingB=new Promise<Response>(r=>{finishB=r});
 vi.stubGlobal('fetch',vi.fn(async(input:string)=>input.includes('/B/')?pendingB:response(cmm({status:'permissions_checked',has_key:true,live_verified:false,permission_checked_at:'2026-10-24T01:00:00Z'}))));
 const {rerender}=render(<CmmSettings base="/projects/A"/>);await screen.findByText('已连接',{exact:true});
 fireEvent.click(screen.getByRole('button',{name:'更换'}));fireEvent.change(screen.getByLabelText('蝉妈妈 API Key'),{target:{value:'project-a-draft'}});
 rerender(<CmmSettings base="/projects/B"/>);
 expect(screen.queryByText('已连接',{exact:true})).not.toBeInTheDocument();
 expect(screen.queryByDisplayValue('project-a-draft')).not.toBeInTheDocument();
 rerender(<CmmSettings base="/projects/C"/>);
 await act(async()=>{finishB(response(cmm({status:'unverified',label:'B-private',has_key:true})));await pendingB});
 expect(screen.queryByText('B-private')).not.toBeInTheDocument();
 await screen.findByText('已连接',{exact:true});
});

test('section links can open usage directly and project changes discard an unsaved budget',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>response(cmm())));
 const props={data:workspace,catalog,run:async(fn:()=>Promise<void>)=>fn(),reload:async()=>{},onPreferences:vi.fn()};
 const {rerender}=render(<SettingsWorkspace {...props} base="/projects/A" initialSection="usage"/>);
 fireEvent.change(screen.getByRole('textbox',{name:'费用上限'}),{target:{value:'999'}});
 rerender(<SettingsWorkspace {...props} base="/projects/B" data={{...workspace,project:{id:'B',name:'B',budget:'20',currency:'CNY'}}} initialSection="usage"/>);
 expect(screen.getByRole('textbox',{name:'费用上限'})).toHaveValue('20');
 rerender(<SettingsWorkspace {...props} base="/projects/B" initialSection="models"/>);
 expect(screen.getByRole('region',{name:'模型服务'})).toBeInTheDocument();
 expect(screen.queryByRole('textbox',{name:'费用上限'})).not.toBeInTheDocument();
});

test('settings focus wraps after the last disclosure so keyboard users can reach advanced controls',async()=>{
 // jsdom has no layout. Treat only elements outside closed disclosures as visible.
 vi.spyOn(HTMLElement.prototype,'getClientRects').mockImplementation(function(this:HTMLElement){
  const closed=this.closest('details:not([open])');
  return (closed&&!(this.tagName==='SUMMARY'&&this.parentElement===closed)?[]:[{width:1,height:1}]) as unknown as DOMRectList;
 });
 vi.stubGlobal('fetch',vi.fn(async(input:string)=>response(input.endsWith('/projects')?[{id:'A',name:'项目A'}]:input.endsWith('/catalog')?{providers:catalog}:input.endsWith('/preferences')?{default_quote:'1',version:2}:input.endsWith('/cmm')?cmm({status:'unverified',has_key:true}):workspace)));
 render(<App/>);await screen.findByRole('option',{name:'项目A'});fireEvent.click(screen.getByRole('button',{name:'设置'}));
 fireEvent.click(screen.getByRole('button',{name:'参考来源'}));
 const lastDisclosure=await screen.findByText('积分与使用记录');lastDisclosure.focus();
 fireEvent.keyDown(lastDisclosure,{key:'Tab'});
 expect(screen.getByRole('button',{name:'关闭'})).toHaveFocus();
 vi.restoreAllMocks();
});

test('a budget arriving after settings opens fills an untouched field without overwriting an edit',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>response(cmm())));
 const props={catalog,run:async(fn:()=>Promise<void>)=>fn(),reload:async()=>{},onPreferences:vi.fn(),base:'/projects/A',initialSection:'usage' as const};
 const {rerender}=render(<SettingsWorkspace {...props} data={{...workspace,project:{id:'A',name:'项目A',currency:'CNY'}}}/>);
 expect(screen.getByRole('textbox',{name:'费用上限'})).toHaveValue('');
 rerender(<SettingsWorkspace {...props} data={workspace}/>);
 expect(screen.getByRole('textbox',{name:'费用上限'})).toHaveValue('50');
 fireEvent.change(screen.getByRole('textbox',{name:'费用上限'}),{target:{value:'70'}});
 rerender(<SettingsWorkspace {...props} data={{...workspace,project:{...workspace.project,budget:'60'}}}/>);
 expect(screen.getByRole('textbox',{name:'费用上限'})).toHaveValue('70');
});

test('an older budget save preserves an edit made while its request is pending',async()=>{
 let finishSave!:(value:Response)=>void;const saveResponse=new Promise<Response>(r=>{finishSave=r});
 const writes:any[]=[];
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{
  if(init?.method==='PUT'){writes.push(JSON.parse(init.body as string));return saveResponse}
  return response(cmm());
 }));
 let refresh!:()=>void;let pendingRun!:Promise<void>;
 const props={catalog,run:(fn:()=>Promise<void>)=>{pendingRun=fn();return pendingRun},reload:async()=>{refresh()},onPreferences:vi.fn(),base:'/projects/A',initialSection:'usage' as const};
 const {rerender}=render(<SettingsWorkspace {...props} data={workspace}/>);
 refresh=()=>rerender(<SettingsWorkspace {...props} data={{...workspace,project:{...workspace.project,budget:'70'}}}/>);
 fireEvent.change(screen.getByRole('textbox',{name:'费用上限'}),{target:{value:'70'}});
 fireEvent.click(screen.getByRole('button',{name:'保存费用提醒'}));
 expect(writes[0].budget).toBe('70');
 fireEvent.change(screen.getByRole('textbox',{name:'费用上限'}),{target:{value:'80'}});
 await act(async()=>{finishSave(response({}));await pendingRun});
 expect(screen.getByRole('textbox',{name:'费用上限'})).toHaveValue('80');
 expect(screen.getByText(/当前修改尚未保存/)).toBeInTheDocument();
});

test('a replacement receipt invalidates old key evidence even when local state refresh fails',async()=>{
 const oldConnection={id:'connection',revision:'old',has_key:true,status:'permissions_checked',permission_checked_at:'2026-10-24T01:00:00Z',live_verified:false,capabilities:{video_detail_info:true},rights:{search:true}};
 const freshConnection={id:'connection',revision:'fresh',has_key:true,status:'unverified',live_verified:false,capabilities:{},rights:null};
 let readCount=0;const writes:any[]=[];
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{
  if(init?.method==='PUT'){writes.push({input,body:JSON.parse(init.body as string)});return response(freshConnection)}
  if(readCount++>0)throw new Error('本机状态刷新暂不可用');
  return response({...cmm(oldConnection,{limit_points:'30',reserved_points:'30',remaining_points:'0',actual_points:null}),capabilities:{video_detail_info:true}});
 }));
 render(<CmmSettings base="/projects/A"/>);
 await screen.findByText('已连接',{exact:true});
 expect(screen.getByText('视频详情 · 可用')).toBeInTheDocument();
 expect(screen.getByText('素材使用范围 · 已记录')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'更换'}));
 fireEvent.change(screen.getByLabelText('蝉妈妈 API Key'),{target:{value:'fixture-fresh-key'}});
 fireEvent.click(screen.getByRole('button',{name:'保存连接'}));
 await screen.findByRole('alert');
 expect(writes).toEqual([{input:expect.stringContaining('/projects/A/cmm/connection'),body:{api_key:'fixture-fresh-key'}}]);
 expect(screen.getByText('密钥已保存，待验证')).toBeInTheDocument();
 expect(screen.queryByText('已连接',{exact:true})).not.toBeInTheDocument();
 expect(screen.queryByText(/最近验证：/)).not.toBeInTheDocument();
 expect(screen.getByText('视频详情 · 未确认')).toBeInTheDocument();
 expect(screen.getByText('素材使用范围 · 首次使用前确认')).toBeInTheDocument();
 expect(screen.getByText(/本项目累计上限 30 积分/)).toHaveTextContent('待确认占用 30 积分 · 可新增暂占额度 0 积分');
 expect(screen.getByText('费用确认中，暂时无法发起新查询')).toBeInTheDocument();
});

test('capabilities without the accepted verification timestamp remain unconfirmed',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>response({...cmm({has_key:true,status:'permissions_checked',revision:'current'}),capabilities:{video_detail_info:true}})));
 render(<CmmSettings base="/projects/A"/>);
 await screen.findByText('密钥已保存，待验证');
 expect(screen.getByText('视频详情 · 未确认')).toBeInTheDocument();
 expect(screen.queryByText('视频详情 · 可用')).not.toBeInTheDocument();
});

test('usage settings open durable processing history without creating another task',async()=>{
 const open=vi.fn();vi.stubGlobal('fetch',vi.fn(async()=>response(cmm())));
 render(<SettingsWorkspace base="/projects/A" data={workspace} catalog={catalog} run={fn=>fn()} reload={async()=>{}} onPreferences={vi.fn()} initialSection="usage" onJobs={open}/>);
 fireEvent.click(screen.getByRole('button',{name:'查看处理记录'}));expect(open).toHaveBeenCalledOnce();
});
