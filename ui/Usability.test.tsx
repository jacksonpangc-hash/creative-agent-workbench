import {afterEach,expect,test,vi} from 'vitest';
import {render,screen,fireEvent,cleanup} from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import PromptPresets from './strategy/PromptPresets';
import ModelServiceSettings from './settings/ModelServiceSettings';
afterEach(()=>cleanup());
test('task presets fill an editable draft immediately without parameter forms',()=>{
 const draft=vi.fn();render(<PromptPresets mode="directed" onDraft={draft}/>);
 fireEvent.click(screen.getByRole('button',{name:'心智策略组合分析'}));
 expect(draft).toHaveBeenCalledTimes(1);
 expect(draft).toHaveBeenCalledWith(expect.objectContaining({text:expect.stringContaining('痛点、卖点、信任和促单'),sourceKinds:[]}));
 expect(draft.mock.calls[0][0].text).toContain('缺失处标为待补充');
 expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
 expect(screen.queryByRole('textbox',{name:'品牌'})).not.toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'填入输入框'})).not.toBeInTheDocument();
});
test('analysis settings expose GPT and Claude through built-in video processing without five purpose choices',()=>{
 const catalog=[{id:'openai',name:'OpenAI',country:'美国',models:[{id:'gpt-test',name:'GPT视觉',availability:{strategy:{available:true},video_analysis:{available:true,label:'画面与台词'},image_input:{available:true}}}]},{id:'anthropic',name:'Anthropic',country:'美国',models:[{id:'claude-test',name:'Claude视觉',availability:{strategy:{available:true},video_analysis:{available:true},image_input:{available:true}}}]},{id:'unsupported',name:'未接通',models:[]}];
 render(<ModelServiceSettings base="/projects/A" models={[]} catalog={catalog} run={vi.fn()} reload={vi.fn()}/>);
 expect(screen.queryByRole('combobox',{name:'服务商'})).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'添加模型服务'}));
 expect(screen.queryByRole('combobox',{name:'用途'})).not.toBeInTheDocument();
 expect(screen.getByRole('option',{name:'OpenAI · 美国'})).toBeEnabled();
 expect(screen.getByRole('option',{name:'Anthropic · 美国'})).toBeEnabled();
 expect(screen.queryByRole('option',{name:/未接通/})).not.toBeInTheDocument();
 expect(screen.getAllByText(/画面.*台词/).length).toBeGreaterThan(0);
});
