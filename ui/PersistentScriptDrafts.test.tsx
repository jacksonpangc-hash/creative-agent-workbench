import {afterEach,expect,test,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import ScriptsWorkspace from './scripts/ScriptsWorkspace';
afterEach(()=>{cleanup();sessionStorage.clear();vi.unstubAllGlobals()});

test('an unfinished script survives reopening without browser storage and explicit save clears the local draft',async()=>{
 const original={id:'s',version:1,title:'拍摄稿',objective:'介绍商品',rows:[{start_ms:0,end_ms:3000,dialogue:'原台词',visual:'近景',references:[]}],revisions:[]};
 let stored:any=null;let saved:any=original;let committed=0;
 const response=(data:any)=>new Response(JSON.stringify(data));
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{
  const body=init?.body?JSON.parse(String(init.body)):null;
  if(input.endsWith('/drafts'))return response({drafts:stored?[stored]:[],active_identity:null});
  if(input.includes('/drafts/')&&body){stored={identity:input.split('/').at(-1),version:body.expected_version+1,content:body.content};return response(stored)}
  if(input.endsWith('/scripts/s')&&init?.method==='PUT'){committed++;saved={...saved,...body,version:2};return response(saved)}
  if(input.endsWith('/scripts'))return response([saved]);
  return response([]);
 }));
 const props={base:'/projects/P',assets:[],onUseScript:vi.fn()};render(<ScriptsWorkspace {...props}/>);
 fireEvent.click(await screen.findByRole('button',{name:'拍摄稿 · v1'}));
 fireEvent.change(screen.getByRole('textbox',{name:'第1行台词'}),{target:{value:'尚未点保存的修改'}});
 await waitFor(()=>expect(stored?.content?.scriptDrafts?.drafts?.s?.rows[0].dialogue).toBe('尚未点保存的修改'));
 expect(committed).toBe(0);cleanup();sessionStorage.clear();render(<ScriptsWorkspace {...props}/>);
 expect(await screen.findByRole('textbox',{name:'第1行台词'})).toHaveValue('尚未点保存的修改');
 fireEvent.click(screen.getByRole('button',{name:'保存新版本'}));
 await waitFor(()=>expect(committed).toBe(1));
 await waitFor(()=>expect(stored.content.scriptDrafts.drafts.s).toBeUndefined());
 expect(saved.rows[0].dialogue).toBe('尚未点保存的修改');
});

test('cached script fields and save stay disabled until the durable draft has loaded',async()=>{
 let release!:()=>void;const gate=new Promise<void>(resolve=>{release=resolve});
 const cached={id:'s',version:1,title:'旧缓存',objective:'介绍商品',rows:[{start_ms:0,end_ms:3000,dialogue:'旧台词',visual:'近景',references:[]}]};
 sessionStorage.setItem('creative-script-drafts-053:'+encodeURIComponent('/projects/P'),JSON.stringify({active:'s',drafts:{s:cached}}));
 const durable={...cached,title:'本机新稿',rows:[{...cached.rows[0],dialogue:'其他窗口保留的台词'}]};
 const writes:any[]=[];
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{
  if(init?.body)writes.push(JSON.parse(String(init.body)));
  if(input.endsWith('/drafts')){await gate;return new Response(JSON.stringify({drafts:[{identity:'script-editor',version:3,content:{scriptDrafts:{active:'s',drafts:{s:durable}}}}]}))}
  return new Response(JSON.stringify([]));
 }));
 render(<ScriptsWorkspace base="/projects/P" assets={[]} onUseScript={vi.fn()}/>);
 expect(screen.getByRole('textbox',{name:'第1行台词'})).toBeDisabled();
 expect(screen.getByRole('button',{name:'保存新版本'})).toBeDisabled();
 release();await waitFor(()=>expect(screen.getByRole('textbox',{name:'第1行台词'})).toBeEnabled());
 expect(screen.getByRole('textbox',{name:'第1行台词'})).toHaveValue('其他窗口保留的台词');
 expect(writes).toHaveLength(0);
});

test('retrying a failed draft read restores saved work before allowing any autosave',async()=>{
 let reads=0;const writes:any[]=[];
 const cached={active:'__new',drafts:{__new:{title:'未完成稿',objective:'介绍商品',rows:[{start_ms:0,end_ms:3000,dialogue:'保留我的修改',visual:'近景',references:[]}]}}};
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{
  if(init?.body)writes.push(JSON.parse(String(init.body)));
  if(input.endsWith('/drafts')){
   if(++reads===1)return new Response(JSON.stringify({detail:'暂时不可用'}),{status:503});
   return new Response(JSON.stringify({drafts:[{identity:'script-editor',version:2,content:{scriptDrafts:cached}}]}));
  }
  return new Response(JSON.stringify([]));
 }));
 render(<ScriptsWorkspace base="/projects/P" assets={[]} onUseScript={vi.fn()}/>);
 const retry=await screen.findByRole('button',{name:'重试读取草稿'});
 expect(screen.getByRole('button',{name:'新建脚本'})).toBeDisabled();
 expect(writes).toHaveLength(0);fireEvent.click(retry);
 expect(await screen.findByRole('textbox',{name:'第1行台词'})).toHaveValue('保留我的修改');
 await waitFor(()=>expect(screen.getByRole('textbox',{name:'第1行台词'})).toBeEnabled());
 expect(reads).toBe(2);expect(writes).toHaveLength(0);
});
