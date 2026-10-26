import {afterEach, expect, test, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import IndustryWorkspace from './industry/IndustryWorkspace';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const permissions = {search:true, retain_metadata:true, download_media:false, retain_media:false, model_process:false,
  derivative_create:false, export_metadata:false, export_media:false, expires_at:'2099-01-01T00:00:00Z', evidence:'客户许可证'};
const catalog = [{provider:'datax',platform:'douyin',operation:'douyin.videos.search',label:'DataX 抖音视频关键词搜索',category_filter:'local_ext_category'},
  {provider:'cmm',platform:'douyin',operation:'video_commerce_video_library_custom_search_top_selling_video',label:'蝉妈妈带货视频关键词检索',response_mapping_required:true}];
const connection = {id:'source-1',provider:'datax',platform:'douyin',operation:'douyin.videos.search',label:'我的数据源',has_key:true,
  rights:permissions,search_enabled:true,live_verified:false};
const result = {id:'result-1',platform_id:'dy-1',platform:'douyin',title:'洗面奶实测',source_url:'https://www.douyin.com/video/dy-1',
  entity:{type:'creator',name:'护肤账号',id:'author-1'},published_at:'2026-10-05',statistics_period:{basis:'cumulative_at_fetch',start:null,end:null},
  metrics:{likes:{kind:'exact',value:'0'},sales:{kind:'missing'},revenue:{kind:'range',lower:'10',upper:'50',unit:'CNY'}},
  provenance:{operation:'douyin.videos.search'},rights_snapshot:permissions,media_url:null};
const completed = {id:'job-1',state:'succeeded',plan:{connection_id:'source-1',query:'洗面奶',allowed_actions:['search','retain_metadata']},
  stop_reason:'source_exhausted',quote:{amount:'0.02',currency:'USD'},actual_cost:'0.02',
  progress:{calls_attempted:1,rows_received:1,results_saved:1,selected:0,media_imported:0}};
function backend(options:{empty?:boolean,media?:boolean}={}) {
  const writes:{path:string,body:any,method:string}[]=[];
  const licensed={...permissions,download_media:!!options.media,retain_media:!!options.media,model_process:!!options.media};
  let connections:any[]=options.empty?[]:[{...connection,rights:licensed}];
  let jobs:any[]=[]; let references:any[]=[];
  const api = vi.fn(async (path:string,init?:RequestInit) => {
    if(init?.method){
      const body=init.body?JSON.parse(String(init.body)):{};writes.push({path,body,method:init.method});
      if(path.endsWith('/connections')){const saved={...connection,...body,id:'source-1',rights:body.rights};delete saved.api_key;connections=[saved];return saved;}
      if(path.endsWith('/rights')){connections=connections.map(c=>({...c,rights:body,search_enabled:!!body.search&&!!body.retain_metadata}));return connections[0];}
      if(path.endsWith('/searches')){const j={...completed,state:'queued',plan:body};jobs=[j];return j;}
      if(path.endsWith('/select')){references=[{id:'ref-1',result_id:'result-1',job_id:'job-1',title:result.title,asset_id:body.import_media?'asset-real':null,selection_reason:body.reason,status:body.import_media?'media_imported':'metadata_only'}];return references;}
    }
    if(path.endsWith('/results'))return [{...result,rights_snapshot:licensed,media_url:options.media?'https://cdn.example/real.mp4':null}];
    if(path.endsWith('/searches/job-1'))return {...completed,plan:jobs[0]?.plan||{...completed.plan,allowed_actions:options.media?['search','retain_metadata','download_media','retain_media','model_process']:completed.plan.allowed_actions}};
    return {catalog,connections,search_jobs:jobs,references};
  });
  return {api:api as any,writes};
}

test('connection rights, explicit quoted search, real result selection and reference persist without automatic paid writes',async()=>{
  const {api,writes}=backend({empty:true}); const changed=vi.fn();
  render(<IndustryWorkspace project="p" api={api} onChanged={changed}/>);
  expect(await screen.findByText(/尚未通过真实账户验收/)).toBeInTheDocument();
  expect(writes).toHaveLength(0);
  fireEvent.change(screen.getByLabelText('来源名称'),{target:{value:'我的数据源'}});
  fireEvent.change(screen.getByLabelText('来源 API 密钥'),{target:{value:'private-user-key'}});
  fireEvent.change(screen.getByLabelText('API 权限依据'),{target:{value:'已开通正式 API'}});
  fireEvent.click(screen.getByLabelText('已确认正式 API 调用权限'));
  fireEvent.change(screen.getByLabelText('新连接授权到期时间'),{target:{value:'2099-01-01T00:00'}});
  fireEvent.change(screen.getByLabelText('新连接授权依据'),{target:{value:'客户许可证'}});
  fireEvent.click(screen.getByLabelText('新连接：查询来源'));
  fireEvent.click(screen.getByLabelText('新连接：留存数据'));
  fireEvent.click(screen.getByRole('button',{name:'保存来源连接'}));
  await waitFor(()=>expect(writes).toHaveLength(1));
  expect(writes[0].body.rights.model_process).toBe(false);
  await waitFor(()=>expect(screen.getByLabelText('来源 API 密钥')).toHaveValue(''));
  fireEvent.change(screen.getByLabelText('检索关键词'),{target:{value:'洗面奶'}});
  fireEvent.change(screen.getByLabelText('查询币种'),{target:{value:'USD'}});
  fireEvent.change(screen.getByLabelText('费用上限'),{target:{value:'0.02'}});
  fireEvent.click(screen.getByRole('button',{name:'核对查询计划'}));
  expect(writes).toHaveLength(1);
  const review=screen.getByRole('region',{name:'待确认查询计划'});
  expect(within(review).getByText(/USD 0.02/)).toBeInTheDocument();
  fireEvent.click(within(review).getByRole('checkbox',{name:'我确认本次费用上限和允许操作'}));
  fireEvent.click(within(review).getByRole('button',{name:'确认上限并开始查询'}));
  await waitFor(()=>expect(writes).toHaveLength(2));
  expect(writes[1].body).toEqual(expect.objectContaining({connection_id:'source-1',query:'洗面奶',quote:'0.02',currency:'USD',max_calls:1,allowed_actions:['search','retain_metadata'],idempotency_key:expect.any(String)}));
  fireEvent.click(await screen.findByRole('button',{name:'刷新查询结果'}));
  expect(await screen.findByText('洗面奶实测')).toBeInTheDocument();
  expect(screen.getByText('点赞：0')).toBeInTheDocument();
  expect(screen.getByText('销量：缺失')).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText('选择 洗面奶实测'));
  fireEvent.change(screen.getByLabelText('选择理由'),{target:{value:'产品特写可借鉴'}});
  expect(screen.getByRole('button',{name:'导入授权媒体'})).toBeDisabled();
  fireEvent.click(screen.getByRole('button',{name:'保存为参考'}));
  await waitFor(()=>expect(writes).toHaveLength(3));
  expect(writes[2].body).toEqual({result_ids:['result-1'],reason:'产品特写可借鉴',import_media:false});
  expect(await screen.findByText('已保存元数据参考，尚未导入媒体')).toBeInTheDocument();
  expect(changed).toHaveBeenCalled();
},20000);

test('editing a preview invalidates approval and source rights can be explicitly revoked',async()=>{
  const {api,writes}=backend();render(<IndustryWorkspace project="p" api={api}/>);
  await screen.findByRole('option',{name:'我的数据源 · douyin'});
  fireEvent.change(screen.getByLabelText('检索关键词'),{target:{value:'原关键词'}});
  fireEvent.change(screen.getByLabelText('查询币种'),{target:{value:'USD'}});
  fireEvent.change(screen.getByLabelText('费用上限'),{target:{value:'0.02'}});
  fireEvent.click(screen.getByRole('button',{name:'核对查询计划'}));
  fireEvent.click(screen.getByLabelText('我确认本次费用上限和允许操作'));
  fireEvent.change(screen.getByLabelText('检索关键词'),{target:{value:'修改关键词'}});
  expect(screen.queryByRole('region',{name:'待确认查询计划'})).not.toBeInTheDocument();
  expect(writes).toHaveLength(0);
  fireEvent.click(screen.getByText('查看与调整来源授权'));
  fireEvent.click(screen.getByLabelText('调整权限：查询来源'));
  fireEvent.click(screen.getByRole('button',{name:'保存授权变更'}));
  await waitFor(()=>expect(writes).toHaveLength(1));
  expect(writes[0]).toMatchObject({method:'PATCH',body:{search:false}});
},20000);

test('unknown cost stops are explained and media needs both plan rights and explicit import confirmation',async()=>{
  const {api,writes}=backend({media:true});
  const wrapped=async(path:string,init?:RequestInit)=>{
    const value=await api(path,init);
    if(!init?.method&&path.endsWith('/searches/job-1'))return {...value,actual_cost:null,stop_reason:'billing_unknown'};
    if(!init?.method&&path.endsWith('/industry'))return {...value,search_jobs:[{...completed,actual_cost:null,stop_reason:'billing_unknown',plan:{...completed.plan,allowed_actions:['search','retain_metadata','download_media','retain_media','model_process']}}]};
    return value;
  };
  render(<IndustryWorkspace project="p" api={wrapped as any}/>);
  fireEvent.click(await screen.findByRole('button',{name:/查看查询.*洗面奶/}));
  expect(await screen.findByText(/费用尚未核清.*已停止继续请求/)).toBeInTheDocument();
  fireEvent.click(await screen.findByLabelText('选择 洗面奶实测'));
  fireEvent.change(screen.getByLabelText('选择理由'),{target:{value:'授权媒体入库'}});
  expect(screen.getByRole('button',{name:'导入授权媒体'})).toBeDisabled();
  fireEvent.click(screen.getByLabelText('我确认本次媒体下载、留存和模型处理授权'));
  fireEvent.click(screen.getByRole('button',{name:'导入授权媒体'}));
  await waitFor(()=>expect(writes).toHaveLength(1));
  expect(writes[0].body.import_media).toBe(true);
},20000);

test('CMM mapping errors and API failures remain visible without claiming a live connection',async()=>{
  const {api,writes}=backend({empty:true});render(<IndustryWorkspace project="p" api={api}/>);
  await screen.findByText(/尚未通过真实账户验收/);
  fireEvent.change(screen.getByLabelText('来源操作'),{target:{value:'cmm:video_commerce_video_library_custom_search_top_selling_video'}});
  fireEvent.change(screen.getByLabelText('来源名称'),{target:{value:'蝉妈妈'}});
  fireEvent.change(screen.getByLabelText('来源 API 密钥'),{target:{value:'user-key'}});
  fireEvent.change(screen.getByLabelText('API 权限依据'),{target:{value:'账户权限'}});
  fireEvent.click(screen.getByLabelText('已确认正式 API 调用权限'));
  fireEvent.change(screen.getByLabelText('新连接授权到期时间'),{target:{value:'2099-01-01T00:00'}});
  fireEvent.change(screen.getByLabelText('新连接授权依据'),{target:{value:'合同'}});
  fireEvent.change(screen.getByLabelText('CMM 响应字段映射'),{target:{value:'{"reviewed":false}'}});
  fireEvent.click(screen.getByRole('button',{name:'保存来源连接'}));
  expect(await screen.findByRole('alert')).toHaveTextContent(/映射.*核对/);
  expect(writes).toHaveLength(0);
},20000);

test('a failed refreshed result read clears previously displayed licensed content and reports the error',async()=>{
  const {api}=backend();let denied=false;
  const guarded=async(path:string,init?:RequestInit)=>{
    if(path.endsWith('/results')&&denied)throw new Error('来源数据留存授权已撤销');
    const value=await api(path,init);
    if(path.endsWith('/industry'))return {...value,search_jobs:[completed]};
    return value;
  };
  render(<IndustryWorkspace project="p" api={guarded as any}/>);
  fireEvent.click(await screen.findByRole('button',{name:/查看查询.*洗面奶/}));
  expect(await screen.findByText('洗面奶实测')).toBeInTheDocument();
  denied=true;fireEvent.click(screen.getByRole('button',{name:'刷新查询结果'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('来源数据留存授权已撤销');
  expect(screen.queryByText('洗面奶实测')).not.toBeInTheDocument();
},20000);

test('switching projects discards late responses from the previous project',async()=>{
  let resolveOld:(value:any)=>void=()=>{};
  const old=new Promise(resolve=>{resolveOld=resolve});
  const api=vi.fn(async(path:string)=>path.includes('/projects/old/')?old:{catalog,connections:[],search_jobs:[],references:[]});
  const view=render(<IndustryWorkspace project="old" api={api as any}/>);
  view.rerender(<IndustryWorkspace project="new" api={api as any}/>);
  resolveOld({catalog,connections:[{...connection,label:'上个项目私有来源'}],search_jobs:[],references:[]});
  await waitFor(()=>expect(api).toHaveBeenCalledWith('/projects/new/industry'));
  expect(screen.queryByText(/上个项目私有来源/)).not.toBeInTheDocument();
},20000);

test('explicit failed import retry uses a new attempt but uncertain network retry retains its attempt',async()=>{
 const {api,writes}=backend({media:true});let failed=true,uncertain=true;
 const guarded=async(path:string,init?:RequestInit)=>{
  if(!init?.method&&path.endsWith('/industry')){
   const value=await api(path,init);
   return {...value,search_jobs:[{...completed,plan:{...completed.plan,allowed_actions:['search','retain_metadata','download_media','retain_media','model_process']}}],references:failed?[{id:'ref',result_id:'result-1',job_id:'job-1',status:'import_failed'}]:[]};
  }
  if(init?.method&&path.endsWith('/select')){
   writes.push({method:init.method,path,body:JSON.parse(String(init.body))});
   if(uncertain){uncertain=false;throw new Error('连接中断，请核对任务')}
   failed=false;return {id:'import',kind:'industry_import',state:'queued'};
  }
  return api(path,init);
 };
 const confirm=vi.spyOn(window,'confirm').mockReturnValue(true);
 render(<IndustryWorkspace project="p" api={guarded as any}/>);
 fireEvent.click(await screen.findByRole('button',{name:/查看查询.*洗面奶/}));
 fireEvent.click(await screen.findByLabelText('选择 洗面奶实测'));
 fireEvent.change(screen.getByLabelText('选择理由'),{target:{value:'明确重试入库'}});
 fireEvent.click(screen.getByLabelText('我确认本次媒体下载、留存和模型处理授权'));
 fireEvent.click(screen.getByRole('button',{name:'确认重试媒体导入'}));
 await screen.findByText('连接中断，请核对任务');
 fireEvent.click(screen.getByRole('button',{name:'确认重试媒体导入'}));
 await waitFor(()=>expect(writes).toHaveLength(2));
 expect(confirm).toHaveBeenCalled();
 expect(writes[0].body.attempt_key).toEqual(expect.any(String));
 expect(writes[1].body.attempt_key).toBe(writes[0].body.attempt_key);
});
