import {afterEach,expect,test,vi} from 'vitest';
import {render,screen,fireEvent,cleanup} from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import {ModeGuide,StrategyComposer} from './strategy/StrategyWorkspace';
afterEach(()=>cleanup());

// Catches examples accidentally sending a billable request or mutating confirmed filters.
test('mode guide uses a brief task description without instruction-manual labels',()=>{
 const example=vi.fn();render(<ModeGuide mode="exploration" onExample={example}/>);
 expect(screen.getByText('从一批素材里找共性、变化和下一轮选题。')).toBeInTheDocument();
 expect(screen.queryByText('准备什么')).not.toBeInTheDocument();expect(screen.queryByText('会得到什么')).not.toBeInTheDocument();expect(example).not.toHaveBeenCalled();
});

// Catches @ keyboard/IME handlers swallowing Chinese composition or widening scope silently.
test('mention picker supports keyboard and leaves IME enter alone',()=>{
 const change=vi.fn(),pick=vi.fn(),send=vi.fn();
 const props={value:'@',onChange:change,onPick:pick,onSubmit:send,disabled:false,models:<option>请选择模型</option>,binding:'',onBinding:vi.fn(),quote:'',onQuote:vi.fn(),currency:'CNY',status:'',choices:[{id:'source:finished',label:'成片',kind:'source',value:'finished'},{id:'industry:cmm',label:'蝉妈妈正式API',kind:'source',value:'industry',disabled:'素材通道尚未接通'}]};
 render(<StrategyComposer {...props}/>);
 const text=screen.getByRole('textbox',{name:'创作目标'});
 fireEvent.change(text,{target:{value:'@',selectionStart:1}});
 fireEvent.compositionStart(text);fireEvent.keyDown(text,{key:'Enter',isComposing:true});
 expect(pick).not.toHaveBeenCalled();expect(send).not.toHaveBeenCalled();
 fireEvent.compositionEnd(text);fireEvent.keyDown(text,{key:'ArrowDown'});fireEvent.keyDown(text,{key:'Enter'});
 expect(pick).toHaveBeenCalledWith(expect.objectContaining({value:'finished'}));
 expect(send).not.toHaveBeenCalled();
});

// The analysis intent controls media transmission, independently from advanced model choice.
test('composer exposes video or spoken intent and only sends on explicit submission',()=>{
 const intent=vi.fn(),send=vi.fn();
 render(<StrategyComposer value="分析" onChange={vi.fn()} onPick={vi.fn()} onSubmit={send} disabled={false} models={<option value="m">测试模型</option>} binding="m" onBinding={vi.fn()} quote="1" onQuote={vi.fn()} currency="CNY" status="" choices={[]} analysisKind="video" onAnalysisKind={intent}/>);
 fireEvent.change(screen.getByRole('combobox',{name:'分析方式'}),{target:{value:'spoken'}});
 expect(intent).toHaveBeenCalledWith('spoken');
 expect(send).not.toHaveBeenCalled();
 expect(screen.getByRole('combobox',{name:'分析方式'})).toBeInTheDocument();
});

test('normal composer does not require per-task monetary input',()=>{
 render(<StrategyComposer value="" onChange={vi.fn()} onPick={vi.fn()} onSubmit={vi.fn()} disabled={false} models={<option/>} binding="" onBinding={vi.fn()} quote="1" onQuote={vi.fn()} currency="CNY" status="" choices={[]}/>);
 expect(screen.queryByRole('textbox',{name:'分析费用预留'})).not.toBeInTheDocument();
 expect(screen.queryByText('本次设置')).not.toBeInTheDocument();
 expect(screen.queryByRole('textbox',{name:'费用上限'})).not.toBeInTheDocument();
});

test('saved session keeps its mode and full script saves the selected script only',async()=>{
 const {default:Workspace}=await import('./strategy/StrategyWorkspace');const save=vi.fn();
 const composer=<StrategyComposer value="" onChange={vi.fn()} onPick={vi.fn()} onSubmit={vi.fn()} disabled={false} models={<option/>} binding="" onBinding={vi.fn()} quote="1" onQuote={vi.fn()} currency="CNY" status="" choices={[]}/>;
 const {container}=render(<Workspace mode="inspiration" onMode={vi.fn()} session={{mode:'inspiration',scope:{asset_ids:[]},turns:[{question:'写脚本',answer:{summary:'完成',result:{scripts:[{title:'脚本 A',objective:'试拍',rows:[{end_ms:12000}]}]}},evidence:[]}]}} question="" onQuestion={vi.fn()} onExample={vi.fn()} pending={null} onApplyExample={vi.fn()} onDiscardExample={vi.fn()} composer={composer} onAsset={vi.fn()} onExcerpt={vi.fn()} onBrief={vi.fn()} onParse={vi.fn()} onReport={vi.fn()} onReview={vi.fn()} onScript={save}/>);
 expect(container.querySelector('.strategy-composer .mode-switch')).not.toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:'保存并编辑脚本'}));expect(save).toHaveBeenCalledWith(0,0);
});
