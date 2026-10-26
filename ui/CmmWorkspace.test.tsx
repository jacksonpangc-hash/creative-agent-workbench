import {afterEach,expect,test,vi} from 'vitest';
import {render,screen,fireEvent,cleanup} from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import {CmmSettings,CmmLinks,CmmReferences} from './industry/CmmWorkspace';
afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.useRealTimers()});
test('saving a key does not trigger a supplier permission check',async()=>{
 const writes:string[]=[];
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{
  if(init?.method&&init.method!=='GET')writes.push(input);
  return new Response(JSON.stringify(input.endsWith('/cmm')?{connection:null,capabilities:{},jobs:[],tariffs:{}}:{}),{status:200});
 }));
 render(<CmmSettings base="/projects/A"/>);
 const input=await screen.findByLabelText('蝉妈妈 API Key');
 fireEvent.change(input,{target:{value:'fixture-not-a-real-key'}});
 fireEvent.click(screen.getByRole('button',{name:'保存连接'}));
 await screen.findByText(/密钥已保存/);
 expect(writes.map(url=>new URL(url,'http://test').pathname)).toEqual(['/api/projects/A/cmm/connection']);
 expect(input).toHaveValue('');
});
test('a waiting CMM task exposes explicit status check and cancellation after reopening settings',async()=>{
 const calls:string[]=[];vi.stubGlobal('fetch',vi.fn(async(input:string)=>{calls.push(input);return new Response(JSON.stringify(input.endsWith('/cmm')?{connection:{status:'permissions_checked',permission_checked_at:'2026-10-24T01:00:00Z'},capabilities:{},usage:{},jobs:[{id:'j',operation:'video_understanding',state:'waiting',provider_task_id:'remote',calls_attempted:1,max_calls:3,reserved_points:'5'}]}:{}),{status:200})}));
 render(<CmmSettings base="/projects/A"/>);await screen.findByText('已检查权益');
 fireEvent.click(screen.getByText('积分与使用记录'));
 fireEvent.click(screen.getByRole('button',{name:'查询处理结果'}));
 expect(calls.some(u=>u.endsWith('/api/projects/A/cmm/jobs/j/poll'))).toBe(true);
 expect(screen.getByRole('button',{name:'停止后续处理'})).toBeInTheDocument();
});
test('link preflight does not submit or silently start a paid operation',async()=>{
 const calls:string[]=[];vi.stubGlobal('fetch',vi.fn(async(input:string)=>{calls.push(input);return new Response(JSON.stringify({authorized:false,query:{video_url:'https://v.douyin.com/test/'}}),{status:200})}));
 render(<CmmLinks base="/projects/A" onImported={vi.fn()}/>);
 fireEvent.change(screen.getByRole('textbox',{name:'素材链接'}),{target:{value:'分享 https://v.douyin.com/test/'}});
 fireEvent.click(screen.getByRole('button',{name:'检查链接 · 不扣积分'}));
 await screen.findByText(/尚未确认所选权益/);
 expect(calls.map(u=>new URL(u,'http://test').pathname)).toEqual(['/api/projects/A/cmm/preflight']);
 expect(screen.getByRole('button',{name:'确认积分上限并处理'})).toBeDisabled();
});
test('an authorized link with unverified pricing cannot start a paid task',async()=>{
 const calls:string[]=[];vi.stubGlobal('fetch',vi.fn(async(input:string)=>{calls.push(input);return new Response(JSON.stringify({authorized:true,tariff_verified:false,max_points_per_call:null,query:{video_url:'https://v.douyin.com/test/'}}),{status:200})}));
 render(<CmmLinks base="/projects/A" onImported={vi.fn()}/>);
 fireEvent.change(screen.getByRole('textbox',{name:'素材链接'}),{target:{value:'https://v.douyin.com/test/'}});
 fireEvent.click(screen.getByRole('button',{name:'检查链接 · 不扣积分'}));
 await screen.findByText('账号已确认所选处理权益');
 fireEvent.change(screen.getByRole('textbox',{name:'本次 AI 积分上限'}),{target:{value:'100'}});
 expect(screen.getByRole('button',{name:'确认积分上限并处理'})).toBeDisabled();
 expect(screen.getByText(/这项处理的积分费用尚未核验/)).toBeInTheDocument();
 expect(calls).toHaveLength(1);
});

test('industry references preserve supplier sales ranges and default to the last 30 complete days',async()=>{
 vi.useFakeTimers({toFake:['Date']});vi.setSystemTime(new Date(2026,9,24,10));
 vi.stubGlobal('fetch',vi.fn(async(input:string)=>new Response(JSON.stringify(
  input.endsWith('/cmm')?{connection:{status:'permissions_checked'},capabilities:{video_commerce_video_library_custom_search_top_selling_video:true},response_schema_ready:{},jobs:[]}:
  input.endsWith('/references')?[]:{id:'j',state:'succeeded',operation:'video_commerce_video_library_custom_search_top_selling_video',reserved_points:'10',calls_attempted:1,max_calls:1,results:[
   {id:'r',title:'范围指标测试',source_url:'https://www.douyin.com/video/1234567890123456789',metrics:{sales:{kind:'range',raw_value:'1000~2500',lower:null,upper:null,unit:'件'},revenue:{kind:'range',raw_value:'1w~2.5w',lower:null,upper:null,unit:'元'}}}
  ]}),{status:200})));
 render(<CmmReferences base="/projects/A" onConnect={vi.fn()} onSelect={vi.fn()}/>);
 await screen.findByLabelText('商品或关键词');
 expect(screen.getByLabelText('发布日期起')).toHaveValue('2026-09-24');
 expect(screen.getByLabelText('发布日期止')).toHaveValue('2026-10-23');
 fireEvent.change(screen.getByLabelText('商品或关键词'),{target:{value:'绿豆饼'}});
 fireEvent.click(screen.getByRole('button',{name:'确认积分上限并搜索'}));
 await screen.findByText('范围指标测试');
 expect(screen.getByText(/1000~2500/)).toBeInTheDocument();
 expect(screen.getByText(/1w~2.5w/)).toBeInTheDocument();
 expect(screen.queryByText(/null/)).not.toBeInTheDocument();
});

test('settings show cumulative reservations separately from unknown actual charges',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify({connection:{status:'unverified'},capabilities:{},jobs:[],tariffs:{},usage:{limit_points:'30',reserved_points:'30',remaining_points:'0',actual_points:null}}),{status:200})));
 render(<CmmSettings base="/projects/A"/>);
 expect(await screen.findByText(/累计上限 30 积分/)).toBeInTheDocument();
 expect(screen.getByText(/可新增暂占额度 0 积分/)).toBeInTheDocument();
 expect(screen.getByText(/累计上限 30 积分/)).toHaveTextContent(/实际扣费.*待核账/);
});

test('first setup can authorize a cumulative points limit without spending or clearing reservations',async()=>{
 const writes:any[]=[];const state={connection:null,jobs:[],tariffs:{},usage:{limit_points:null,reserved_points:'20',remaining_points:null,actual_points:null}};
 vi.stubGlobal('fetch',vi.fn(async(input:string,init:any)=>{if(init?.method){writes.push({input,body:JSON.parse(init.body)});return new Response('{}')}return new Response(JSON.stringify(state))}));
 render(<CmmSettings base="/projects/A" view="usage"/>);fireEvent.change(await screen.findByRole('spinbutton',{name:'累计积分额度'}),{target:{value:'100'}});fireEvent.click(screen.getByRole('button',{name:'保存累计额度'}));await screen.findByText('累计额度已保存，已有使用记录和占用不变');expect(writes).toEqual([{input:expect.stringContaining('/cmm/budget'),body:{limit_points:'100'}}]);
});
