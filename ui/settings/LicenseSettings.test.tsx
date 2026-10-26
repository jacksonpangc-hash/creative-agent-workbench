import {render, screen, fireEvent, waitFor, cleanup} from '@testing-library/react';
import {afterEach, expect, test, vi} from 'vitest';
import LicenseSettings from './LicenseSettings';
const mocks=vi.hoisted(()=>({request:vi.fn(),post:vi.fn()}));
vi.mock('../api',()=>mocks);
afterEach(()=>{cleanup();vi.clearAllMocks()});
test('shows expiry restriction and activates without retaining password',async()=>{
 mocks.request.mockResolvedValue({mode:'commercial',can_work:false,device_id:'test-device',reason:'请登录激活'});
 mocks.post.mockResolvedValue({mode:'commercial',can_work:true,device_id:'test-device',expires_at:1800259200,offline_until:1800086400});
 render(<LicenseSettings/>);
 await screen.findByText('请登录激活');
 fireEvent.change(screen.getByLabelText('授权编号'),{target:{value:'license-A'}});
 fireEvent.change(screen.getByLabelText('授权密码'),{target:{value:'password-long'}});
 fireEvent.click(screen.getByRole('button',{name:'登录并激活'}));
 await waitFor(()=>expect(mocks.post).toHaveBeenCalledWith('/license/login',{license_id:'license-A',password:'password-long'}));
 await screen.findByText('授权有效');
 expect((screen.getByLabelText('授权密码') as HTMLInputElement).value).toBe('');
 fireEvent.click(screen.getByRole('button',{name:'刷新授权'}));
 await waitFor(()=>expect(mocks.post).toHaveBeenCalledWith('/license/refresh',{}));
});
test('internal mode is explicit and offers no misleading activation controls',async()=>{
 mocks.request.mockResolvedValue({mode:'internal',can_work:true});
 render(<LicenseSettings/>);
 await screen.findByText('内部开发模式');
 expect(screen.queryByRole('button',{name:'登录并激活'})).toBeNull();
});
test('releases current device and shows stopped status',async()=>{
 mocks.request.mockResolvedValue({mode:'commercial',can_work:true,device_id:'test-device'});
 mocks.post.mockResolvedValue({mode:'commercial',can_work:false,device_id:'test-device',reason:'本机授权已释放'});
 render(<LicenseSettings/>);
 await screen.findByText('授权有效');
 fireEvent.click(screen.getByRole('button',{name:'释放本机授权'}));
 await waitFor(()=>expect(mocks.post).toHaveBeenCalledWith('/license/release',{}));
 await screen.findByText('本机授权已释放');
});
test('shows signed feature names without inventing permissions',async()=>{
 mocks.request.mockResolvedValue({mode:'commercial',can_work:true,device_id:'test-device',features:['workspace.use','script.generate']});
 render(<LicenseSettings/>);
 await screen.findByText('工作空间、脚本创作');
 expect(screen.queryByText('商业数据查询')).toBeNull();
});
