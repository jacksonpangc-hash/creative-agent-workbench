import {afterEach,expect,test,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import App from './App';

function setup(hasModel=true,failAction=false,initialDrafts:any[]=[],draftsGate?:Promise<void>,failDraftRead=false){
 let storedDrafts=initialDrafts;
 const calls:{path:string,body:any}[]=[];
 const model={id:'m',provider:'deepseek',label:'我的模型',role:'strategy',has_key:true,
  availability:{strategy:{available:true},video_analysis:{available:true},image_input:{available:true}}};
 const state:any={project:{id:'P',name:'Test1',currency:'CNY'},budget:{limit:null,available:null,reserved:'0',spent:'0',currency:'CNY'},
  assets:[{id:'a',title:'绿豆饼',media_type:'video',source_kind:'own',inspection:'ready',metadata:{frame_count:1714,duration_ms:57000},
   readiness:{summary:'原片可播放 · 台词待识别 · 画面尚未分析'}}],models:hasModel?[model]:[],sessions:[],notes:[],instructions:[],parses:[],templates:[],jobs:[],groups:[],scripts:[]};
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{
  const path=String(input),body=init?.body?JSON.parse(String(init.body)):null;calls.push({path,body});
  const response=(v:any)=>new Response(JSON.stringify(v));
  if(path.endsWith('/projects'))return response([{id:'P',name:'Test1'}]);
  if(path.endsWith('/catalog'))return response({providers:[]});
  if(path.endsWith('/preferences'))return response({default_binding_id:hasModel?'m':'',version:1});
  if(path.endsWith('/state'))return response(state);
  if(path.endsWith('/drafts')){if(draftsGate)await draftsGate;if(failDraftRead){failDraftRead=false;return new Response(JSON.stringify({detail:'暂时不可用'}),{status:503})}return response({drafts:storedDrafts,active_identity:storedDrafts[0]?.identity||null})}
  if(path.includes('/drafts/')&&body){const identity=path.split('/').at(-1);const saved={identity,version:body.expected_version+1,content:body.content};storedDrafts=[saved,...storedDrafts.filter(d=>d.identity!==identity)];return response(saved)}
  if(path.endsWith('/actions')){
   if(failAction)return new Response(JSON.stringify({detail:'模型服务暂时无法响应，输入已保留。'}),{status:400});
   if(body.question.includes('查询')){
    state.sessions=[{id:'local',title:body.question,mode:'directed',discussion_only:true,local_only:true,scope:{asset_ids:[]},
     turns:[{question:body.question,answer:{summary:'当前项目有 1 条素材，台词待识别。',findings:[]},evidence:[]}]}];
    return response({id:'local',session_id:'local',local_only:true,state:'succeeded'});
   }
   return response({id:'j',state:'queued'});
  }
  if(path.endsWith('/knowledge/search'))return response({own_footage:[{asset_id:'a'}],references:[]});
  if(path.endsWith('/scenes')||path.endsWith('/frames'))return response([]);
  return response({});
 }));
 render(<App/>);return calls;
}
afterEach(()=>{cleanup();localStorage.clear();sessionStorage.clear();vi.unstubAllGlobals()});

test('normal creation starts without entering a monetary limit',async()=>{
 const calls=setup();const input=await screen.findByRole('textbox',{name:'创作目标'});
 fireEvent.change(input,{target:{value:'写一条完整脚本'}});
 const submit=screen.getByRole('button',{name:'生成拍摄脚本'});
 await waitFor(()=>expect(submit).toBeEnabled());fireEvent.click(submit);
 await waitFor(()=>expect(calls.some(c=>c.path.endsWith('/actions'))).toBe(true));
 expect(screen.queryByRole('textbox',{name:'费用上限'})).not.toBeInTheDocument();
 await waitFor(()=>expect(submit).toBeDisabled());
});

test('local material inspection needs no model and opens its result',async()=>{
 const calls=setup(false);const input=await screen.findByRole('textbox',{name:'创作目标'});
 fireEvent.change(input,{target:{value:'请帮我查询一下我的素材问题。'}});
 fireEvent.click(screen.getByRole('button',{name:'发送'}));
 await screen.findByText('当前项目有 1 条素材，台词待识别。');
 expect(calls.filter(c=>c.path.endsWith('/actions'))).toHaveLength(1);
 expect(screen.queryByText('正在读取素材并整理分析…')).not.toBeInTheDocument();
 expect(screen.queryByText('旧版依据保留在原创作记录中。')).not.toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'拆解画面'})).not.toBeInTheDocument();
});

test('material list shows understanding state rather than total video frames',async()=>{
 setup();fireEvent.click(await screen.findByRole('button',{name:'我的素材'}));
 expect(await screen.findByText('原片可播放 · 台词待识别 · 画面尚未分析')).toBeInTheDocument();
 expect(screen.queryByText(/1714帧已索引/)).not.toBeInTheDocument();
});

test('content search uses local knowledge with no external embedding form',async()=>{
 const calls=setup(false);fireEvent.click(await screen.findByRole('button',{name:'我的素材'}));
 fireEvent.change(screen.getByRole('textbox',{name:'搜索素材'}),{target:{value:'绿豆饼'}});
 fireEvent.change(screen.getByRole('combobox',{name:'搜索方式'}),{target:{value:'semantic'}});
 fireEvent.click(screen.getByRole('button',{name:'执行搜索'}));
 await waitFor(()=>expect(calls.some(c=>c.path.endsWith('/knowledge/search'))).toBe(true));
 expect(screen.queryByRole('combobox',{name:'向量模型'})).not.toBeInTheDocument();
});

test('a material can enter analysis without leaving details to select it again',async()=>{
 setup();fireEvent.click(await screen.findByRole('button',{name:'我的素材'}));
 fireEvent.click(await screen.findByRole('button',{name:'绿豆饼画面'}));
 fireEvent.click(await screen.findByRole('button',{name:'在对话中分析这条素材'}));
 expect(screen.getByRole('textbox',{name:'创作目标'})).toHaveValue('分析这条素材的开头、卖点表达和镜头安排，标出对应片段。');
 expect(screen.getByLabelText('本次素材')).toHaveTextContent('绿豆饼');
});

test('model error belongs to the task and does not cover material management',async()=>{
 setup(true,true);fireEvent.change(await screen.findByRole('textbox',{name:'创作目标'}),{target:{value:'写一条完整脚本'}});
 const send=screen.getByRole('button',{name:'生成拍摄脚本'});await waitFor(()=>expect(send).toBeEnabled());fireEvent.click(send);
 await screen.findByText('模型服务暂时无法响应，输入已保留。');
 fireEvent.click(screen.getByRole('button',{name:'我的素材'}));
 expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

test('user can select spoken analysis without provider-purpose parameters',async()=>{
 const calls=setup();fireEvent.change(await screen.findByRole('textbox',{name:'创作目标'}),{target:{value:'写一条完整脚本'}});
 fireEvent.change(screen.getByRole('combobox',{name:'分析方式'}),{target:{value:'spoken'}});
 const send=screen.getByRole('button',{name:'生成拍摄脚本'});await waitFor(()=>expect(send).toBeEnabled());fireEvent.click(send);
 await waitFor(()=>expect(calls.some(c=>c.path.endsWith('/actions'))).toBe(true));
 expect(calls.find(c=>c.path.endsWith('/actions'))?.body.analysis_kind).toBe('spoken');
 expect(screen.queryByRole('combobox',{name:'用途'})).not.toBeInTheDocument();
});

test('single-material analysis clears stale draft groups, references and date restrictions',async()=>{
 sessionStorage.setItem('creative-task-drafts-v2',JSON.stringify({'P:directed':{
  question:'旧问题',selected:['other'],referenceIds:['old-reference'],activeGroups:['old-group'],
  startDate:'2020-01-01',endDate:'2020-01-02',scopeSources:['industry'],discovery:true,query:'旧范围',ownAssetIds:['other'],useHistory:true
 }}));
 const calls=setup();fireEvent.click(await screen.findByRole('button',{name:'我的素材'}));
 fireEvent.click(await screen.findByRole('button',{name:'绿豆饼画面'}));
 fireEvent.click(await screen.findByRole('button',{name:'在对话中分析这条素材'}));
 expect(screen.getByLabelText('本次素材')).not.toHaveTextContent('行业参考');
 const send=screen.getByRole('button',{name:'发送'});await waitFor(()=>expect(send).toBeEnabled());fireEvent.click(send);
 await waitFor(()=>expect(calls.some(c=>c.path.endsWith('/actions'))).toBe(true));
 const scope=calls.find(c=>c.path.endsWith('/actions'))?.body.scope;
 expect(scope.asset_ids).toEqual(['a']);expect(scope.group_ids).toEqual([]);expect(scope.reference_ids).toEqual([]);
 expect(scope.start_date).toBeNull();expect(scope.end_date).toBeNull();expect(scope.discovery).toBe(false);
});


test('reopening a workspace restores its server draft without browser storage',async()=>{
 localStorage.clear();sessionStorage.clear();
 setup(true,false,[{identity:'directed',version:3,content:{analysisMode:'directed',question:'继续比较这条视频的开头',selected:['a'],analysisKind:'video'}}]);
 expect(await screen.findByRole('textbox',{name:'创作目标'})).toHaveValue('继续比较这条视频的开头');
 expect(screen.getByLabelText('本次素材')).toHaveTextContent('绿豆饼');
});

test('edited input is persisted to the current project without sending to a model',async()=>{
 const calls=setup();const input=await screen.findByRole('textbox',{name:'创作目标'});
 fireEvent.change(input,{target:{value:'明天继续写这个开场'}});
 await waitFor(()=>expect(calls.some(c=>c.path.includes('/drafts/')&&c.body?.content?.question==='明天继续写这个开场')).toBe(true));
 expect(calls.some(c=>c.path.endsWith('/actions'))).toBe(false);
});

test('changing a quick task keeps current material scope while restoring its own question draft',async()=>{
 sessionStorage.setItem('creative-task-drafts-v2',JSON.stringify({'P:directed':{question:'比较之前写好的问题',selected:['stale'],referenceIds:['stale-reference'],activeGroups:['stale-group']}}));
 setup();fireEvent.click(await screen.findByRole('button',{name:'我的素材'}));
 fireEvent.click(await screen.findByRole('checkbox',{name:'选择素材：绿豆饼'}));
 fireEvent.click(screen.getByRole('button',{name:'创作台'}));
 fireEvent.change(screen.getByRole('textbox',{name:'创作目标'}),{target:{value:'写稿的目标继续保留'}});
 fireEvent.click(screen.getByRole('button',{name:'拆视频'}));
 expect(screen.getByRole('textbox',{name:'创作目标'})).toHaveValue('比较之前写好的问题');
 expect(screen.getByLabelText('本次素材')).toHaveTextContent('绿豆饼');
 expect(screen.getByLabelText('本次素材')).not.toHaveTextContent('行业参考');
 fireEvent.click(screen.getByRole('button',{name:'写脚本'}));
 expect(screen.getByRole('textbox',{name:'创作目标'})).toHaveValue('写稿的目标继续保留');
 expect(screen.getByLabelText('本次素材')).toHaveTextContent('绿豆饼');
});


test('material selection waits for draft restoration, then remains selected',async()=>{
 let release!:()=>void;const gate=new Promise<void>(resolve=>{release=resolve});
 setup(true,false,[{identity:'inspiration',version:2,content:{question:'服务器草稿',selected:[]}}],gate);
 fireEvent.click(await screen.findByRole('button',{name:'我的素材'}));
 const item=await screen.findByRole('checkbox',{name:'选择素材：绿豆饼'});
 expect(item).toBeDisabled();release();
 await waitFor(()=>expect(item).toBeEnabled());fireEvent.click(item);
 fireEvent.click(screen.getByRole('button',{name:'创作台'}));
 expect(await screen.findByRole('textbox',{name:'创作目标'})).toHaveValue('服务器草稿');
 expect(screen.getByLabelText('本次素材')).toHaveTextContent('绿豆饼');
});

test('a failed conversation draft read can be retried without writing an empty replacement',async()=>{
 const calls=setup(true,false,[{identity:'directed',version:3,content:{analysisMode:'directed',question:'本机需要保留的问题',selected:['a']}}],undefined,true);
 const retry=await screen.findByRole('button',{name:'重试读取草稿'});
 expect(calls.some(c=>c.path.includes('/drafts/')&&c.body)).toBe(false);
 fireEvent.click(retry);
 expect(await screen.findByRole('textbox',{name:'创作目标'})).toHaveValue('本机需要保留的问题');
 expect(screen.getByLabelText('本次素材')).toHaveTextContent('绿豆饼');
});
