import {test} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {captureVisiblePage} from './extract.js';
function fixture(html, url) {
 const dom=new JSDOM(html,{url});
 globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.location=dom.window.location;
 dom.window.HTMLElement.prototype.getBoundingClientRect=function(){return {width:100,height:20}};
 return dom;
}
test('visible public counters retain raw values, links and timestamp without sales inference',()=>{
 fixture('<title>洗衣液演示</title><div data-e2e="user-title">公开作者</div><span data-e2e="like-count">1.2万</span><span data-e2e="comment-count" style="display:none">999</span><a href="/video/222">另一个视频</a><a href="https://evil.test/secret">外站</a><input type="password" value="secret">','https://www.douyin.com/video/111');
 const result=captureVisiblePage();
 assert.equal(result.page.title,'洗衣液演示');assert.equal(result.page.author,'公开作者');
 assert.equal(result.page.metrics.length,1);assert.equal(result.page.metrics[0].raw,'1.2万');
 assert.equal(result.page.metrics[0].basis,'visible_page');assert.ok(Date.parse(result.page.metrics[0].as_of));
 assert.equal(result.links.length,1);assert.equal(result.links[0].url,'https://www.douyin.com/video/222');
 assert.equal(JSON.stringify(result).includes('secret'),false);
});
test('search links do not inherit page-wide counters and empty values are not zero',()=>{
 fixture('<span data-e2e="like-count">999</span><a href="/short-video/abc">商品演示</a>','https://www.kuaishou.com/search/video?searchKey=洗衣液');
 const result=captureVisiblePage();assert.deepEqual(result.page.metrics,[]);assert.deepEqual(result.links[0].metrics,[]);
});
test('unsupported pages are rejected before reading DOM',()=>{
 fixture('<title>私人页面</title>','https://evil.test/');assert.throws(()=>captureVisiblePage(),/抖音|快手/);
});
test('observed Douyin search card keeps title and author separate from duration and counters',()=>{
 fixture('<a href="/video/123"><div><div data-home-video-id><span>03:50</span><span>5307</span></div><div><div><div>商品做法示范</div><div><span><span>@</span><span>公开作者</span></span><span>4月前</span></div></div></div></div></a>','https://www.douyin.com/search/商品');
 const result=captureVisiblePage();
 assert.equal(result.links[0].title,'商品做法示范');assert.equal(result.links[0].author,'公开作者');
 assert.deepEqual(result.links[0].metrics,[]);
});
test('observed detail action strip retains owner and counters without using hashed classes',()=>{
 fixture('<h1>公开做法</h1><div><a href="/user/commenter">评论用户</a><p>评论内容</p></div><aside><a href="/user/owner">公开作者</a><p>粉丝 1.5万 获赞 17万</p></aside><section>'+[
  ['0 0 90 94','5300',''],['0 0 99 99','433',''],['0 0 100 100','7900',''],['0 0 99 99','2200','data-e2e="video-share-icon-container"']
 ].map(([viewBox,count,attrs])=>`<div ${attrs}><svg viewBox="${viewBox}"></svg><span>${count}</span></div>`).join('')+'</section>','https://www.douyin.com/video/123');
 const result=captureVisiblePage();assert.equal(result.page.author,'公开作者');
 assert.deepEqual(Object.fromEntries(result.page.metrics.map(m=>[m.name,m.raw])),{likes:'5300',comments:'433',favorites:'7900',shares:'2200'});
 document.querySelector('svg').setAttribute('viewBox','0 0 1 1');
 assert.deepEqual(captureVisiblePage().page.metrics,[]);
});
