import {afterEach,expect,test,vi} from 'vitest';
import {render,screen,fireEvent,cleanup,within,waitFor} from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import App from './App';
import PromptPresets from './strategy/PromptPresets';

afterEach(()=>{cleanup();sessionStorage.clear();localStorage.clear();vi.unstubAllGlobals()});
function workspace(assets:Record<string,any>[]=[]){
 vi.stubGlobal('fetch',vi.fn(async(input:string)=>new Response(JSON.stringify(input.endsWith('/projects')?[{id:'A',name:'绿豆饼'}]:input.endsWith('/catalog')?{providers:[]}:input.endsWith('/cmm')?{connected:false,capabilities:[],usage:[]}:/\/(scenes|frames)$/.test(input)?[]:{project:{id:'A',name:'绿豆饼',currency:'CNY'},budget:{},assets,models:[],sessions:[],notes:[],instructions:[],parses:[],templates:[],jobs:[],groups:[]}),{status:200})));
 render(<App/>);
}
test('primary navigation has only workbench and my materials, with source actions inside the conversation',async()=>{
 workspace();await screen.findByRole('textbox',{name:'创作目标'});
 const nav=screen.getByRole('navigation');
 expect(within(nav).getAllByRole('button').map(x=>x.textContent)).toEqual(['创作台','我的素材']);
 const composer=screen.getByRole('region',{name:'创作输入'});
 fireEvent.click(within(composer).getByText('添加',{exact:true}));for(const name of ['添加视频','粘贴链接','从素材库选择'])expect(within(composer).getByRole('button',{name})).toBeEnabled();
 expect(screen.queryByRole('button',{name:'视频仿拍'})).not.toBeInTheDocument();
 expect(screen.queryByRole('textbox',{name:'搜索历史分析'})).not.toBeInTheDocument();
});
test('presets put a useful editable task directly into the draft without a setup form',()=>{
 const onDraft=vi.fn();render(<PromptPresets mode="exploration" onDraft={onDraft}/>);
 fireEvent.click(screen.getByRole('button',{name:'批量仿写脚本'}));
 expect(onDraft).toHaveBeenCalledOnce();
 expect(onDraft.mock.calls[0][0].text).toContain('10');
 expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});
test('settings have one data source and no video generation configuration',async()=>{
 workspace();await screen.findByRole('textbox',{name:'创作目标'});
 fireEvent.click(screen.getByRole('button',{name:'设置'}));
 expect(screen.queryByRole('tab',{name:'视频生成'})).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'参考来源'}));
 await waitFor(()=>expect(screen.getByRole('heading',{name:'蝉妈妈'})).toBeInTheDocument());
 expect(screen.queryByText(/TikHub|DataX|响应字段映射/)).not.toBeInTheDocument();
});
test('asset details expose the saved transcript and its review warning beside the original video',async()=>{
 workspace([{id:'video-1',project_id:'A',title:'试吃口播',inspection:'ready',media_type:'video',source_kind:'owned',metadata:{duration_ms:8000,transcript:'打开包装，掰开绿豆饼。',transcription:{method:'whisper.cpp-local',reviewed:false,needs_review:true,warnings:['识别结果有较多相同片段，请核对原音频后再使用；尚未确认内容准确性。']}}}]);
 await screen.findByRole('textbox',{name:'创作目标'});
 fireEvent.click(screen.getByRole('button',{name:'我的素材'}));
 fireEvent.click(screen.getByRole('button',{name:'试吃口播画面'}));
 fireEvent.click(screen.getByRole('tab',{name:'台词'}));
 expect(screen.getByText('打开包装，掰开绿豆饼。')).toBeVisible();
 expect(screen.queryByRole('textbox',{name:'第1句台词'})).not.toBeInTheDocument();
 expect(screen.getByText('台词需要核对，画面分析可以先看。')).toBeVisible();
});
