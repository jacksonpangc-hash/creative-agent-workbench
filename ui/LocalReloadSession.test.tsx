import {afterEach,expect,test,vi} from 'vitest';

afterEach(()=>{sessionStorage.clear();localStorage.clear();history.replaceState(null,'','/');vi.unstubAllGlobals();vi.resetModules()});

test('a local tab can refresh without putting its runtime token in the URL or persistent storage',async()=>{
 vi.resetModules();history.replaceState(null,'','/#token=local-runtime-test-token');
 const requests:RequestInit[]=[];
 vi.stubGlobal('fetch',vi.fn(async(_url:string,init:RequestInit)=>{requests.push(init);return new Response('{}')}));
 const initial=await import('./api');await initial.request('/projects');
 expect(new Headers(requests.at(-1)?.headers).get('Authorization')).toBe('Bearer local-runtime-test-token');
 expect(location.hash).toBe('');expect(localStorage.length).toBe(0);
 vi.resetModules();const reloaded=await import('./api');await reloaded.request('/projects');
 expect(new Headers(requests.at(-1)?.headers).get('Authorization')).toBe('Bearer local-runtime-test-token');
 expect(document.cookie).toBe('');
});

test('a new runtime replaces a stale local tab token and logout removes it',async()=>{
 vi.resetModules();history.replaceState(null,'','/#token=first-runtime');
 await import('./api');vi.resetModules();history.replaceState(null,'','/#token=next-runtime');
 const api=await import('./api');const headers:string[]=[];
 vi.stubGlobal('fetch',vi.fn(async(_url:string,init:RequestInit)=>{headers.push(new Headers(init.headers).get('Authorization')||'');return new Response('{}')}));
 await api.request('/projects');expect(headers.at(-1)).toBe('Bearer next-runtime');
 api.setSessionToken('');vi.resetModules();const cleared=await import('./api');await cleared.request('/projects');
 expect(headers.at(-1)).toBe('Bearer');expect(sessionStorage.length).toBe(0);
});

test('team login tokens stay in memory and cannot become local reload credentials',async()=>{
 vi.resetModules();history.replaceState(null,'','/#token=local-test');const api=await import('./api');
 api.setTeamMode(true);api.setSessionToken('team-login-test');
 expect(sessionStorage.length).toBe(0);expect(localStorage.length).toBe(0);
});
