"""Synthetic product-source regressions; no provider responses or external calls."""
from copy import deepcopy
import json

import pytest

from creative_agent.agent import AnalysisAgent, Reports, validate_answer
from creative_agent.contracts import AnalysisScope
from creative_agent.creative_tasks import TaskSpec, freeze_selection, model_selection
from creative_agent.scripts import Scripts
from creative_agent.store import Store


@pytest.fixture
def core(tmp_path):
    store = Store(tmp_path / 'synthetic.db')
    p = store.create_project('合成商品证据测试')['id']
    facts = Scripts(store).save_facts(p, 0, {'product': '收纳盒', 'sku_id': 'A', 'claims': [
        {'fact_id': 'demo-category', 'statement': '桌面收纳盒', 'source': 'A款包装品类', 'confirmed': True},
        {'fact_id': 'unconfirmed', 'statement': '方便携带', 'source': '待核对文案', 'confirmed': False},
        {'fact_id': 'expired', 'statement': '限时优惠', 'source': '旧活动', 'confirmed': True, 'valid_until': '2000-01-01'},
    ]})
    return store, p, facts


class SyntheticReply:
    def __init__(self, citation='demo-category'):
        self.citation, self.payloads = citation, []

    def chat(self, system, user, **kwargs):
        self.payloads.append(json.loads(user))
        return {'data': {'summary': '从桌面整理动作开始拍。', 'findings': [
            {'type': 'fact', 'text': '商品是桌面收纳盒。', 'evidence_ids': [self.citation]},
        ], 'result': {'comparisons': [], 'recommendations': ['先试拍整理前后对照']}}}


def analyze(core, reply, **kwargs):
    store, p, _ = core
    return AnalysisAgent(store, None).analyze(AnalysisScope(project_id=p), 'directed',
        '怎样展示商品？', reply, discussion_only=True, **kwargs)


def test_known_plain_fact_id_is_normalized_to_frozen_product_evidence(core):
    reply = SyntheticReply()
    session = analyze(core, reply)
    record = session['turns'][0]['evidence'][0]
    assert record['id'].startswith('product-evidence-')
    assert record['evidence_kind'] == 'product_fact' and record['fact_id'] == 'demo-category'
    assert record['statement'] == '桌面收纳盒' and record['source'] == 'A款包装品类'
    assert record['project_id'] == core[1] and record['sku_id'] == 'A'
    assert record['facts_version'] == core[2]['version'] and record['snapshot_at'] == core[2]['updated_at']
    answer = session['turns'][0]['answer']
    assert answer['findings'][0]['evidence_ids'] == [record['id']]
    assert answer['citation_normalizations'] == [{'finding_index': 0, 'original_id': 'demo-category', 'evidence_id': record['id']}]
    assert reply.payloads[0]['context']['product_evidence'] == [record]
    assert 'asset_id' not in record


@pytest.mark.parametrize('citation', ['invented', 'expired', 'unconfirmed'])
def test_unknown_expired_unconfirmed_citations_still_fail(core, citation):
    with pytest.raises(ValueError, match='证据'):
        analyze(core, SyntheticReply(citation))
    assert core[0].list(core[1], 'session') == []


def test_product_registry_is_stable_scoped_versioned_and_fail_closed(core):
    from creative_agent.product_evidence import build_product_evidence
    _, p, facts = core
    records = build_product_evidence(facts, p)
    assert records == build_product_evidence(deepcopy(facts), p) and len(records) == 1
    assert build_product_evidence(facts, 'another-project') == []
    for field, value in [('version', 0), ('updated_at', ''), ('project_id', '')]:
        assert build_product_evidence({**facts, field: value}, p) == []
    for field, value in [('version', 2), ('sku_id', 'B'), ('product_id', 'other-product')]:
        assert build_product_evidence({**facts, field: value}, p)[0]['id'] != records[0]['id']
    for change in [{'confirmed': False}, {'source': ''}, {'valid_until': 'bad-date'}, {'withdrawn': True}]:
        changed = deepcopy(facts); changed['claims'][0].update(change)
        assert build_product_evidence(changed, p) == []
    duplicated = deepcopy(facts); duplicated['claims'].append(deepcopy(facts['claims'][0]))
    assert build_product_evidence(duplicated, p) == []


def test_caller_snapshot_cannot_invent_or_rebind_product_claims(core):
    store, p, facts = core
    frozen = freeze_selection(store, p, {'project_id': p}, None)
    for changes in [{'project_id': 'elsewhere'}, {'sku_id': 'B'}, {'claims': [
        {'fact_id': 'invented', 'statement': '虚构', 'source': '自填', 'confirmed': True}]}]:
        with pytest.raises(ValueError, match='商品|快照|项目'):
            analyze(core, SyntheticReply('invented'), selection_snapshot={**frozen, 'product_facts_snapshot': {**facts, **changes}})


def test_alias_never_overrides_selected_media_or_ambiguous_product_ids(core):
    from creative_agent.product_evidence import build_product_evidence
    product = build_product_evidence(core[2], core[1])[0]
    data = {'summary': '合成', 'findings': [{'type': 'fact', 'text': '观察', 'evidence_ids': ['demo-category']}]}
    media = {'id': 'demo-category', 'asset_id': 'demo-category'}
    assert validate_answer(deepcopy(data), [media, product])['findings'][0]['evidence_ids'] == ['demo-category']
    with pytest.raises(ValueError, match='证据'):
        validate_answer(deepcopy(data), [product, {**product, 'id': product['id'] + '-other'}])
    # Product evidence cannot grant any additional visual ID.
    data['findings'][0]['evidence_ids'] = ['unselected-media']
    with pytest.raises(ValueError, match='证据'):
        validate_answer(data, [product])


def test_followup_retains_frozen_registry_and_filtered_own_identity_without_text_match(core):
    store, p, facts = core
    own = store.put(p, 'asset', {'source_kind': 'own', 'sha256': 'authorized-sha', 'metadata': {
        'transcript': 'UNSELECTED_TRANSCRIPT', 'subtitles': [], 'private_path': '/private/local'}})
    unselected = store.put(p, 'asset', {'source_kind': 'own', 'metadata': {'transcript': 'NEVER_SEND'}})
    spec = TaskSpec(own_asset_ids=[own['id']])
    snapshot = {**freeze_selection(store, p, {'project_id': p}, spec), 'product_facts_snapshot': facts}
    reply = SyntheticReply()
    session = analyze(core, reply, task_spec=spec, selection_snapshot=snapshot)
    assert session['turns'][0]['own_footage_snapshot'] == []
    revised = {k: v for k, v in facts.items() if k in {'product', 'claims', 'sku_id', 'product_id'}}
    revised['audience'] = '新增备注不应进入已冻结上下文'
    Scripts(store).save_facts(p, 1, revised)
    result = AnalysisAgent(store, None).followup(p, session['id'], '继续比较', reply)
    payload = reply.payloads[-1]
    assert payload['selection_snapshot'] == model_selection(snapshot)
    assert payload['own_footage'] == []
    assert payload['product_evidence'] == session['turns'][0]['evidence']
    assert result['turns'][-1]['evidence'] == session['turns'][0]['evidence']
    assert payload['product_facts'] == {k: v for k, v in facts.items() if k != 'revisions'}
    serialized = json.dumps(payload)
    assert own['id'] in serialized
    for private in ['UNSELECTED_TRANSCRIPT', 'NEVER_SEND', '/private/local', unselected['id'], revised['audience']]:
        assert private not in serialized
    note = Reports(store).from_session(p, session['id'], '商品建议')
    for expected in ['商品资料', '桌面收纳盒', 'A款包装品类', 'v1']:
        assert expected in note['markdown']
    assert note['evidence'] == result['turns'][-1]['evidence']


@pytest.mark.parametrize('change', ['withdrawn', 'expired', 'statement', 'source', 'sku'])
def test_followup_excludes_now_unavailable_claim_but_preserves_historical_turn(core, change):
    store, p, facts = core
    session = analyze(core, SyntheticReply())
    original = deepcopy(session['turns'][0])
    revised = {k: deepcopy(v) for k, v in facts.items() if k in {'product', 'claims', 'sku_id', 'product_id'}}
    if change == 'withdrawn': revised['claims'][0]['confirmed'] = False
    elif change == 'expired': revised['claims'][0]['valid_until'] = '2000-01-01'
    elif change == 'statement': revised['claims'][0]['statement'] = '不同商品描述'
    elif change == 'source': revised['claims'][0]['source'] = '不同依据'
    else: revised['sku_id'] = 'B'
    Scripts(store).save_facts(p, 1, revised)
    reply = SyntheticReply()
    with pytest.raises(ValueError, match='证据'):
        AnalysisAgent(store, None).followup(p, session['id'], '继续比较', reply)
    payload = reply.payloads[-1]
    assert payload['product_facts'] == {k: v for k, v in facts.items() if k != 'revisions'}
    assert payload['product_evidence'] == []
    assert payload['product_evidence_unavailable'][0]['fact_id'] == 'demo-category'
    assert payload['product_evidence_unavailable'][0]['reason']
    assert store.get(p, 'session', session['id'])['turns'] == [original]


def test_foreign_project_prefixed_id_is_not_accepted(core):
    from creative_agent.product_evidence import build_product_evidence
    store, p, facts = core
    foreign = store.create_project('外部测试')['id']
    foreign_id = build_product_evidence({**facts, 'project_id': foreign}, foreign)[0]['id']
    with pytest.raises(ValueError, match='证据'):
        analyze(core, SyntheticReply(foreign_id))


def test_synthesis_delivery_prioritizes_business_answer_and_registry_over_metadata(core):
    from creative_agent.modes import task_contract
    guidance = task_contract('directed')['delivery']
    assert '简洁自然的中文' in guidance
    assert 'product_evidence' in guidance and '商品资料' in guidance
    assert '不逐项罗列' in guidance and '高转化' in guidance


def test_withdrawn_product_does_not_block_media_only_followup(core,tmp_path):
    store, p, facts = core
    session = analyze(core, SyntheticReply())
    # A historical selected media observation is unchanged by product withdrawal.
    media = {'id': 'selected-media', 'asset_id': 'selected-media', 'title': '已选画面', 'provenance': 'image'}
    from PIL import Image
    image=tmp_path/'selected.png';Image.new('RGB',(8,8)).save(image)
    store.put_if_absent(p,'asset',{'title':'已选画面','media_type':'image','source_kind':'own','inspection':'ready','path':str(image),'sha256':'fixture','metadata':{}},'selected-media')
    session['scope']['asset_ids']=['selected-media']
    session['turns'][0].pop('context_snapshot_id',None)
    session['turns'][0]['evidence'].insert(0, media)
    store.put(p, 'session', session, session['id'])
    revised = {k: deepcopy(v) for k, v in facts.items() if k in {'product', 'claims', 'sku_id', 'product_id'}}
    revised['claims'][0]['confirmed'] = False
    Scripts(store).save_facts(p, 1, revised)
    reply = SyntheticReply('selected-media')
    result = AnalysisAgent(store, None).followup(p, session['id'], '只比较这个画面', reply)
    assert result['turns'][-1]['evidence'] == [{**media,'legacy_basis':True}]
    assert result['turns'][-1]['product_evidence_unavailable']
    assert result['turns'][0]['evidence'] == session['turns'][0]['evidence']


@pytest.mark.parametrize('identity', [{'sku_id': 'B'}, {'product_id': 'different-product'}])
def test_explicit_task_identity_cannot_use_another_product_or_sku_registry(core, identity):
    reply = SyntheticReply()
    with pytest.raises(ValueError, match='证据'):
        analyze(core, reply, task_spec=TaskSpec(**identity))
    assert reply.payloads[0]['context']['product_evidence'] == []
    assert reply.payloads[0]['context']['product_evidence_unavailable']


def test_batched_scripts_preserve_alias_normalization_with_combined_finding_indexes(core):
    class BatchReply(SyntheticReply):
        def chat(self, system, user, **kwargs):
            result = super().chat(system, user, **kwargs)
            result['data']['result'] = {'scripts': [{
                'title': '整理展示' + str(len(self.payloads)), 'objective': '试拍整理动作',
                'rows': [{'start_ms': 0, 'end_ms': 3000, 'dialogue': '桌面整齐了',
                          'visual': '收纳盒近景', 'reshoot': '补拍收纳动作'}],
            }]}
            return result
    result = analyze(core, BatchReply(), task_spec=TaskSpec(output_type='scripts', count=2, max_generation_rounds=2))
    answer = result['turns'][0]['answer']
    assert len(answer['result']['scripts']) == 2
    assert [x['finding_index'] for x in answer['citation_normalizations']] == [0, 1]
    assert all(x['original_id'] == 'demo-category' for x in answer['citation_normalizations'])


def test_model_context_omits_fact_revision_history_but_keeps_local_frozen_snapshot(core):
    store, p, facts = core
    old = deepcopy(facts)
    old['sku_id'] = 'HISTORICAL_PRIVATE_SKU'
    old['claims'][0]['source'] = 'HISTORICAL_WITHDRAWN_SOURCE'
    old = Scripts(store).save_facts(p, 1, {k: v for k, v in old.items() if k in {'product', 'claims', 'sku_id', 'product_id'}})
    current = Scripts(store).save_facts(p, 2, {k: v for k, v in facts.items() if k in {'product', 'claims', 'sku_id', 'product_id'}})
    reply = SyntheticReply()
    session = analyze((store, p, current), reply)
    assert session['turns'][0]['product_facts_snapshot'] == current
    assert 'revisions' not in reply.payloads[0]['context']['product_facts']
    session['turns'][0]['answer']['result']['scripts'] = [{'title': '历史稿', 'product_facts_snapshot': current}]
    store.put(p, 'session', session, session['id'])
    AnalysisAgent(store, None).followup(p, session['id'], '继续比较', reply)
    for payload in reply.payloads:
        serialized = json.dumps(payload)
        assert 'HISTORICAL_PRIVATE_SKU' not in serialized
        assert 'HISTORICAL_WITHDRAWN_SOURCE' not in serialized
    assert 'revisions' not in reply.payloads[-1]['history'][0]['answer']['result']['scripts'][0]['product_facts_snapshot']


def test_report_preserves_unavailable_product_reasons(core):
    store, p, facts = core
    session = analyze(core, SyntheticReply())
    session['turns'][0]['product_evidence_unavailable'] = [{
        'id': 'product-evidence-old', 'fact_id': 'withdrawn', 'reason': '当前商品说法已撤回',
    }]
    store.put(p, 'session', session, session['id'])
    note = Reports(store).from_session(p, session['id'], '资料状态')
    assert note['product_evidence_unavailable'] == session['turns'][0]['product_evidence_unavailable']
    assert '当前商品说法已撤回' in note['markdown']
