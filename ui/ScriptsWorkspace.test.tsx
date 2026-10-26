import {localDraftFixture} from './localDraftFixture';
import {afterEach,expect,test,vi} from 'vitest';
import {render,screen,fireEvent,waitFor,cleanup} from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import ScriptsWorkspace from './scripts/ScriptsWorkspace';
afterEach(()=>{cleanup();sessionStorage.clear();vi.restoreAllMocks();vi.unstubAllGlobals()});
test('script edit sends expected version without exposing deferred production workflow',async()=>{
 const writes:any[]=[];let script:any={id:'s',version:2,title:'脚本A',objective:'试拍',confirmed:false,rows:[{start_ms:0,end_ms:3000,dialogue:'原台词',visual:'近景',tone:'',notes:'',references:[]}],revisions:[]};
 vi.stubGlobal('fetch',vi.fn(async(input:string,init:any)=>{const localDraft=localDraftFixture(input,init);if(localDraft)return localDraft;
  if(init?.method==='PUT'){writes.push(JSON.parse(init.body));script={...script,...writes.at(-1),version:3};return new Response(JSON.stringify(script))}
  if(input.endsWith('/scripts'))return new Response(JSON.stringify([script]));
  if(input.endsWith('/product-facts'))return new Response(JSON.stringify({version:0,product:'',claims:[],constraints:[]}));
  return new Response('[]');
 }));
 render(<ScriptsWorkspace base="/projects/p" assets={[]} onUseScript={vi.fn()}/>);
 fireEvent.click(await screen.findByRole('button',{name:'脚本A · v2'}));
 fireEvent.change(screen.getByRole('textbox',{name:'第1行台词'}),{target:{value:'修订台词'}});
 fireEvent.change(screen.getByRole('spinbutton',{name:'第1镜时长'}),{target:{value:'5'}});
 expect(screen.queryByRole('button',{name:'建立制作任务'})).not.toBeInTheDocument();
 expect(screen.getByText('选择已有素材，或写下这个画面怎样补拍。')).toBeInTheDocument();
 await waitFor(()=>expect(screen.getByRole('button',{name:'保存新版本'})).toBeEnabled());
 fireEvent.click(screen.getByRole('button',{name:'保存新版本'}));
 await waitFor(()=>expect(writes).toHaveLength(1));
 expect(writes[0].expected_version).toBe(2);
 expect(writes[0].rows[0].dialogue).toBe('修订台词');
 expect(writes[0].rows[0].end_ms).toBe(5000);
 expect(writes[0].confirmed).toBe(false);
});

test('script revisions preserve provenance and creation fields and expose five exports',async()=>{
 const writes:any[]=[];let script:any={id:'s',version:2,title:'完整脚本',objective:'试拍',confirmed:false,usage_status:'planned',product_id:'product',sku_id:'sku',target_duration_ms:8000,selection_snapshot:{angle:'酥皮'},rows:[{start_ms:0,end_ms:8000,dialogue:'原台词',visual:'近景',tone:'',notes:'',references:[],own_references:[{asset_id:'a',start_ms:0,end_ms:2000,observation:'开箱',provenance:'subtitle'}],reshoot:'',claim_ids:['claim'],props:['盘子']}],revisions:[]};
 vi.stubGlobal('fetch',vi.fn(async(input:string,init:any)=>{const localDraft=localDraftFixture(input,init);if(localDraft)return localDraft;if(init?.method==='PUT'){writes.push(JSON.parse(init.body));script={...script,...writes.at(-1),version:3};return new Response(JSON.stringify(script))}if(input.endsWith('/scripts'))return new Response(JSON.stringify([script]));if(input.endsWith('/product-facts'))return new Response(JSON.stringify({version:0,product:'',claims:[],constraints:[]}));return new Response('[]')}));
 render(<ScriptsWorkspace base="/projects/p" assets={[]} onUseScript={vi.fn()}/>);
 fireEvent.click(await screen.findByRole('button',{name:'完整脚本 · v2'}));
 await waitFor(()=>expect(screen.getByRole('button',{name:'DOCX'})).toBeEnabled());
 for(const format of ['DOCX','XLSX','PDF','MD','JSON'])expect(screen.getByRole('button',{name:format})).toBeEnabled();
 fireEvent.click(screen.getByText('脚本信息与版本'));
 fireEvent.change(screen.getByRole('combobox',{name:'脚本采用状态'}),{target:{value:'shot'}});
 fireEvent.change(screen.getByRole('textbox',{name:'第1行台词'}),{target:{value:'修订台词'}});
 fireEvent.click(screen.getByRole('button',{name:'保存新版本'}));
 await waitFor(()=>expect(writes).toHaveLength(1));
 expect(writes[0]).toMatchObject({usage_status:'shot',product_id:'product',sku_id:'sku',selection_snapshot:{angle:'酥皮'},rows:[expect.objectContaining({own_references:script.rows[0].own_references,claim_ids:['claim'],props:['盘子']})]});
 await screen.findByRole('button',{name:'完整脚本 · v3'});fireEvent.click(screen.getByRole('button',{name:'完整脚本 · v3'}));expect(await screen.findByRole('textbox',{name:'第1行台词'})).toHaveValue('修订台词');
});

test('editor assigns a real own media fragment and retains its source interval',async()=>{const writes:any[]=[];const script={id:'s',version:1,title:'片段脚本',objective:'复用',confirmed:false,sku_id:'sku',selection_snapshot:{own_assets:[{id:'own'}]},source:{selection_snapshot:{own_assets:[{id:'own'}]}},rows:[{start_ms:0,end_ms:3000,dialogue:'台词',visual:'近景',tone:'',notes:'',references:[],own_references:[]}],revisions:[]};vi.stubGlobal('fetch',vi.fn(async(input:string,init:any)=>{const localDraft=localDraftFixture(input,init);if(localDraft)return localDraft;if(init?.method==='PUT'){writes.push(JSON.parse(init.body));return new Response(JSON.stringify({...script,...writes.at(-1),version:2}))}return new Response(JSON.stringify(input.endsWith('/scripts')?[script]:input.endsWith('/product-facts')?{product:'',claims:[],constraints:[]}:[]))}));render(<ScriptsWorkspace base="/projects/p" assets={[{id:'own',title:'开箱原片',source_kind:'own',inspection:'ready',media_type:'video',metadata:{duration_ms:9000,sku_id:'sku'}},{id:'reference',title:'行业参考',source_kind:'industry',metadata:{}}]} onUseScript={vi.fn()}/>);fireEvent.click(await screen.findByRole('button',{name:'片段脚本 · v1'}));fireEvent.click(screen.getByRole('button',{name:'为第1镜添加自有片段'}));fireEvent.change(screen.getByRole('combobox',{name:'选择自有素材'}),{target:{value:'own'}});fireEvent.click(screen.getByRole('button',{name:'使用所选片段'}));fireEvent.click(screen.getByText('镜头详情与依据'));expect(screen.queryByRole('option',{name:'行业参考'})).not.toBeInTheDocument();fireEvent.change(screen.getByRole('spinbutton',{name:'第1镜自有片段1起点'}),{target:{value:'2'}});fireEvent.change(screen.getByRole('spinbutton',{name:'第1镜自有片段1终点'}),{target:{value:'5'}});await waitFor(()=>expect(screen.getByRole('button',{name:'保存新版本'})).toBeEnabled());fireEvent.click(screen.getByRole('button',{name:'保存新版本'}));await waitFor(()=>expect(writes).toHaveLength(1));expect(writes[0].rows[0].own_references).toEqual([expect.objectContaining({asset_id:'own',start_ms:2000,end_ms:5000})])});


test('semantic claim review shows actual snapshot evidence and saves human review',async()=>{const writes:any[]=[];const script={id:'s',version:1,title:'依据脚本',objective:'核实',confirmed:false,product_facts_snapshot:{claims:[{fact_id:'c1',statement:'独立包装',source:'包装清单',confirmed:true}]},rows:[{start_ms:0,end_ms:3000,dialogue:'方便随身携带',visual:'特写',references:[],claim_ids:['c1'],factual_claims:[{statement:'方便随身携带',fact_ids:['c1'],reviewed:false}]}],revisions:[]};vi.stubGlobal('fetch',vi.fn(async(input:string,init:any)=>{const localDraft=localDraftFixture(input,init);if(localDraft)return localDraft;if(init?.method==='PUT'){writes.push(JSON.parse(init.body));return new Response(JSON.stringify({...script,...writes.at(-1),version:2}))}return new Response(JSON.stringify(input.endsWith('/scripts')?[script]:[]))}));render(<ScriptsWorkspace base="/projects/p" assets={[]} onUseScript={vi.fn()}/>);fireEvent.click(await screen.findByRole('button',{name:'依据脚本 · v1'}));expect(screen.getByText('需要确认：方便随身携带')).toBeInTheDocument();fireEvent.click(screen.getByText('镜头详情与依据'));expect(screen.getAllByText(/包装清单/).length).toBeGreaterThan(0);fireEvent.click(screen.getByRole('checkbox',{name:'我已核对这条说法与依据'}));fireEvent.click(screen.getByRole('button',{name:'保存新版本'}));await waitFor(()=>expect(writes).toHaveLength(1));expect(writes[0].rows[0].factual_claims[0]).toMatchObject({statement:'方便随身携带',fact_ids:['c1'],reviewed:true});expect(screen.queryByRole('heading',{name:'制作任务'})).not.toBeInTheDocument()});

test('compact script keeps unsaved edits when switching scripts, reopening, and replaying footage',async()=>{
 const script={id:'s',version:1,title:'回看脚本',objective:'试拍',rows:[{start_ms:0,end_ms:3000,dialogue:'原台词',visual:'近景',references:[],own_references:[{asset_id:'own',start_ms:1000,end_ms:4000,observation:'开箱',provenance:'sampled_frames'}]}],revisions:[]};
 const other={...script,id:'s2',title:'另一条脚本'};
 const writes:any[]=[],replay=vi.fn();
 vi.stubGlobal('fetch',vi.fn(async(input:string,init:any)=>{const localDraft=localDraftFixture(input,init);if(localDraft)return localDraft;if(init?.method)writes.push(init);return new Response(JSON.stringify(input.endsWith('/scripts')?[script,other]:[]))}));
 const props={base:'/projects/p',assets:[{id:'own',title:'开箱原片',inspection:'ready',source_kind:'own',media_type:'video',metadata:{duration_ms:6000}}],onUseScript:vi.fn(),onOpenReference:replay};
 render(<ScriptsWorkspace {...props}/>);
 fireEvent.click(await screen.findByRole('button',{name:'回看脚本 · v1'}));
 for(const label of ['镜头','画面怎么拍','台词 / 字幕','时长','用哪段素材'])expect(screen.getByRole('columnheader',{name:label})).toBeInTheDocument();
 fireEvent.change(screen.getByRole('textbox',{name:'第1行台词'}),{target:{value:'保留这段修改'}});
 fireEvent.click(screen.getByRole('button',{name:/^回看自有片段1.*开箱原片.*1–4秒/}));
 expect(replay).toHaveBeenCalledWith(expect.objectContaining({asset_id:'own',start_ms:1000,end_ms:4000}),true);
 expect(screen.getByText('修改尚未保存')).toBeInTheDocument();
 expect(screen.getByRole('button',{name:'DOCX'})).toBeDisabled();
 fireEvent.click(screen.getByRole('button',{name:'另一条脚本 · v1'}));
 fireEvent.click(screen.getByRole('button',{name:'回看脚本 · v1'}));
 expect(screen.getByRole('textbox',{name:'第1行台词'})).toHaveValue('保留这段修改');
 cleanup();const recovered=render(<ScriptsWorkspace {...props}/>);
 expect(await screen.findByRole('textbox',{name:'第1行台词'})).toHaveValue('保留这段修改');
 recovered.rerender(<ScriptsWorkspace {...props} base="/projects/other"/>);
 expect(screen.queryByDisplayValue('保留这段修改')).not.toBeInTheDocument();
 recovered.rerender(<ScriptsWorkspace {...props}/>);
 expect(screen.getByRole('textbox',{name:'第1行台词'})).toHaveValue('保留这段修改');
 expect(writes).toHaveLength(0);
});

test('saved scripts offer bounded edit callbacks while dirty drafts stay disabled',async()=>{
 const script={id:'s',version:4,title:'可继续修改',objective:'试拍',source:{session_id:'analysis',turn_index:1},rows:[{start_ms:0,end_ms:3000,dialogue:'原台词',visual:'近景',references:[]}],revisions:[]};
 const legacy={...script,id:'old',title:'手工脚本',source:undefined};
 const edits=vi.fn(),writes:any[]=[];
 vi.stubGlobal('fetch',vi.fn(async(input:string,init:any)=>{const localDraft=localDraftFixture(input,init);if(localDraft)return localDraft;if(init?.method)writes.push(init);return new Response(JSON.stringify(input.endsWith('/scripts')?[script,legacy]:[]))}));
 render(<ScriptsWorkspace base="/projects/p" assets={[]} onUseScript={edits}/>);
 fireEvent.click(await screen.findByRole('button',{name:'可继续修改 · v4'}));
 for(const label of ['换个开头','缩短到30秒','减少补拍']){const button=screen.getByRole('button',{name:label});expect(button).toBeEnabled();fireEvent.click(button)}
 expect(edits).toHaveBeenCalledTimes(3);
 expect(edits).toHaveBeenNthCalledWith(1,expect.objectContaining({id:'s',version:4,source:script.source,edit_instruction:expect.stringContaining('换个开头')}));
 fireEvent.change(screen.getByRole('textbox',{name:'第1行台词'}),{target:{value:'尚未保存'}});
 expect(screen.getByRole('button',{name:'换个开头'})).toBeDisabled();
 expect(screen.getByText('先保存修改，再调整这份稿。')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'手工脚本 · v4'}));
 expect(screen.getByRole('button',{name:'减少补拍'})).toBeEnabled();
 expect(writes).toHaveLength(0);
});

test('saved manual script can request AI revision without a source conversation',async()=>{const use=vi.fn();const script={id:'manual',version:4,title:'手工拍摄稿',objective:'试拍',rows:[{start_ms:0,end_ms:3000,dialogue:'台词',visual:'近景',references:[]}]};vi.stubGlobal('fetch',vi.fn(async(input:string,init:any)=>localDraftFixture(input,init)||new Response(JSON.stringify(input.endsWith('/scripts')?[script]:[]))));render(<ScriptsWorkspace base="/projects/p" assets={[]} onUseScript={use}/>);fireEvent.click(await screen.findByRole('button',{name:'手工拍摄稿 · v4'}));expect(screen.getByRole('button',{name:'换个开头'})).toBeEnabled();fireEvent.click(screen.getByRole('button',{name:'换个开头'}));expect(use).toHaveBeenCalledWith(expect.objectContaining({id:'manual',version:4,edit_instruction:expect.stringContaining('换个开头')}));});
