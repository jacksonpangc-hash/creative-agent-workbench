import {afterEach,expect,test,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import ModelServiceSettings from './settings/ModelServiceSettings';
import AnalysisPreferences from './settings/AnalysisPreferences';
import ProductFactsEditor from './strategy/ProductFactsEditor';
import PublicReferences from './industry/PublicReferences';
import BrowserConnection from './settings/BrowserConnection';

const response=(value:unknown)=>new Response(JSON.stringify(value));
const availability={strategy:{available:true},video_analysis:{available:true,route:'sampled_frames'}};
const catalog=[{id:'example',name:'示例服务',country:'测试',models:[{id:'vision',name:'视觉模型',availability}]}];
const model={id:'binding',provider:'example',model_id:'vision',label:'已保存的模型',role:'strategy',has_key:true,availability,max_output_tokens:8192};
afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.restoreAllMocks()});

test('model connections begin with saved services and only open the form on an explicit action',()=>{
 render(<ModelServiceSettings base="/projects/A" models={[model]} catalog={catalog} run={vi.fn()} reload={vi.fn()}/>);
 expect(screen.getByText('已保存的模型')).toBeVisible();
 expect(screen.queryByRole('combobox',{name:'服务商'})).not.toBeInTheDocument();
 expect(screen.queryByRole('radiogroup',{name:'模型分析方式'})).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'添加模型服务'}));
 expect(screen.getByRole('combobox',{name:'服务商'})).toBeVisible();
 expect(screen.getByLabelText('API 密钥 · 保存到系统密钥库')).toBeVisible();
 expect(screen.getAllByText(/可分析画面/).length).toBeGreaterThan(0);
});

test('saving the selected default keeps the existing payer and preserves the legacy quote',async()=>{
 const writes:any[]=[];
 vi.stubGlobal('fetch',vi.fn(async(_input:string,init?:RequestInit)=>{if(init?.method==='PUT'){writes.push(JSON.parse(init.body as string));return response({...writes[0],version:3})}return response({default_binding_id:'missing-existing-account',default_quote:'0.7',version:2})}));
 render(<AnalysisPreferences base="/projects/A" models={[model]} currency="CNY" onSaved={vi.fn()}/>);
 await waitFor(()=>expect(screen.getByLabelText('默认分析模型')).toHaveValue('missing-existing-account'));
 expect(screen.getByRole('option',{name:'此前选择的服务（当前不可用）'})).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'保存偏好'}));
 await waitFor(()=>expect(writes).toHaveLength(1));
 expect(writes[0]).toEqual({default_binding_id:'missing-existing-account',allow_external_references:false,expected_version:2});
});

test('pasted product text becomes an editable unconfirmed draft without a model request or identifier inputs',async()=>{
 const writes:any[]=[];
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{expect(input).not.toContain('/actions');if(init?.method==='PUT'){writes.push(JSON.parse(init.body as string));return response({...writes[0],version:1})}return response({version:0,product:'',claims:[],constraints:[]})}));
 render(<ProductFactsEditor base="/projects/A"/>);
 await waitFor(()=>expect(screen.getByLabelText('商品介绍')).not.toBeDisabled());
 fireEvent.change(screen.getByLabelText('商品介绍'),{target:{value:'商品名称：清洁牙膏\n规格：100g\n卖点：独立密封\n适合人群：成年人'}});
 fireEvent.click(screen.getByRole('button',{name:'整理成草稿'}));
 expect(screen.getByLabelText('商品名称')).toHaveValue('清洁牙膏');
 expect(screen.getByLabelText('商品规格')).toHaveValue('100g');
 expect(screen.queryByRole('textbox',{name:/ID/})).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'确认并保存商品'}));
 await waitFor(()=>expect(writes).toHaveLength(1));
 expect(writes[0]).toMatchObject({expected_version:0,claims:[expect.objectContaining({statement:'独立密封',confirmed:false,source:expect.stringContaining('用户提供的商品介绍')})],constraints:['商品规格：100g']});
 expect(writes[0].sku_id).toBeTruthy();
});

test('product edits retain internal identities and clear confirmation only on a changed claim',async()=>{
 const writes:any[]=[];
 vi.stubGlobal('fetch',vi.fn(async(_input:string,init?:RequestInit)=>{if(init?.method==='PUT'){writes.push(JSON.parse(init.body as string));return response({...writes[0],version:3})}return response({version:2,product:'原商品',product_id:'product',sku_id:'sku',claims:[{fact_id:'claim',statement:'原说法',source:'包装',confirmed:true,valid_until:'2026-12-31'}],constraints:[]})}));
 render(<ProductFactsEditor base="/projects/A"/>);
 fireEvent.change(await screen.findByLabelText('卖点内容 1'),{target:{value:'修改的说法'}});
 fireEvent.click(screen.getByRole('button',{name:'确认并保存商品'}));
 await waitFor(()=>expect(writes).toHaveLength(1));
 expect(writes[0]).toMatchObject({product_id:'product',sku_id:'sku',expected_version:2,claims:[expect.objectContaining({fact_id:'claim',confirmed:false,valid_until:'2026-12-31'})]});
});

test('reference collection keeps explicit source choices and only fetches media after explicit permission',async()=>{
 const calls:{input:string;body:any}[]=[];
 const ref={id:'r',title:'只保存的链接',platform:'douyin',url:'https://www.douyin.com/video/123',can_visually_analyze:false,observations:[]};
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{calls.push({input,body:init?.body?JSON.parse(init.body as string):null});return response(input.endsWith('/references')?[ref]:input.endsWith('/state')?{assets:[]}:input.endsWith('/media')?{id:'job',state:'queued'}:[])}));
 render(<PublicReferences base="/projects/A"/>);
 await screen.findByText('只保存的链接');
 expect(screen.getAllByRole('tab').map(t=>t.textContent)).toEqual(['我的收藏','平台网页','创意结构库','已连接的数据服务']);
 expect(calls.some(c=>c.input.endsWith('/media'))).toBe(false);
 expect(screen.getByRole('button',{name:'获取视频'})).toBeDisabled();
 fireEvent.click(screen.getByLabelText('我有权获取并分析这条视频：只保存的链接'));
 fireEvent.click(screen.getByRole('button',{name:'获取视频'}));
 await waitFor(()=>expect(calls.some(c=>c.body?.rights_confirmed===true)).toBe(true));
 expect(calls.find(c=>c.body?.rights_confirmed)?.body.idempotency_key).toBeTruthy();
 expect(screen.getByRole('button',{name:'加入本次创作'})).toBeDisabled();
});

test('browser connection copies the actual runtime origin and project-bound short code without a bearer',async()=>{
 const writeText=vi.fn(async(_text:string)=>{});
 Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText}});
 vi.stubGlobal('fetch',vi.fn(async()=>response({code:'ABCDEFGH23',expires_in:120,project_id:'A'})));
 render(<BrowserConnection base="/projects/A"/>);
 fireEvent.click(screen.getByText('开发与排障：单次导入'));
 fireEvent.click(screen.getByRole('button',{name:'生成本次导入连接'}));
 const text=await screen.findByRole('textbox',{name:'本次连接信息'});
 const value=(text as HTMLTextAreaElement).value;
 const copied=JSON.parse(value);
 expect(copied).toMatchObject({base_url:'http://127.0.0.1:8765',code:'ABCDEFGH23',project_id:'A'});
 expect(value).not.toMatch(/bearer|token|api_key/i);
});

test('opening the shared import entry never creates a paid query or a second reference library',async()=>{
 const calls:string[]=[];
 vi.stubGlobal('fetch',vi.fn(async(input:string)=>{calls.push(input);return response(input.endsWith('/state')?{assets:[]}:[])}));
 render(<PublicReferences base="/projects/A" initialQuery="牙膏" initialImport/>);
 await waitFor(()=>expect(screen.getByLabelText('分享文案或链接（每行一条）')).toBeVisible());
 expect(screen.getByLabelText('搜索收藏')).toHaveValue('牙膏');
 expect(calls.every(input=>input.endsWith('/plans')||input.endsWith('/references')||input.endsWith('/state')||input.endsWith('/browser-bridge/status')||input.endsWith('/browser-bridge/connections'))).toBe(true);
 expect(calls.some(input=>input.includes('/cmm')||input.includes('/media'))).toBe(false);
});
