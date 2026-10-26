"""Synthetic task-continuity contracts; no provider calls or customer records."""
import json

import pytest

from creative_agent.agent import AnalysisAgent
from creative_agent.contracts import AnalysisScope
from creative_agent.creative_tasks import TaskSpec
from creative_agent.modes import task_contract, validate_result
from creative_agent.store import Store


@pytest.mark.parametrize('question,output_type', [
    ('你好，能帮我做什么？', 'answer'),
    ('解释这个脚本为什么长，不要重写', 'analysis'),
    ('只分析开头，先别给三个方向', 'analysis'),
    ('不要写分镜稿，比较卖点', 'comparison'),
    ('这两个脚本哪个开头更清楚？请比较', 'comparison'),
])
def test_new_turn_resolves_actual_request_instead_of_deliverable_mentions(question, output_type):
    spec = TaskSpec.resolve(question)
    assert spec.output_type == output_type
    assert spec.count == 1
    assert spec.max_generation_rounds == 1


@pytest.mark.parametrize('question', ['比较这几个开头', '解释这个脚本为什么长，不要重写', '这段主要表达什么？'])
def test_generic_followup_cannot_inherit_script_count_or_duration(question):
    prior = TaskSpec(output_type='scripts', count=10, max_generation_rounds=4,
                     target_duration_ms=30000, product_id='product-a', sku_id='sku-a',
                     own_asset_ids=['own-a'], constraints=['只用已有素材'])
    spec = TaskSpec.resolve(question, previous=prior.model_dump())
    assert spec.output_type != 'scripts'
    assert (spec.count, spec.max_generation_rounds, spec.target_duration_ms) == (1, 1, None)
    assert (spec.product_id, spec.sku_id, spec.own_asset_ids) == ('product-a', 'sku-a', ['own-a'])
    assert spec.constraints == ['只用已有素材']


@pytest.mark.parametrize('question', ['继续', '继续写脚本', '按刚才要求再写一遍'])
def test_explicit_script_continuation_keeps_relevant_delivery_requirements(question):
    prior = TaskSpec(output_type='scripts', count=10, target_duration_ms=30000, max_generation_rounds=4)
    spec = TaskSpec.resolve(question, previous=prior)
    assert (spec.output_type, spec.count, spec.target_duration_ms, spec.max_generation_rounds) == ('scripts', 10, 30000, 4)


def test_new_script_request_overrides_previous_quantity():
    spec = TaskSpec.resolve('继续写两条20秒的完整脚本', previous=TaskSpec(output_type='scripts', count=10))
    assert (spec.output_type, spec.count, spec.target_duration_ms) == ('scripts', 2, 20000)


def test_explicit_output_override_takes_precedence_without_stale_batch_requirements():
    prior = TaskSpec(output_type='scripts', count=10, max_generation_rounds=4, target_duration_ms=30000, sku_id='a')
    spec = TaskSpec.resolve('写十条脚本', explicit={'output_type': 'analysis'}, previous=prior)
    assert (spec.output_type, spec.count, spec.max_generation_rounds, spec.target_duration_ms) == ('analysis', 1, 1, None)
    assert spec.sku_id == 'a'
    scripts = TaskSpec.resolve('给一条脚本', explicit={'output_type': 'scripts', 'count': 5})
    assert scripts.count == 5 and scripts.max_generation_rounds == 2


@pytest.mark.parametrize('output_type', ['answer', 'analysis'])
def test_plain_reply_has_no_mode_phase_brief_or_creative_deliverables(output_type):
    spec = TaskSpec(output_type=output_type)
    contract = task_contract('inspiration', task_spec=spec)
    assert contract['required_fields'] == []
    assert 'required' not in contract['result_schema']
    answer = {'summary': '这一段铺垫重复，解释即可。', 'findings': [], 'content': '两次重复介绍使用场景，因此显得长。'}
    validated = validate_result(answer, 'inspiration', task_spec=spec)
    assert validated['content'] == answer['content']
    assert validated['result'] == {}


class LocalReply:
    """Only the model transport is replaced; real task, store and evidence guards run."""
    def __init__(self, *answers):
        self.answers = iter(answers)
        self.payloads = []

    def chat(self, _system, question, **_kwargs):
        self.payloads.append(json.loads(question))
        return {'data': next(self.answers), 'usage': {}}


def test_followup_comparison_after_ten_scripts_is_single_comparison_not_script_batch(tmp_path):
    store = Store(tmp_path / 'synthetic.db')
    project = store.create_project('synthetic')['id']
    agent = AnalysisAgent(store, None)
    client = LocalReply(
        {'summary': '尚未完成', 'findings': [], 'result': {'scripts': []}},
        {'summary': '开头长度不同', 'findings': [], 'result': {'comparisons': ['A更短，B铺垫更长']}},
    )
    session = agent.analyze(AnalysisScope(project_id=project), 'inspiration', '写十条30秒脚本', client, discussion_only=True)
    updated = agent.followup(project, session['id'], '比较这些开头，不要再写脚本', client)
    task = client.payloads[-1]['task']
    assert task['output_spec']['output_type'] == 'comparison'
    assert task['required_fields'] == ['comparisons']
    assert task['output_spec']['count'] == 1
    assert updated['turns'][-1]['answer']['result']['delivery_progress'] == {'requested': 1, 'completed': 1, 'state': 'complete'}
    assert len(client.payloads) == 2


def test_new_plain_reply_is_saved_and_cannot_bypass_top_level_evidence_validation(tmp_path):
    store = Store(tmp_path / 'synthetic.db')
    project = store.create_project('synthetic')['id']
    agent = AnalysisAgent(store, None)
    answer = {'summary': '你好', 'findings': [], 'content': '可以从一个问题开始。'}
    session = agent.analyze(AnalysisScope(project_id=project), 'inspiration', '你好', LocalReply(answer), discussion_only=True)
    assert session['turns'][0]['task_spec']['output_type'] == 'answer'
    assert session['turns'][0]['answer']['content'] == '可以从一个问题开始。'
    invalid = {'summary': '错误事实', 'findings': [{'type': 'fact', 'text': '未提供的结论', 'evidence_ids': ['outside']} ]}
    with pytest.raises(ValueError, match='证据'):
        agent.followup(project, session['id'], '解释原因', LocalReply(invalid))
    assert len(store.get(project, 'session', session['id'])['turns']) == 1


@pytest.mark.parametrize('question', ['继续，不要脚本', '继续，不要重写'])
def test_negated_deliverable_prevents_implicit_script_continuation(question):
    spec = TaskSpec.resolve(question, previous=TaskSpec(output_type='scripts', count=10))
    assert spec.output_type != 'scripts'
    assert spec.count == 1


@pytest.mark.parametrize('output_type', ['answer', 'analysis'])
def test_optional_structured_script_still_requires_complete_dialogue_and_visual(output_type):
    result = {'scripts': [{'title': '只有提纲', 'objective': '说明', 'rows': [
        {'start_ms': 0, 'end_ms': 1000, 'dialogue': '实际台词', 'visual': ''},
    ]}]}
    with pytest.raises(ValueError, match='完整脚本'):
        validate_result({'summary': '提纲', 'findings': [], 'result': result}, 'inspiration', task_spec=TaskSpec(output_type=output_type))


@pytest.mark.parametrize('question', ['解释为什么要写脚本', '分析怎么写脚本', '为什么要给三个方向？'])
def test_explanation_about_a_deliverable_is_analysis_not_creative_generation(question):
    spec = TaskSpec.resolve(question)
    assert spec.output_type == 'analysis'


def test_continuation_preserves_previously_bounded_generation_rounds():
    prior = TaskSpec(output_type='scripts', count=10, max_generation_rounds=1)
    assert TaskSpec.resolve('继续', previous=prior).max_generation_rounds == 1


def test_manual_script_revision_uses_frozen_current_version_without_overwriting_source(tmp_path):
    store = Store(tmp_path / 'synthetic.db')
    project = store.create_project('synthetic')['id']
    original = store.put(project, 'script', {'title': '人工稿', 'objective': '试拍', 'version': 3,
        'rows': [{'start_ms': 0, 'end_ms': 2000, 'dialogue': '保留人工细节', 'visual': '开盖动作'}]})
    revision = {'title': '人工稿修订', 'objective': '试拍', 'rows': [
        {'start_ms': 0, 'end_ms': 1500, 'dialogue': '人工细节', 'visual': '开盖动作'}]}
    client = LocalReply({'summary': '已缩短开头', 'findings': [], 'result': {'scripts': [revision]}})
    result = AnalysisAgent(store, None).analyze(AnalysisScope(project_id=project), 'inspiration',
        '将开头改短一点', client, discussion_only=True, script_context=original)
    payload = client.payloads[-1]
    assert payload['current_saved_script']['id'] == original['id']
    assert payload['current_saved_script']['version'] == 3
    assert payload['current_saved_script']['rows'][0]['dialogue'] == '保留人工细节'
    assert payload['revision_instruction']
    assert payload['task']['output_spec']['output_type'] == 'scripts'
    assert payload['task']['output_spec']['count'] == 1
    assert payload['task']['output_spec']['max_generation_rounds'] == 1
    assert result['turns'][0]['answer']['result']['scripts'][0]['title'] == '人工稿修订'
    assert store.get(project, 'script', original['id']) == original


def test_manual_revision_rejects_foreign_project_before_provider_request(tmp_path):
    store = Store(tmp_path / 'synthetic.db')
    project = store.create_project('synthetic')['id']
    client = LocalReply()
    with pytest.raises(ValueError, match='范围|项目'):
        AnalysisAgent(store, None).analyze(AnalysisScope(project_id=project), 'inspiration',
            '缩短开头', client, discussion_only=True, script_context={'project_id': 'foreign'})
    assert client.payloads == []
    assert store.list(project, 'session') == []
