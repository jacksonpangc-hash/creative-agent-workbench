import {afterEach,expect,test,vi} from 'vitest';
import {request} from './api';
afterEach(()=>vi.restoreAllMocks());
test('browser transport failure explains recovery instead of exposing Load failed',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>{throw new TypeError('Load failed')}));
 await expect(request('/projects/A/assets',{method:'POST',body:new FormData()})).rejects.toThrow('连接中断');
});
