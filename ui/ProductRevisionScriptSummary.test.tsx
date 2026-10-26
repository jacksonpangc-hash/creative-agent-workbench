import {afterEach,expect,test,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,within} from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import ScriptsWorkspace from './scripts/ScriptsWorkspace';
afterEach(()=>{cleanup();vi.unstubAllGlobals()});
test('editable script summarizes actual own footage, concrete reshoot and unreviewed claims',async()=>{
 const draft={id:'s',version:1,title:'真实拍摄稿',objective:'试拍',rows:[{start_ms:0,end_ms:3000,dialogue:'看一眼质地',visual:'手持展示',own_references:[{asset_id:'own',start_ms:1000,end_ms:4000}],reshoot:'补拍手指蘸取',factual_claims:[{statement:'适合随身携带',fact_ids:[],reviewed:false}]}]};
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>new Response(JSON.stringify(url.endsWith('/scripts')?[draft]:[]),{status:200})));
 render(<ScriptsWorkspace base="/projects/A" assets={[{id:'own',title:'质地实拍',source_kind:'own',inspection:'ready',metadata:{}}]} onUseScript={vi.fn()}/>);
 fireEvent.click(await screen.findByRole('button',{name:'真实拍摄稿 · v1'}));
 fireEvent.click(screen.getByText('拍摄准备',{exact:true}));const summary=screen.getByText('已有素材（1处）').closest('.script-preparation') as HTMLElement;
 expect(summary).toHaveTextContent('已有素材（1处）');expect(summary).toHaveTextContent('质地实拍 · 1–4秒');
 expect(summary).toHaveTextContent('第1镜 · 补拍手指蘸取');expect(summary).toHaveTextContent('第1镜 · 适合随身携带');
 fireEvent.change(screen.getByRole('textbox',{name:'第1镜补拍说明'}),{target:{value:'补拍瓶身细节'}});expect(summary).toHaveTextContent('第1镜 · 补拍瓶身细节');
 expect(within(summary).getByText('需要确认（1处）')).toBeInTheDocument();
});
