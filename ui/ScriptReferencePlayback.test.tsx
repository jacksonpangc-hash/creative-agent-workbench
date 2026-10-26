import {afterEach,expect,test,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,within} from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import ScriptsWorkspace from './scripts/ScriptsWorkspace';
import App from './App';

afterEach(()=>{cleanup();sessionStorage.clear();localStorage.clear();vi.unstubAllGlobals();vi.restoreAllMocks()});
const own={asset_id:'own',start_ms:1200,end_ms:4600,observation:'展示盒盖',provenance:'metadata'};
const reference={asset_id:'ref',start_ms:5000,end_ms:7500,observation:'参考开头节奏',provenance:'sampled_frames'};
const assets=[
 {id:'own',title:'盒盖实拍',source_kind:'own',inspection:'ready',media_type:'video',metadata:{duration_ms:10000,reuse_allowed:true}},
 {id:'ref',title:'行业参考',source_kind:'industry',inspection:'ready',media_type:'video',metadata:{duration_ms:12000}},
];
const script={id:'s',version:1,title:'拍摄稿',objective:'展示整理动作',timing_estimated:true,rows:[{start_ms:0,end_ms:30000,dialogue:'打开盒盖',visual:'手部近景',notes:'',references:[reference],own_references:[own]}]};
const response=(value:unknown)=>new Response(JSON.stringify(value));
function setup(value:any=script,currentAssets:any[]=assets){
 vi.stubGlobal('fetch',vi.fn(async(input:string)=>response(input.endsWith('/scripts')?[value]:[])));
 const opened:any[]=[];
 render(<ScriptsWorkspace base="/projects/A" assets={currentAssets} onUseScript={vi.fn()} onOpenReference={ref=>opened.push(ref)}/>);
 return opened;
}
async function openScript(){fireEvent.click(await screen.findByRole('button',{name:'拍摄稿 · v1'}));fireEvent.click(screen.getByText('镜头详情与依据'))}
function preparation(){const summary=screen.getByText('拍摄准备');fireEvent.click(summary);return summary.closest('details') as HTMLElement}

test('summary and editable row replay the exact own source interval, including current unsaved time edits',async()=>{
 const opened=setup();await openScript();
 fireEvent.click(within(preparation()).getByRole('button',{name:/回看.*盒盖实拍/}));
 expect(opened[0]).toMatchObject({asset_id:'own',start_ms:1200,end_ms:4600,observation:'展示盒盖',provenance:'metadata'});
 fireEvent.change(screen.getByRole('spinbutton',{name:'第1镜自有片段1起点'}),{target:{value:'2'}});
 fireEvent.click(screen.getByRole('button',{name:/^回看自有片段1.*盒盖实拍/}));
 expect(opened[1]).toMatchObject({asset_id:'own',start_ms:2000,end_ms:4600,provenance:'metadata'});
});

test('reference citation replays its own file, source interval and provenance',async()=>{
 const opened=setup();await openScript();
 fireEvent.click(screen.getByRole('button',{name:/回看参考.*行业参考/}));
 expect(opened).toEqual([reference]);
});

test('missing project assets and invalid video intervals remain disabled with a clear reason',async()=>{
 const opened=setup({...script,rows:[{...script.rows[0],references:[{...reference,asset_id:'other-project'}],own_references:[{...own,end_ms:1200}]}]});await openScript();
 expect(screen.getByRole('button',{name:/回看参考/})).toBeDisabled();
 expect(screen.getByText(/原素材已移除或不在当前项目/)).toBeInTheDocument();
 expect(screen.getByRole('button',{name:/^回看自有片段1.*盒盖实拍/})).toBeDisabled();
 expect(screen.getAllByText(/这段时间无法定位/).length).toBeGreaterThan(0);
 expect(opened).toEqual([]);
});

test('references outside the saved source selection or frozen time range cannot be replayed',async()=>{
 const source={scope:{asset_ids:['ref'],selected_segments:[{asset_id:'ref',start_ms:5500,end_ms:7500}]},selection_snapshot:{own_assets:[]}};
 const opened=setup({...script,source});await openScript();
 expect(screen.getByRole('button',{name:/回看参考/})).toBeDisabled();
 expect(screen.getByText(/这段时间超出了原分析片段/)).toBeInTheDocument();
 expect(screen.getByRole('button',{name:/^回看自有片段1.*盒盖实拍/})).toBeDisabled();
 expect(screen.getAllByText(/这条素材不在当前脚本的来源中/).length).toBeGreaterThan(0);
 expect(opened).toEqual([]);
});

test('unfinished media cannot be replayed while its source is still processing',async()=>{
 setup(script,assets.map(a=>({...a,inspection:'processing'})));await openScript();
 expect(screen.getByRole('button',{name:/回看参考/})).toBeDisabled();
 expect(screen.getAllByText(/素材尚未就绪/).length).toBeGreaterThan(0);
});

test('whole image replay preserves null endpoints instead of inventing a video interval',async()=>{
 const image={...own,start_ms:null,end_ms:null,provenance:'image'};
 const opened=setup({...script,rows:[{...script.rows[0],own_references:[image]}]},[{...assets[0],media_type:'image'}]);await openScript();
 fireEvent.click(within(preparation()).getByRole('button',{name:/回看.*整图/}));
 expect(opened[0]).toMatchObject({asset_id:'own',start_ms:null,end_ms:null,provenance:'image'});
});

test('estimated timing is explained without classifying a full script as a legacy outline',async()=>{
 setup();await openScript();
 expect(screen.getByText(/时长为估算.*试读和试拍/)).toBeInTheDocument();
 expect(screen.queryByText(/历史大纲/)).not.toBeInTheDocument();
});

test('App opens cited source at its exact interval and keeps dirty script edits after closing the player',async()=>{
 vi.stubGlobal('fetch',vi.fn(async(input:string)=>{
  if(input.endsWith('/projects'))return response([{id:'A',name:'项目A'}]);
  if(input.endsWith('/catalog'))return response({providers:[]});
  if(input.endsWith('/scripts'))return response([script]);
  if(input.endsWith('/state'))return response({project:{id:'A',name:'项目A',currency:'CNY'},budget:{},assets,models:[],sessions:[],notes:[],parses:[],templates:[],jobs:[],groups:[],instructions:[]});
  return response([]);
 }));
 vi.stubGlobal('URL',Object.assign(URL,{createObjectURL:vi.fn(()=> 'blob:local-media'),revokeObjectURL:vi.fn()}));
 render(<App/>);
 fireEvent.click(await screen.findByRole('button',{name:'全部创作'}));
 fireEvent.click(screen.getByRole('button',{name:'脚本'}));await openScript();
 fireEvent.change(screen.getByRole('textbox',{name:'第1行台词'}),{target:{value:'尚未保存的试拍台词'}});
 fireEvent.click(within(preparation()).getByRole('button',{name:/回看.*盒盖实拍/}));
 const dialog=await screen.findByRole('dialog',{name:'盒盖实拍'});
 const player=within(dialog).getByLabelText('素材播放器') as HTMLVideoElement;
 fireEvent.loadedMetadata(player);expect(player.currentTime).toBe(1.2);
 expect(within(dialog).getByText(/已选 00:01–00:04/)).toBeInTheDocument();
 fireEvent.click(within(dialog).getByText('素材信息',{exact:true}));
 expect(within(dialog).getByText('素材 ID：own')).toBeInTheDocument();
 fireEvent.click(within(dialog).getByRole('button',{name:'关闭'}));
 expect(screen.getByRole('textbox',{name:'第1行台词'})).toHaveValue('尚未保存的试拍台词');
 expect(screen.getByText('修改尚未保存')).toBeInTheDocument();
 expect(screen.getByRole('button',{name:'MD'})).toBeDisabled();
});

test('dirty script replay blocks add-to-dialogue in the modal and retains edits after clicking that action',async()=>{
 vi.stubGlobal('fetch',vi.fn(async(input:string)=>{
  if(input.endsWith('/projects'))return response([{id:'A',name:'项目A'}]);
  if(input.endsWith('/catalog'))return response({providers:[]});
  if(input.endsWith('/scripts'))return response([script]);
  if(input.endsWith('/state'))return response({project:{id:'A',name:'项目A',currency:'CNY'},budget:{},assets,models:[],sessions:[],notes:[],parses:[],templates:[],jobs:[],groups:[],instructions:[]});
  return response([]);
 }));
 vi.stubGlobal('URL',Object.assign(URL,{createObjectURL:vi.fn(()=> 'blob:local-media'),revokeObjectURL:vi.fn()}));
 render(<App/>);
 fireEvent.click(await screen.findByRole('button',{name:'全部创作'}));
 fireEvent.click(screen.getByRole('button',{name:'脚本'}));await openScript();
 fireEvent.change(screen.getByRole('textbox',{name:'第1行台词'}),{target:{value:'不能丢失的未保存台词'}});
 fireEvent.click(screen.getByRole('button',{name:/回看参考.*行业参考/}));
 const dialog=await screen.findByRole('dialog',{name:'行业参考'});
 fireEvent.loadedMetadata(within(dialog).getByLabelText('素材播放器'));
 const transfer=within(dialog).getByRole('button',{name:'将所选片段带入对话'});
 fireEvent.click(transfer);
 expect(screen.getByRole('textbox',{name:'第1行台词'})).toHaveValue('不能丢失的未保存台词');
 expect(transfer).toBeDisabled();
 expect(within(dialog).getByText(/脚本有未保存修改.*关闭回看.*保存新版本/)).toBeInTheDocument();
 expect(screen.getByRole('dialog',{name:'行业参考'})).toBeInTheDocument();
 fireEvent.click(within(dialog).getByRole('button',{name:'关闭'}));
 expect(screen.getByRole('textbox',{name:'第1行台词'})).toHaveValue('不能丢失的未保存台词');
 expect(screen.getByText('修改尚未保存')).toBeInTheDocument();
});

test('a saved script keeps the normal scoped add-to-dialogue action',async()=>{
 vi.stubGlobal('fetch',vi.fn(async(input:string)=>{
  if(input.endsWith('/projects'))return response([{id:'A',name:'项目A'}]);
  if(input.endsWith('/catalog'))return response({providers:[]});
  if(input.endsWith('/scripts'))return response([script]);
  if(input.endsWith('/state'))return response({project:{id:'A',name:'项目A',currency:'CNY'},budget:{},assets,models:[],sessions:[],notes:[],parses:[],templates:[],jobs:[],groups:[],instructions:[]});
  return response([]);
 }));
 vi.stubGlobal('URL',Object.assign(URL,{createObjectURL:vi.fn(()=> 'blob:local-media'),revokeObjectURL:vi.fn()}));
 render(<App/>);
 fireEvent.click(await screen.findByRole('button',{name:'全部创作'}));
 fireEvent.click(screen.getByRole('button',{name:'脚本'}));await openScript();
 fireEvent.click(screen.getByRole('button',{name:/回看参考.*行业参考/}));
 const dialog=await screen.findByRole('dialog',{name:'行业参考'});
 fireEvent.loadedMetadata(within(dialog).getByLabelText('素材播放器'));
 const transfer=within(dialog).getByRole('button',{name:'将所选片段带入对话'});
 expect(transfer).toBeEnabled();fireEvent.click(transfer);
 expect(screen.queryByRole('dialog',{name:'行业参考'})).not.toBeInTheDocument();
 expect(screen.getByRole('textbox',{name:'创作目标'})).toHaveValue('参考《行业参考》00:05–00:07：参考开头节奏');
});
