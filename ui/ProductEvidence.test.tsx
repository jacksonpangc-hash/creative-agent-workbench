import {afterEach,expect,test,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,within} from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import Workspace from './strategy/StrategyWorkspace';
afterEach(()=>cleanup());

function Composer(){return <div/>}
function view(evidence:Record<string,any>[],ids:string[]){
 const onAsset=vi.fn();
 const result=render(<Workspace mode="directed" onMode={vi.fn()} session={{mode:'directed',turns:[{question:'怎样拍',answer:{summary:'从整理动作开始。',findings:[{type:'fact',text:'商品资料说明它是收纳盒。',evidence_ids:ids}]},evidence}]}} question="" onQuestion={vi.fn()} onExample={vi.fn()} pending={null} onApplyExample={vi.fn()} onDiscardExample={vi.fn()} composer={<Composer/>} onAsset={onAsset} onExcerpt={vi.fn()} onBrief={vi.fn()} onParse={vi.fn()} onReport={vi.fn()} onReview={vi.fn()}/>);
 return {...result,onAsset};
}

test('product citations reveal statement source version and SKU without opening playable media',()=>{
 const product={id:'product-evidence-synthetic',evidence_kind:'product_fact',title:'商品资料',statement:'桌面收纳盒',source:'A款包装品类',facts_version:3,snapshot_at:'2026-10-24T00:00:00+00:00',product_id:'box',sku_id:'A',provenance:'confirmed_product_facts'};
 const {container,onAsset}=view([product],[product.id]);
 const citations=container.querySelector('.citations') as HTMLElement;
 fireEvent.click(within(citations).getByText('商品资料'));
 expect(citations).toHaveTextContent('桌面收纳盒');expect(citations).toHaveTextContent('A款包装品类');expect(citations).toHaveTextContent('v3');expect(citations).toHaveTextContent('SKU：A');
 expect(container.querySelector('.finding .tag')).toHaveTextContent('商品资料');
 expect(citations).not.toHaveTextContent('抽样帧');expect(citations).not.toHaveTextContent('观察事实');
 expect(within(citations).queryByRole('button')).not.toBeInTheDocument();expect(onAsset).not.toHaveBeenCalled();
 const evidence=container.querySelector('.evidence-details') as HTMLElement;
 expect(evidence).toHaveTextContent('商品资料');expect(evidence).toHaveTextContent('A款包装品类');expect(evidence).not.toHaveTextContent('抽样帧');
});

test('historical media citations keep opening the selected asset',()=>{
 const {container,onAsset}=view([{id:'media-1',asset_id:'asset-1',title:'历史参考',provenance:'sampled_frames'}],['media-1']);
 fireEvent.click(within(container.querySelector('.citations') as HTMLElement).getByRole('button',{name:'历史参考'}));
 expect(onAsset.mock.calls[0][0]).toBe('asset-1');
});
