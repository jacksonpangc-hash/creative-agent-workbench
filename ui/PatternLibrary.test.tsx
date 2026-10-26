import {afterEach,expect,test,vi} from 'vitest';
import {cleanup,render,screen,fireEvent} from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import PatternLibrary from './industry/PatternLibrary';
afterEach(()=>{cleanup();vi.unstubAllGlobals()});
test('offline structure previews retain attribution and only fill a draft',async()=>{
 const use=vi.fn();vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify({source:{record_count:647},total:1,next_offset:null,items:[{id:'p',market:'US',category:'Food',video_type:'Demo',hooks:['pain-point'],visuals:'Hands open package',pain_point:'Difficult to carry',source:'https://www.tiktok.com/@example/video/123',library_source:{attribution:'Clipcat',license:'CC BY 4.0'}}]}))));
 render(<PatternLibrary base="/projects/A" onUse={use}/>);expect(await screen.findByText('Hands open package')).toBeInTheDocument();expect(screen.getByText(/没有销量或互动数据/)).toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:'用这个结构写草稿'}));expect(use).toHaveBeenCalledWith(expect.stringContaining('仅借鉴表达结构'));expect(use.mock.calls[0][0]).toContain('https://www.tiktok.com/');expect(use.mock.calls[0][0]).not.toContain('热销');
});
