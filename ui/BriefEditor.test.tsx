import {useState} from 'react';
import {afterEach,expect,test,vi} from 'vitest';
import {render,screen,fireEvent,cleanup,waitFor} from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import BriefEditor from './strategy/BriefEditor';
afterEach(()=>cleanup());
const initial={id:'brief',title:'A',version:1,objective:'原目标',constraints:[],hypotheses:['检验留存'],references:[],confirmed:false,revisions:[],source:{session_id:'s',turn_index:0,scope:{asset_ids:[]},findings:[{text:'有待验证'}],evidence:[]}};
test('edited brief requires saved confirmation before passing into creation',async()=>{
 const create=vi.fn(),saved:any[]=[];
 function Host(){const [brief,setBrief]=useState<Record<string,any>>(initial);return <BriefEditor brief={brief} assets={[]} onSave={async v=>{saved.push(v);const next={...brief,...v,version:2,revisions:[brief]};setBrief(next);return next}} onCreate={create} onSession={()=>{}}/>}
 render(<Host/>);
 const next=screen.getByRole('button',{name:'携带此简报进入仿拍'});
 expect(next).toBeDisabled();
 fireEvent.change(screen.getByRole('textbox',{name:'创作目标'}),{target:{value:'新目标'}});
 fireEvent.click(screen.getByRole('checkbox',{name:'我已核对本版目标、参考素材和测试假设'}));
 expect(next).toBeDisabled();
 fireEvent.click(screen.getByRole('button',{name:'保存为新版本'}));
 await waitFor(()=>expect(next).toBeEnabled());
 expect(saved[0]).toMatchObject({expected_version:1,objective:'新目标',confirmed:true});
 fireEvent.click(next);expect(create).toHaveBeenCalledOnce();
 fireEvent.change(screen.getByRole('textbox',{name:'创作目标'}),{target:{value:'未确认修改'}});
 expect(next).toBeDisabled();
});
