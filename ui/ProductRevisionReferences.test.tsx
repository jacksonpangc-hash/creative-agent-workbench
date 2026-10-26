import {afterEach,expect,test,vi} from 'vitest';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import PublicReferences from './industry/PublicReferences';
afterEach(()=>{cleanup();vi.unstubAllGlobals()});
test('saved real cards lead reference panel and source switches reveal only their own controls',async()=>{
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>new Response(JSON.stringify(url.endsWith('/references')?[{id:'r',title:'已收藏的真实参考',platform:'douyin',url:'https://www.douyin.com/video/123',observations:[]}]:url.endsWith('/state')?{assets:[]}:[]),{status:200})));
 render(<PublicReferences base="/projects/A" connectedData={<p>蝉妈妈付费查询</p>}/>);
 expect(await screen.findByText('已收藏的真实参考')).toBeVisible();expect(screen.queryByRole('textbox',{name:'搜索关键词'})).not.toBeInTheDocument();
 const tabs=screen.getAllByRole('tab');expect(tabs.map(t=>t.textContent)).toEqual(['我的收藏','平台网页','已连接的数据服务']);
 tabs[0].focus();fireEvent.keyDown(tabs[0],{key:'ArrowRight'});expect(screen.getByRole('tab',{name:'平台网页'})).toHaveFocus();expect(screen.getByRole('tab',{name:'平台网页'})).toHaveAttribute('aria-selected','true');
 fireEvent.click(screen.getByRole('tab',{name:'平台网页'}));expect(screen.getByRole('textbox',{name:'搜索关键词'})).toBeVisible();expect(screen.queryByRole('button',{name:'加入本次创作'})).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('tab',{name:'我的收藏'}));fireEvent.click(screen.getByRole('button',{name:'添加参考'}));expect(screen.getByLabelText('分享文案或链接（每行一条）')).toBeVisible();expect(screen.queryByRole('textbox',{name:'搜索关键词'})).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('tab',{name:'已连接的数据服务'}));expect(screen.getByText('蝉妈妈付费查询')).toBeVisible();
 fireEvent.click(screen.getByRole('tab',{name:'我的收藏'}));expect(screen.getByText('已收藏的真实参考')).toBeVisible();
});
