import {localDraftFixture} from './localDraftFixture';
import {afterEach,expect,test,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import ScriptsWorkspace from './scripts/ScriptsWorkspace';

afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.restoreAllMocks()});

function localScript(mediaType='video'){
 const image=mediaType==='image';
 return {id:'script',version:1,title:'安排拍摄',objective:'展示整理动作',confirmed:false,
  production_notes:['周四试读'],unresolved_items:[],rows:[{
   start_ms:0,end_ms:8000,dialogue:'把桌面整理好',visual:'桌面近景',notes:'保留原来机位',
   reshoot:'补拍开盖动作',references:[],own_references:[{
    asset_id:'own',start_ms:image?null:0,end_ms:image?null:200,
    observation:'原始选材说明',provenance:'sampled_frames',
   }],
  }],revisions:[]};
}

function setup(script:any,mediaType='video'){
 const writes:any[]=[];let stored=script;
 vi.stubGlobal('fetch',vi.fn(async(input:string,init:any)=>{const localDraft=localDraftFixture(input,init);if(localDraft)return localDraft;
  if(init?.method==='PUT'){
   writes.push(JSON.parse(init.body));stored={...stored,...writes.at(-1),version:2};
   return new Response(JSON.stringify(stored));
  }
  return new Response(JSON.stringify(input.endsWith('/scripts')?[stored]:[]));
 }));
 render(<ScriptsWorkspace base="/projects/p" assets={[{
  id:'own',title:'自有产品素材',source_kind:'own',inspection:'ready',media_type:mediaType,
  metadata:{duration_ms:10000,reuse_allowed:true},
 }]} onUseScript={vi.fn()}/>);
 return writes;
}

test('special display plan and ordinary notes survive save and reopen without widening source evidence',async()=>{
 const writes=setup(localScript());
 fireEvent.click(await screen.findByRole('button',{name:'安排拍摄 · v1'}));
 fireEvent.change(screen.getByRole('combobox',{name:'第1镜自有片段1播放方式'}),{target:{value:'freeze_frame'}});
 fireEvent.change(screen.getByRole('spinbutton',{name:'第1镜自有片段1计划使用时长'}),{target:{value:'8'}});
 fireEvent.change(screen.getByRole('textbox',{name:'第1镜自有片段1播放安排说明'}),{target:{value:'取末帧定格展示'}});
 fireEvent.change(screen.getByRole('spinbutton',{name:'第1镜计划补拍时长'}),{target:{value:'0'}});
 fireEvent.click(screen.getByText('脚本信息与版本'));
 fireEvent.change(screen.getByRole('textbox',{name:'制作备注'}),{target:{value:'周五试拍\n注意灯光'}});
 fireEvent.click(screen.getByRole('button',{name:'保存新版本'}));
 await waitFor(()=>expect(writes).toHaveLength(1));
 expect(writes[0]).toMatchObject({
  expected_version:1,confirmed:false,production_notes:['周五试拍','注意灯光'],
  rows:[expect.objectContaining({notes:'保留原来机位',reshoot_duration_ms:0,own_references:[{
   asset_id:'own',start_ms:0,end_ms:200,observation:'原始选材说明',provenance:'sampled_frames',
   usage_mode:'freeze_frame',use_duration_ms:8000,usage_note:'取末帧定格展示',
  }]})],
 });
 fireEvent.click(await screen.findByRole('button',{name:'安排拍摄 · v2'}));
 expect(screen.getByRole('combobox',{name:'第1镜自有片段1播放方式'})).toHaveValue('freeze_frame');
 expect(screen.getByRole('spinbutton',{name:'第1镜自有片段1计划使用时长'})).toHaveValue(8);
 expect(screen.getByRole('textbox',{name:'制作备注',hidden:true})).toHaveValue('周五试拍\n注意灯光');
});

test('whole image display duration remains separate from a fabricated video time range',async()=>{
 const writes=setup(localScript('image'),'image');
 fireEvent.click(await screen.findByRole('button',{name:'安排拍摄 · v1'}));
 expect(screen.getAllByText(/整图/).length).toBeGreaterThan(0);
 expect(screen.queryByRole('combobox',{name:'第1镜自有片段1播放方式'})).not.toBeInTheDocument();
 fireEvent.change(screen.getByRole('spinbutton',{name:'第1镜自有片段1计划使用时长'}),{target:{value:'8'}});
 fireEvent.click(screen.getByRole('button',{name:'保存新版本'}));
 await waitFor(()=>expect(writes).toHaveLength(1));
 expect(writes[0].rows[0].own_references[0]).toMatchObject({
  asset_id:'own',start_ms:null,end_ms:null,use_duration_ms:8000,
 });
});

test('only manual pending items can be explicitly moved into production notes before saving',async()=>{
 const manual='周五调整灯光';
 const automatic='第1镜自有片段时长不足';
 const script={...localScript(),unresolved_items:[manual,automatic],validation_issues:[automatic]};
 const writes=setup(script);
 fireEvent.click(await screen.findByRole('button',{name:'安排拍摄 · v1'}));
 fireEvent.click(screen.getByText(/制作前需要确认/));
 expect(screen.getAllByRole('button',{name:/归入制作备注/})).toHaveLength(1);
 fireEvent.click(screen.getByRole('button',{name:'将第1项归入制作备注'}));
 expect(screen.queryByRole('button',{name:/归入制作备注/})).not.toBeInTheDocument();
 fireEvent.click(screen.getByText('脚本信息与版本'));
 expect(screen.getByRole('textbox',{name:'制作备注'})).toHaveValue('周四试读\n周五调整灯光');
 expect(screen.getByText(automatic)).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'保存新版本'}));
 await waitFor(()=>expect(writes).toHaveLength(1));
 expect(writes[0]).toMatchObject({
  production_notes:['周四试读',manual],unresolved_items:[automatic],validation_issues:[automatic],
 });
});
