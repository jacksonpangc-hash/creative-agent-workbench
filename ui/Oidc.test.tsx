import {beforeEach,expect,it,vi} from 'vitest';
import {beginLogin,completeLogin} from './oidc';
const config={issuer:'https://identity.example',client_id:'dam',authorization_endpoint:'https://identity.example/auth',token_endpoint:'https://identity.example/token'};
beforeEach(()=>{sessionStorage.clear();vi.restoreAllMocks()});
it('binds callback to one-time state and PKCE verifier, keeps token out of storage',async()=>{
 const redirect='https://dam.example/';
 const url=new URL(await beginLogin(config,redirect));
 expect(url.searchParams.get('code_challenge_method')).toBe('S256');
 expect(url.searchParams.get('code_challenge')).toBeTruthy();
 const fetcher=vi.fn().mockResolvedValue({ok:true,json:async()=>({access_token:'sensitive',token_type:'Bearer'})});
 const token=await completeLogin(config,new URLSearchParams({code:'code',state:url.searchParams.get('state')!}),redirect,fetcher);
 expect(token).toBe('sensitive');
 const body=fetcher.mock.calls[0][1].body as URLSearchParams;
 expect(body.get('code_verifier')!.length).toBeGreaterThanOrEqual(43);
 expect(sessionStorage.length).toBe(0);
 await expect(completeLogin(config,new URLSearchParams({code:'code',state:url.searchParams.get('state')!}),redirect,fetcher)).rejects.toThrow();
 expect(fetcher).toHaveBeenCalledTimes(1);
});
it('rejects incorrect callback state without exchanging code',async()=>{
 await beginLogin(config,'https://dam.example/');
 const fetcher=vi.fn();
 await expect(completeLogin(config,new URLSearchParams({code:'code',state:'wrong'}),'https://dam.example/',fetcher)).rejects.toThrow();
 expect(fetcher).not.toHaveBeenCalled();
});
