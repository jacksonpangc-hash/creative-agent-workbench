import json
import pytest
from test_agent import setup, Answer
from creative_agent.agent import AnalysisAgent
from creative_agent.contracts import AnalysisScope
from creative_agent.modes import task_contract, validate_result


class ModeClient(Answer):
    def __init__(self, mode, results):
        super().__init__(); self.mode=mode; self.results=iter(results)
    def chat(self, system, question, **kwargs):
        reply=super().chat(system,question,**kwargs)
        if not kwargs:
            reply['data']['result']=next(self.results)
        return reply


@pytest.mark.parametrize('mode,result',[
 ('directed', {'comparisons':[], 'recommendations':['开头A/B测试']}),
 ('exploration', {'patterns':['钩子'], 'differences':['表达差异'], 'opportunities':['测试机会'], 'evidence_gaps':['缺少投放数据']}),
 ('inspiration', {'phase':'diverge','questions':['产品售价？'],'directions':['功效演示','生活场景'],'script_outline':[],'brief':None}),
])
def test_legacy_mode_contracts_remain_explicitly_validatable(mode,result):
    validated=validate_result({'summary':'历史结构','findings':[],'result':result},mode)
    assert validated['result']==result
    task=task_contract(mode)
    assert task['mode']==mode
    assert task['stage']=='initial'
    assert task['required_fields']==list(result)


def test_generic_response_cannot_pass_as_explicit_legacy_exploration():
    with pytest.raises(ValueError,match='模式'):
        validate_result({'summary':'历史结构','findings':[],'result':{}},'exploration')


def test_explicit_legacy_inspiration_complete_phase_requires_script_and_brief():
    result={'phase':'converge','questions':[],'directions':['方向'],'script_outline':[],'brief':None}
    with pytest.raises(ValueError,match='脚本'):
        validate_result({'summary':'历史结构','findings':[],'result':result},'inspiration')


def test_followup_carries_mode_and_prior_stage(setup):
    store,p,media,a=setup
    initial={'phase':'diverge','questions':['产品？'],'directions':['A','B'],'script_outline':[],'brief':None}
    compare={**initial,'phase':'compare','questions':['选择哪个方向？']}
    client=ModeClient('inspiration',[initial,compare]); agent=AnalysisAgent(store,media)
    session=agent.analyze(AnalysisScope(project_id=p,asset_ids=[a['id']]),'inspiration','分析',client)
    result=agent.followup(p,session['id'],'对比两个方向',client)
    task=json.loads(client.questions[-1])['task']
    assert task['mode']=='inspiration' and task['stage']=='continuation'
    assert task['previous_phase']=='diverge'
    assert result['turns'][-1]['answer']['result']['phase']=='compare'


def test_inspiration_first_turn_can_deliver_complete_script(setup):
    store,p,media,a=setup
    result={'scripts':[{'title':'场景演示','objective':'试拍','rows':[{'start_ms':0,'end_ms':4000,'dialogue':'看这个使用动作','visual':'手持产品进行演示'}]}]}
    session=AnalysisAgent(store,media).analyze(AnalysisScope(project_id=p,asset_ids=[a['id']]),'inspiration','信息充分，直接给脚本',ModeClient('inspiration',[result]))
    saved=session['turns'][0]['answer']['result']
    assert saved['delivery_progress']=={'requested':1,'completed':1,'state':'complete'}
    assert saved['scripts'][0]['rows'][0]['end_ms']==4000


def test_spoken_analysis_uses_transcript_without_sending_media(setup):
    store,p,media,a=setup
    a['metadata'].update(duration_ms=1200,transcript_source='user_import',transcript='产品使用方式',subtitles=[{'start_ms':0,'end_ms':1200,'text':'产品使用方式'}])
    store.put(p,'asset',a,a['id'])
    class TextOnly(Answer):
        def chat(self,system,question,**kwargs):
            assert not kwargs.get('images') and not kwargs.get('video')
            return super().chat(system,question,**kwargs)
    result=AnalysisAgent(store,media).analyze(AnalysisScope(project_id=p,asset_ids=[a['id']]),'directed','比较口播',TextOnly(),analysis_kind='spoken')
    evidence=result['turns'][0]['evidence'][0]
    assert evidence['provenance']=='subtitle'
    assert evidence['transcript']=='产品使用方式'
    assert result['analysis_kind']=='spoken'


def test_spoken_missing_transcript_refuses_before_model_call(setup):
    store,p,media,a=setup; client=Answer()
    with pytest.raises(ValueError,match='文字稿|字幕'):
        AnalysisAgent(store,media).analyze(AnalysisScope(project_id=p,asset_ids=[a['id']]),'directed','口播',client,analysis_kind='spoken')
    assert not client.questions


def test_discussion_without_evidence_is_explicit_and_cannot_claim_facts(setup):
    store,p,media,a=setup
    session=AnalysisAgent(store,media).analyze(AnalysisScope(project_id=p), 'inspiration','讨论创意',Answer(),discussion_only=True)
    assert session['discussion_only'] is True
    assert session['turns'][0]['evidence']==[]
    with pytest.raises(ValueError):
        AnalysisAgent(store,media).analyze(AnalysisScope(project_id=p,asset_ids=[a['id']]), 'inspiration','讨论',Answer(),discussion_only=True)
