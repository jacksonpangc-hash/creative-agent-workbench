import {afterEach,expect,it,vi} from 'vitest';
import {request,setTeamMode} from './api';
afterEach(()=>{setTeamMode(false);vi.restoreAllMocks()});
it('requires explicit consent before saving a shared team credential',async()=>{
 setTeamMode(true);
 const fetcher=vi.spyOn(globalThis,'fetch').mockResolvedValue({ok:true,json:async()=>({saved:true})} as Response);
 const confirm=vi.spyOn(window,'confirm').mockReturnValue(false);
 await expect(request('/projects/p/models',{method:'POST',body:JSON.stringify({api_key:'fixture-key'})})).rejects.toThrow('取消');
 expect(fetcher).not.toHaveBeenCalled();
 confirm.mockReturnValue(true);
 await request('/projects/p/models',{method:'POST',body:JSON.stringify({api_key:'fixture-key'})});
 expect(new Headers(fetcher.mock.calls[0][1]?.headers).get('X-Confirm-Shared-Credential')).toBe('true');
});
