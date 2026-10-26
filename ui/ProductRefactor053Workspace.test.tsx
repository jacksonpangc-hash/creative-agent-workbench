import {afterEach,expect,test,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import App from './App';
const response=(value:unknown)=>new Response(JSON.stringify(value));
function setup(){
 const calls:{path:string,body:any}[]=[];
 const model={id:'m',provider:'deepseek',label:'我的模型',model_id:'deepseek-flash',role:'strategy',has_key:true,availability:{strategy:{available:true},video_analysis:{available:true},image_input:{available:true}}};
 const state={project:{id:'P',name:'演示项目',currency:'CNY'},budget:{limit:'5',available:'5',reserved:'0',spent:'0',currency:'CNY'},assets:[],models:[model],sessions:[],notes:[],instructions:[],parses:[],templates:[],jobs:[],groups:[],scripts:[]};
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{
  const path=String(input);const body=init?.body?JSON.parse(String(init.body)):null;calls.push({path,body});
  if(path.endsWith('/projects'))return response([{id:'P',name:'演示项目'}]);
  if(path.endsWith('/catalog'))return response({providers:[]});
  if(path.endsWith('/state'))return response(state);
  if(path.endsWith('/preferences'))return response({default_binding_id:'m',default_quote:'',version:1});
  if(path.endsWith('/actions'))return response({id:'j',state:'queued'});
  if(path.endsWith('/references')||path.endsWith('/plans'))return response([]);
  return response({});
 }));return calls;
}
afterEach(()=>{cleanup();sessionStorage.clear();localStorage.clear();vi.unstubAllGlobals();vi.restoreAllMocks()});
test('task input has one switch, no technical parameter form, and preserves separate goals',async()=>{
 setup();render(<App/>);
 await screen.findByRole('button',{name:'发送'},{timeout:5000});
 const input=screen.getByRole('textbox',{name:'创作目标'});
 fireEvent.change(input,{target:{value:'商品介绍草稿'}});
 fireEvent.click(screen.getByRole('button',{name:'拆视频'}));
 expect(input).toHaveValue('');
 fireEvent.change(input,{target:{value:'比较视频开头'}});
 fireEvent.click(screen.getByRole('button',{name:'写脚本'}));
 expect(input).toHaveValue('商品介绍草稿');
 expect(screen.queryByRole('group',{name:'策略分析模式'})).not.toBeInTheDocument();
 expect(screen.queryByLabelText('分析费用预留')).not.toBeInTheDocument();
 expect(screen.queryByLabelText('分析模型')).not.toBeInTheDocument();
 expect(screen.getAllByText('添加',{exact:true})).toHaveLength(1);
 expect(screen.queryByRole('textbox',{name:'搜索历史分析'})).not.toBeInTheDocument();
});
test('script generation sends a structured task and inherits the service without a client quote',async()=>{
 const calls=setup();render(<App/>);
 const input=await screen.findByRole('textbox',{name:'创作目标'});
 fireEvent.change(input,{target:{value:'介绍这款点心'}});
 await waitFor(()=>expect(screen.getByRole('button',{name:'发送'})).toBeEnabled());
 fireEvent.click(screen.getByRole('button',{name:'发送'}));
 await waitFor(()=>expect(calls.some(c=>c.path.endsWith('/actions'))).toBe(true));
 const payload=calls.find(c=>c.path.endsWith('/actions'))!.body;
 expect(payload).toMatchObject({task_intent:'script',question:'介绍这款点心',binding_id:'m',discussion_only:true});
 expect(payload).not.toHaveProperty('quote');
});
test('reference search remains available without requiring a model operation',async()=>{
 const calls=setup();render(<App/>);await screen.findByRole('textbox',{name:'创作目标'});
 fireEvent.click(screen.getByRole('button',{name:'找参考'}));
 fireEvent.change(screen.getByRole('textbox',{name:'创作目标'}),{target:{value:'绿豆饼'}});
 fireEvent.click(screen.getByRole('button',{name:'搜索收藏'}));
 await screen.findByRole('tab',{name:'我的收藏'});
 expect(calls.some(c=>c.path.endsWith('/actions'))).toBe(false);
});

import {automaticAnalysisKind} from './strategy/taskRouting';
test('text-only service uses reliable supplied subtitles, never unreliable automatic speech as video',()=>{
 const text={has_key:true,availability:{strategy:{available:true},video_analysis:{available:false}}};
 const a={metadata:{transcript:'已核对台词',subtitles:[{text:'已核对台词'}],transcript_source:'user_import'}};
 expect(automaticAnalysisKind([a],[text],text)).toBe('spoken');
 expect(automaticAnalysisKind([{metadata:{transcript:'重复',transcription:{needs_review:true,reviewed:false}}}],[text],text)).toBe('video');
});
