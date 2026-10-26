import {afterEach,expect,test,vi} from 'vitest';
import {render,screen,fireEvent,cleanup,waitFor} from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import LinkedMediaDetails from './media/LinkedMediaDetails';
afterEach(()=>{cleanup();vi.unstubAllGlobals()});
const asset={id:'a',title:'绿豆饼',media_type:'video',inspection:'ready',transcript_policy:{allowed:true,quality_state:'usable',execution_state:'succeeded',review_state:'unreviewed'},metadata:{duration_ms:8000,subtitles:[{start_ms:0,end_ms:3000,text:'打开包装'},{start_ms:3000,end_ms:8000,text:'掰开看酥皮'}],transcript:'打开包装\n掰开看酥皮'}};
function viewFixture(state:any){return {asset,results:[...(state.sessions||[]).map((record:any)=>({id:record.id,kind:'session',version:1,title:'分析',record})),...(state.parses||[]).map((record:any)=>({id:record.id,kind:'parse',version:1,title:'拆解',record}))]}}
function setup(){const writes:any[]=[];vi.stubGlobal('fetch',vi.fn(async(input:string,init:any)=>{if(input.includes('/media-ticket/'))return new Response(JSON.stringify({streaming:false}));if(init?.method==='POST'){writes.push(JSON.parse(init.body));return new Response('{}')}return new Response(JSON.stringify(input.endsWith('/scenes')?[{id:'scene',start_ms:3000,end_ms:8000}]:[]))}));const evidence=vi.fn();render(<LinkedMediaDetails base="/projects/p" asset={asset} reload={vi.fn()} onEvidence={evidence}/>);return{writes,evidence}}
test('transcript and scenes seek the same persistent player; selected actual fragment becomes evidence',async()=>{const{evidence}=setup();const video=screen.getByLabelText('素材播放器') as HTMLVideoElement;fireEvent.click(screen.getByRole('tab',{name:'台词'}));fireEvent.click(screen.getByRole('button',{name:/00:03.*掰开看酥皮/}));expect(video.currentTime).toBe(3);fireEvent.timeUpdate(video,{target:{currentTime:4}});expect(screen.getByRole('button',{name:/00:03.*掰开看酥皮/})).toHaveAttribute('aria-current','true');fireEvent.click(screen.getByRole('button',{name:'将所选片段带入对话'}));expect(evidence).toHaveBeenCalledWith(expect.objectContaining({asset_id:'a',start_ms:3000,end_ms:8000,observation:'掰开看酥皮',provenance:'subtitle'}));fireEvent.click(screen.getByRole('tab',{name:'分镜'}));fireEvent.click(await screen.findByRole('button',{name:/镜头.*00:03/}));expect(screen.getByLabelText('素材播放器')).toBe(video);expect(video.currentTime).toBe(3)});
test('correction saves timestamped subtitles through existing route and survives tab changes',async()=>{const{writes}=setup();fireEvent.click(screen.getByRole('tab',{name:'台词'}));fireEvent.click(screen.getByRole('button',{name:'校正文字稿'}));fireEvent.change(screen.getByRole('textbox',{name:'第2句台词'}),{target:{value:'掰开看看酥皮'}});fireEvent.click(screen.getByRole('tab',{name:'分析'}));fireEvent.click(screen.getByRole('tab',{name:'台词'}));expect(screen.getByRole('textbox',{name:'第2句台词'})).toHaveValue('掰开看看酥皮');fireEvent.click(screen.getByRole('button',{name:'保存文字稿校正'}));await waitFor(()=>expect(writes).toHaveLength(1));expect(writes[0].text).toContain('00:00:03,000 --> 00:00:08,000\n掰开看看酥皮')});
test('cancelling transcript correction discards unsaved words before evidence selection',()=>{const {evidence,writes}=setup();fireEvent.click(screen.getByRole('tab',{name:'台词'}));fireEvent.click(screen.getByRole('button',{name:'校正文字稿'}));fireEvent.change(screen.getByRole('textbox',{name:'第2句台词'}),{target:{value:'未保存改写'}});fireEvent.click(screen.getByRole('button',{name:'取消校正'}));expect(screen.queryByText('未保存改写')).not.toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:/00:03.*掰开看酥皮/}));fireEvent.click(screen.getByRole('button',{name:'将所选片段带入对话'}));expect(evidence).toHaveBeenCalledWith(expect.objectContaining({observation:'掰开看酥皮'}));expect(writes).toHaveLength(0)});
test('a scene selected in parse results opens the source player at that actual interval',()=>{vi.stubGlobal('fetch',vi.fn(async()=>new Response('[]')));render(<LinkedMediaDetails base="/projects/p" asset={asset} reload={vi.fn()} onEvidence={vi.fn()} initialFragment={{asset_id:'a',start_ms:3000,end_ms:8000,observation:'实际解析镜头',provenance:'sampled_frames'}}/>);const video=screen.getByLabelText('素材播放器') as HTMLVideoElement;fireEvent.loadedMetadata(video);expect(video.currentTime).toBe(3);expect(screen.getByText(/已选 00:03–00:08/)).toBeInTheDocument()});

test('analysis shows only scoped findings with this asset evidence and seeks their exact interval',async()=>{
 const current={id:'e-a',asset_id:'a',start_ms:3000,end_ms:8000,provenance:'sampled_frames'};
 const state={sessions:[{id:'current',scope:{asset_ids:['a','b'],selected_segments:[{asset_id:'a',start_ms:3000,end_ms:8000}]},turns:[{evidence:[current,{id:'e-b',asset_id:'b',start_ms:0,end_ms:2000,provenance:'native_video'},{id:'e-out',asset_id:'a',start_ms:0,end_ms:8000,provenance:'sampled_frames'}],answer:{summary:'跨素材概要不可当作当前片结论',findings:[{type:'fact',text:'掰开酥皮展示层次',evidence_ids:['e-a']},{type:'fact',text:'别的素材结论',evidence_ids:['e-b']},{type:'hypothesis',text:'没有当前素材依据的建议',evidence_ids:[]},{type:'fact',text:'超过已选片段的结论',evidence_ids:['e-out']}]}}]},{id:'outside',scope:{asset_ids:['b']},turns:[{evidence:[current],answer:{findings:[{type:'fact',text:'范围不匹配的结论',evidence_ids:['e-a']}]}}]}]};
 vi.stubGlobal('fetch',vi.fn(async(input:string)=>new Response(JSON.stringify(input.endsWith('/analysis-results')?viewFixture(state):[]))));
 render(<LinkedMediaDetails base="/projects/p" asset={asset} reload={vi.fn()} onEvidence={vi.fn()}/>);
 expect(screen.getByRole('tab',{name:'分析'})).toHaveAttribute('aria-selected','true');
 expect(await screen.findByText('掰开酥皮展示层次')).toBeInTheDocument();
 for(const text of ['别的素材结论','范围不匹配的结论','没有当前素材依据的建议','跨素材概要不可当作当前片结论','超过已选片段的结论'])expect(screen.queryByText(text)).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:/定位片段.*00:03.*00:08/}));
 expect((screen.getByLabelText('素材播放器') as HTMLVideoElement).currentTime).toBe(3);
 expect(screen.getByText(/已选 00:03–00:08/)).toHaveTextContent('掰开酥皮展示层次');
});

test('no saved analysis gives a next step and a missing evidence callback remains disabled',async()=>{
 vi.stubGlobal('fetch',vi.fn(async(input:string)=>new Response(JSON.stringify(input.endsWith('/analysis-results')?viewFixture({sessions:[]}):[]))));
 render(<LinkedMediaDetails base="/projects/p" asset={{...asset,metadata:{...asset.metadata,transcription:{needs_review:true,warnings:['ASR NOT_ACCEPTED internal-route']}}}} reload={vi.fn()}/>);
 expect(await screen.findByText('还没有这条视频的分析。')).toBeInTheDocument();
 expect(screen.getByText('可以把这条素材直接带入当前创作，继续提问或拆解。')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('tab',{name:'台词'}));
 expect(screen.getByText('台词需要核对，画面分析可以先看。')).toBeInTheDocument();
 expect(screen.queryByText(/ASR NOT_ACCEPTED/)).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:/00:03.*掰开看酥皮/}));
 expect(screen.getByRole('button',{name:'将所选片段带入对话'})).toBeDisabled();
});

test('granular observations use their own modality and exact frame or transcript support',async()=>{
 const manifest={id:'input',asset_id:'a',asset_sha256:'sha-a',frames:[{frame_id:'f',time_ms:1260}],transcript:{segments:[{segment_id:'s',start_ms:3000,end_ms:4500}]}};
 const visual={observation_id:'ov',asset_id:'a',asset_sha256:'sha-a',input_manifest_id:'input',claim_kind:'visual_observation',interpretation:'observed',quality_state:'usable',statement:'杯盖打开',support:[{frame_id:'f',time_ms:1260}]};
 const spoken={...visual,observation_id:'os',claim_kind:'spoken_content',statement:'台词说便于携带',support:[{transcript_segment_id:'s',start_ms:3000,end_ms:4500}]};
 const state={sessions:[{id:'session',scope:{asset_ids:['a']},turns:[{evidence:[{id:'a',asset_id:'a',provenance:'sampled_frames',input_manifest:manifest,observations:[visual,spoken]}],answer:{findings:[]}}]}]};
 vi.stubGlobal('fetch',vi.fn(async(input:string)=>new Response(JSON.stringify(input.endsWith('/analysis-results')?viewFixture(state):[]))));
 render(<LinkedMediaDetails base="/projects/p" asset={{...asset,sha256:'sha-a'}} reload={vi.fn()} onEvidence={vi.fn()}/>);
 expect(await screen.findByText('杯盖打开')).toBeInTheDocument();
 expect(screen.getByText('画面观察')).toBeInTheDocument();expect(screen.getByText('台词内容')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'回看画面 00:01.260'}));
 expect((screen.getByLabelText('素材播放器') as HTMLVideoElement).currentTime).toBe(1.26);
 fireEvent.click(screen.getByRole('button',{name:'核对台词 00:03.000–00:04.500'}));
 expect((screen.getByLabelText('素材播放器') as HTMLVideoElement).currentTime).toBe(3);
});

test('legacy evidence is labelled and stale granular media versions are not shown as current',async()=>{
 const state={sessions:[{id:'legacy',scope:{asset_ids:['a']},turns:[{evidence:[{id:'a',asset_id:'a',start_ms:0,end_ms:3000,provenance:'sampled_frames'}],answer:{findings:[{type:'fact',text:'旧结论',evidence_ids:['a']}]}}]},{id:'stale',scope:{asset_ids:['a']},turns:[{evidence:[{id:'a',asset_id:'a',observations:[{asset_id:'a',asset_sha256:'old',input_manifest_id:'input',claim_kind:'visual_observation',interpretation:'observed',statement:'旧媒体画面',support:[{frame_id:'f',time_ms:500}]}]}],answer:{findings:[]}}]}]};
 vi.stubGlobal('fetch',vi.fn(async(input:string)=>new Response(JSON.stringify(input.endsWith('/analysis-results')?viewFixture(state):[]))));
 render(<LinkedMediaDetails base="/projects/p" asset={{...asset,sha256:'new'}} reload={vi.fn()}/>);
 expect(await screen.findByText('旧结论')).toBeInTheDocument();expect(screen.getByText('旧版依据')).toBeInTheDocument();
 expect(screen.queryByText('旧媒体画面')).not.toBeInTheDocument();
});

test('transcript correction explicitly records reviewed state and keeps backend quality visible',async()=>{
 const writes:any[]=[];vi.stubGlobal('fetch',vi.fn(async(input:string,init:any)=>{if(input.includes('/media-ticket/'))return new Response(JSON.stringify({streaming:false}));if(init?.method==='POST'){writes.push(JSON.parse(init.body));return new Response('{}')}return new Response(JSON.stringify(input.endsWith('/analysis-results')?viewFixture({sessions:[]}):[]))}));
 render(<LinkedMediaDetails base="/projects/p" asset={{...asset,transcript_policy:{allowed:false,execution_state:'succeeded',quality_state:'suspect',review_state:'unreviewed',transcript_revision:'revision'}}} reload={vi.fn()}/>);
 fireEvent.click(screen.getByRole('tab',{name:'台词'}));expect(screen.getByText('识别完成 · 台词有疑点 · 未校对')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'校正文字稿'}));fireEvent.click(screen.getByRole('button',{name:'保存文字稿校正'}));
 await waitFor(()=>expect(writes).toHaveLength(1));expect(writes[0]).toMatchObject({reviewed:true,expected_transcript_revision:'revision'});
});

test('unknown transcript can be reviewed but cannot silently become dialogue evidence',()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>new Response('[]')));
 render(<LinkedMediaDetails base="/projects/p" asset={{...asset,transcript_policy:{allowed:false,quality_state:'unknown',execution_state:'succeeded',review_state:'unreviewed'}}} reload={vi.fn()} onEvidence={vi.fn()}/>);
 fireEvent.click(screen.getByRole('tab',{name:'台词'}));fireEvent.click(screen.getByRole('button',{name:/00:03.*掰开看酥皮/}));
 expect(screen.getByRole('button',{name:'将所选片段带入对话'})).toBeDisabled();
 expect(screen.getByText('这条依据需要先核对，校正保存后可带入对话。')).toBeInTheDocument();
});

test('material detail chooses a specific saved result and never loads the full workspace',async()=>{
 const calls:string[]=[];const rec=(id:string,text:string)=>({id,scope:{asset_ids:['a']},turns:[{evidence:[{id:'a',asset_id:'a',start_ms:0,end_ms:3000,provenance:'sampled_frames'}],answer:{findings:[{type:'fact',text,evidence_ids:['a']}]}}]});
 vi.stubGlobal('fetch',vi.fn(async(input:string)=>{calls.push(input);return new Response(JSON.stringify(input.endsWith('/analysis-results')?{asset,results:[{id:'one',kind:'session',title:'开头分析',version:1,record:rec('one','第一份结论')},{id:'two',kind:'session',title:'镜头比较',version:2,record:rec('two','第二份结论')}]}:[]))}));
 render(<LinkedMediaDetails base="/projects/p" asset={asset} reload={vi.fn()}/>);expect(await screen.findByText('第一份结论')).toBeInTheDocument();expect(screen.queryByText('第二份结论')).not.toBeInTheDocument();fireEvent.change(screen.getByRole('combobox',{name:'分析成果'}),{target:{value:'two'}});expect(screen.getByText('第二份结论')).toBeInTheDocument();expect(screen.queryByText('第一份结论')).not.toBeInTheDocument();expect(calls.some(url=>url.endsWith('/state'))).toBe(false);expect(calls.some(url=>url.includes('/scene/'))).toBe(false);
});

test('video uses an asset-only playback ticket instead of downloading the entire file',async()=>{const calls:string[]=[];vi.stubGlobal('fetch',vi.fn(async(input:string)=>{calls.push(input);return new Response(JSON.stringify(input.includes('/media-ticket/')?{streaming:true,path:'/api/media-streams/opaque-only-asset'}:[]))}));render(<LinkedMediaDetails base="/projects/p" asset={asset} reload={vi.fn()}/>);await waitFor(()=>expect(screen.getByLabelText('素材播放器')).toHaveAttribute('src',expect.stringContaining('/api/media-streams/opaque-only-asset')));expect(calls.some(path=>path.endsWith('/file'))).toBe(false)});
