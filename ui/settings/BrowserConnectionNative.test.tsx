import {render,screen,waitFor,fireEvent,cleanup} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import BrowserConnection from './BrowserConnection';
const api=vi.hoisted(()=>({request:vi.fn(),post:vi.fn()}));
vi.mock('../api',()=>({...api,API:'',teamMode:false}));
afterEach(()=>cleanup());
beforeEach(()=>{vi.clearAllMocks();api.request.mockImplementation((path:string)=>Promise.resolve(path.endsWith('/status')?{state:'ready',store_state:'awaiting_store_publication'}:{connections:[{connection_id:'c1',state:'pending_confirmation',origin:'chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/',project_ids:[]}]}));api.post.mockResolvedValue({state:'connected'})});
it('names the missing store release honestly and confirms only the current project',async()=>{
 render(<BrowserConnection base="/projects/project-1"/>);
 expect(await screen.findByText('商店版待上架')).not.toBeNull();
 const approve=await screen.findByRole('button',{name:'允许保存到当前项目'});fireEvent.click(approve);
 await waitFor(()=>expect(api.post).toHaveBeenCalledWith('/browser-bridge/connections/c1/approve',{project_ids:['project-1']}));
 expect(screen.queryByText('高级连接方式')).toBeNull();
});

it('invalid connection responses fail closed without crashing or showing approval',async()=>{
 api.request.mockImplementation((path:string)=>Promise.resolve(path.endsWith('/status')?{state:'ready',store_state:'awaiting_store_publication'}:{}));
 render(<BrowserConnection base="/projects/project-2"/>);
 expect(await screen.findByRole('alert')).not.toBeNull();
 expect(screen.queryByRole('button',{name:'允许保存到当前项目'})).toBeNull();
 expect(api.post).not.toHaveBeenCalled();
});
