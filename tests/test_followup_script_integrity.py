"""Concrete T3 §5.4 counterexamples; local SQLite only, with no model calls."""
from copy import deepcopy
import io
import json
from zipfile import ZipFile

import pytest

from creative_agent.scripts import Scripts
from creative_agent.store import Store


@pytest.fixture
def core(tmp_path):
    store = Store(tmp_path / 'scripts.db')
    project = store.create_project('脚本业务校验')['id']
    return store, Scripts(store), project


def body(dialogue='把桌面整理好。', **changes):
    result = {
        'title': '收纳盒拍摄稿', 'objective': '展示整理动作', 'confirmed': True,
        'rows': [{'start_ms': 0, 'end_ms': 8000, 'dialogue': dialogue,
                  'visual': '把桌面杂物放入盒内', 'reshoot': '补拍桌面整理动作'}],
    }
    result.update(changes)
    return result


def own_asset(core, tmp_path, *, metadata=None, source_kind='own'):
    store, _, project = core
    path = tmp_path / 'own.mp4'
    path.write_bytes(b'controlled file locator, not visual evidence')
    return store.put(project, 'asset', {
        'source_kind': source_kind, 'media_type': 'video', 'path': str(path),
        'metadata': {'duration_ms': 10000, 'reuse_allowed': True, **(metadata or {})},
    })


def with_footage(asset, *, start=0, end=200, **usage):
    data = body()
    data['rows'][0].update(reshoot='', own_references=[{
        'asset_id': asset['id'], 'start_ms': start, 'end_ms': end, **usage,
    }])
    return data


@pytest.mark.parametrize('dialogue', ['可以承重50公斤。', '到手价19.9元。'])
def test_numeric_bearing_and_price_still_require_product_evidence(core, dialogue):
    _, service, project = core
    with pytest.raises(ValueError, match='依据|核实'):
        service.create(project, body(dialogue))


@pytest.mark.parametrize('dialogue', ['防水防潮。', '可以彻底治愈湿疹。', '承重能力强。'])
def test_empty_factual_claims_cannot_confirm_key_product_claims(core, dialogue):
    _, service, project = core
    data = body(dialogue)
    data['rows'][0]['factual_claims'] = []
    with pytest.raises(ValueError, match='依据|核实'):
        service.create(project, data)


@pytest.mark.parametrize('reviewed', [False, True])
def test_irrelevant_existing_fact_cannot_support_non_numeric_claim(core, reviewed):
    _, service, project = core
    service.save_facts(project, 0, {'product': '收纳盒', 'claims': [{
        'fact_id': 'color', 'statement': '白色', 'source': '包装标识', 'confirmed': True,
    }]})
    data = body('防水防潮。')
    data['rows'][0].update(claim_ids=['color'], factual_claims=[{
        'statement': '防水防潮', 'fact_ids': ['color'], 'reviewed': reviewed,
    }])
    with pytest.raises(ValueError, match='依据|核实|支持'):
        service.create(project, data)


def test_declaring_an_unrelated_claim_does_not_cover_the_actual_dialogue(core):
    _, service, project = core
    service.save_facts(project, 0, {'product': '收纳盒', 'claims': [{
        'fact_id': 'color', 'statement': '白色', 'source': '包装标识', 'confirmed': True,
    }]})
    data = body('防水防潮。')
    data['rows'][0].update(claim_ids=['color'], factual_claims=[{
        'statement': '白色', 'fact_ids': ['color'], 'reviewed': True,
    }])
    with pytest.raises(ValueError, match='依据|核实'):
        service.create(project, data)


def test_saved_confirmed_evidence_can_support_the_exact_key_claim(core):
    _, service, project = core
    service.save_facts(project, 0, {'product': '收纳盒', 'claims': [{
        'fact_id': 'water', 'statement': '防水防潮', 'source': '该SKU有效测试报告', 'confirmed': True,
    }]})
    data = body('防水防潮。')
    data['rows'][0]['claim_ids'] = ['water']
    script = service.create(project, data)
    assert script['confirmed'] and script['validation_issues'] == []


def test_existing_human_review_of_non_critical_paraphrase_is_preserved(core):
    _, service, project = core
    service.save_facts(project, 0, {'product': '零食', 'claims': [{
        'fact_id': 'package', 'statement': '独立包装', 'source': '包装清单', 'confirmed': True,
    }]})
    data = body('方便随身携带。')
    data['rows'][0]['factual_claims'] = [{
        'statement': '方便随身携带', 'fact_ids': ['package'], 'reviewed': True,
    }]
    assert service.create(project, data)['confirmed']


def test_revision_invalidates_confirmation_and_rechecks_changed_dialogue(core):
    _, service, project = core
    saved = service.create(project, body())
    rows = deepcopy(saved['rows'])
    rows[0]['dialogue'] = '防水防潮。'
    revised = service.revise(project, saved['id'], 1, {'rows': rows})
    assert revised['confirmed'] is False
    assert revised['validation_issues']
    with pytest.raises(ValueError, match='依据|核实'):
        service.revise(project, saved['id'], 2, {'confirmed': True})


def test_explicit_production_note_can_remain_when_confirming(core):
    _, service, project = core
    note = '周五试拍，注意收纳盒开盖动作和灯光。'
    saved = service.create(project, body(production_notes=[note]))
    assert saved['confirmed'] is True
    assert saved['production_notes'] == [note]
    assert note in service.export(project, saved['id'], 'md').decode()


@pytest.mark.parametrize('field', ['production_notes', 'notes'])
@pytest.mark.parametrize('note', [
    '台词只用可验证动作与场景描述，不写价格、促销、销量、功效或未确认成分。',
    '不使用未经授权素材。',
])
def test_prohibition_note_allows_draft_confirmation_without_removing_constraint(core, field, note):
    _, service, project = core
    data = body(confirmed=False)
    if field == 'production_notes':
        data[field] = [note]
    else:
        data['rows'][0][field] = note
    draft = service.create(project, data)
    confirmed = service.revise(project, draft['id'], 1, {'confirmed': True})
    assert confirmed['confirmed'] is True
    assert confirmed['validation_issues'] == confirmed['unresolved_items'] == []
    assert (confirmed[field] if field == 'production_notes' else confirmed['rows'][0][field]) == ([note] if field == 'production_notes' else note)


@pytest.mark.parametrize('edit_first', [False, True])
@pytest.mark.parametrize('manual', ['', '商品功效待核实', '复用权限尚未确认'])
def test_previously_saved_prohibition_issue_is_rechecked_without_clearing_manual_pending(core, edit_first, manual):
    store, service, project = core
    note = '台词只用可验证动作与场景描述，不写价格、促销、销量、功效或未确认成分。'
    issue = '制作备注中仍有需要核实的事实或权限：' + note
    # Controlled pre-fix v2 storage shape, including both generated-issue lists.
    saved = store.put(project, 'script', body(
        confirmed=False, version=2, revisions=[], production_notes=[note],
        validation_issues=[issue], unresolved_items=[issue] + ([manual] if manual else []),
    ))
    if edit_first:
        saved = service.revise(project, saved['id'], 2, {'title': '更新试拍安排'})
        assert saved['validation_issues'] == []
        assert saved['unresolved_items'] == ([manual] if manual else [])
    if manual:
        with pytest.raises(ValueError, match=manual):
            service.revise(project, saved['id'], saved['version'], {'confirmed': True})
    else:
        confirmed = service.revise(project, saved['id'], saved['version'], {'confirmed': True})
        assert confirmed['confirmed'] is True
        assert confirmed['validation_issues'] == confirmed['unresolved_items'] == []
        assert confirmed['production_notes'] == [note]


@pytest.mark.parametrize('field', ['production_notes', 'notes'])
@pytest.mark.parametrize('note', [
    '商品功效待核实', '复用权限尚未确认', 'SKU款式未知',
    '不要夸大；商品功效待核实',
    '不写价格、促销、销量、功效或未确认成分；复用权限尚未确认',
    '不使用未经授权素材，商品功效待核实',
    '商品功效，待核实',
    '不要说未经核实的话，台词：“商品功效待核实”',
    '台词：“不使用未经授权素材”',
])
def test_positive_pending_or_quoted_claim_still_blocks_confirmation(core, field, note):
    _, service, project = core
    data = body(confirmed=False)
    if field == 'production_notes':
        data[field] = [note]
    else:
        data['rows'][0][field] = note
    draft = service.create(project, data)
    assert len(draft['validation_issues']) == 1
    with pytest.raises(ValueError, match='核实'):
        service.revise(project, draft['id'], 1, {'confirmed': True})


@pytest.mark.parametrize('field', ['dialogue', 'subtitle'])
def test_note_prohibition_does_not_exempt_copied_warning_in_live_claim_fields(core, field):
    _, service, project = core
    data = body(confirmed=False)
    data['rows'][0][field] = '台词只用可验证动作与场景描述，不写价格、促销、销量、功效或未确认成分。'
    draft = service.create(project, data)
    assert draft['validation_issues']
    with pytest.raises(ValueError, match='依据|核实'):
        service.revise(project, draft['id'], 1, {'confirmed': True})


@pytest.mark.parametrize('item', ['待核实防水功效依据', '制作备注：复用权限尚未确认', '旧版未分类待办'])
def test_manual_pending_fact_permission_and_unclassified_history_still_block(core, item):
    _, service, project = core
    with pytest.raises(ValueError, match='核实|待处理|确认'):
        service.create(project, body(unresolved_items=[item]))


def test_pending_permission_cannot_be_relabelled_as_a_production_note(core):
    _, service, project = core
    with pytest.raises(ValueError, match='权限|核实|确认'):
        service.create(project, body(production_notes=['复用权限尚未确认']))


def test_client_supplied_validation_issues_cannot_erase_a_manual_blocker(core):
    _, service, project = core
    pending = '复用权限尚未确认'
    with pytest.raises(ValueError, match='权限|核实|确认'):
        service.create(project, body(unresolved_items=[pending], validation_issues=[pending]))


def test_two_tenths_of_a_second_cannot_be_eight_seconds_of_normal_footage(core, tmp_path):
    _, service, project = core
    data = with_footage(own_asset(core, tmp_path))
    with pytest.raises(ValueError, match='时长|覆盖|片段'):
        service.create(project, data)
    data['confirmed'] = False
    draft = service.create(project, data)
    assert draft['validation_issues']
    assert draft['rows'][0]['own_references'][0]['end_ms'] == 200


def test_duplicate_overlapping_source_ranges_do_not_multiply_normal_duration(core, tmp_path):
    _, service, project = core
    data = with_footage(own_asset(core, tmp_path), end=4000)
    data['rows'][0]['own_references'] *= 2
    with pytest.raises(ValueError, match='时长|覆盖|片段'):
        service.create(project, data)


def test_explicit_freeze_can_fill_display_duration_without_inventing_source_observation(core, tmp_path):
    _, service, project = core
    data = with_footage(own_asset(core, tmp_path), usage_mode='freeze_frame', use_duration_ms=8000,
                        usage_note='取片段末帧定格，屏幕上展示8秒')
    saved = service.create(project, data)
    ref = saved['rows'][0]['own_references'][0]
    assert (ref['start_ms'], ref['end_ms']) == (0, 200)
    assert ref['provenance'] == 'metadata'
    assert ref['use_duration_ms'] == 8000
    assert '定格' in service.export(project, saved['id'], 'md').decode()


def test_partial_available_footage_needs_explicit_remaining_reshoot_duration(core, tmp_path):
    _, service, project = core
    data = with_footage(own_asset(core, tmp_path))
    data['rows'][0].update(reshoot='补拍继续整理桌面的近景动作', reshoot_duration_ms=7800)
    saved = service.create(project, data)
    assert saved['confirmed']
    assert saved['rows'][0]['own_references'][0]['end_ms'] == 200
    assert saved['rows'][0]['reshoot_duration_ms'] == 7800


@pytest.mark.parametrize('metadata', [
    {'reuse_allowed': None},
    {'reuse_allowed': False},
    {'reuse_allowed': True, 'rights': {'derivative_create': False}},
    {'reuse_allowed': True, 'rights': {'derivative_create': True, 'expires_at': '2000-01-01T00:00:00Z'}},
    {'reuse_allowed': True, 'rights': {'derivative_create': True}},
])
def test_unknown_denied_or_expired_rights_cannot_become_own_footage(core, tmp_path, metadata):
    _, service, project = core
    asset = own_asset(core, tmp_path, metadata=metadata)
    with pytest.raises(ValueError, match='权限|授权|用途'):
        service.create(project, with_footage(asset, end=8000))


def test_missing_reuse_permission_is_not_implicitly_granted(core, tmp_path):
    store, service, project = core
    asset = own_asset(core, tmp_path)
    del asset['metadata']['reuse_allowed']
    asset = store.put(project, 'asset', asset, asset['id'])
    with pytest.raises(ValueError, match='权限|授权|用途'):
        service.create(project, with_footage(asset, end=8000))


def test_valid_explicit_derivative_rights_can_allow_the_own_clip(core, tmp_path):
    _, service, project = core
    asset = own_asset(core, tmp_path, metadata={'reuse_allowed': None, 'rights': {
        'derivative_create': True, 'expires_at': '2999-01-01T00:00:00Z',
        'evidence': '受控测试授权记录',
    }})
    assert service.create(project, with_footage(asset, end=8000))['confirmed']


def test_explicit_special_use_never_grants_reference_material_reuse_rights(core, tmp_path):
    _, service, project = core
    asset = own_asset(core, tmp_path, source_kind='inspiration')
    data = with_footage(asset, usage_mode='freeze_frame', use_duration_ms=8000, usage_note='定格展示8秒')
    with pytest.raises(ValueError, match='研究|自有|权限'):
        service.create(project, data)


@pytest.mark.parametrize('format', ['md', 'json', 'docx', 'xlsx'])
def test_exports_preserve_source_range_display_usage_and_production_note(core, tmp_path, format):
    _, service, project = core
    data = with_footage(own_asset(core, tmp_path), usage_mode='freeze_frame', use_duration_ms=8000,
                        usage_note='取末帧定格展示')
    data['production_notes'] = ['周五试拍，注意灯光']
    saved = service.create(project, data)
    exported = service.export(project, saved['id'], format)
    if format == 'json':
        decoded = json.loads(exported)
        assert decoded['production_notes'] == ['周五试拍，注意灯光']
        assert decoded['rows'][0]['own_references'][0]['use_duration_ms'] == 8000
        return
    if format == 'md':
        text = exported.decode()
    elif format == 'docx':
        with ZipFile(io.BytesIO(exported)) as archive:
            text = archive.read('word/document.xml').decode()
    else:
        from openpyxl import load_workbook
        book = load_workbook(io.BytesIO(exported))
        text = str([list(sheet.values) for sheet in book])
    assert '0–200 ms' in text
    assert '定格' in text and '8000' in text
    assert '周五试拍，注意灯光' in text


@pytest.mark.parametrize('change', ['revoked', 'expired'])
def test_production_rechecks_current_reuse_rights_after_script_confirmation(core, tmp_path, change):
    store, service, project = core
    asset = own_asset(core, tmp_path)
    saved = service.create(project, with_footage(asset, end=8000))
    if change == 'revoked':
        asset['metadata']['reuse_allowed'] = False
    else:
        asset['metadata']['rights'] = {
            'derivative_create': True, 'expires_at': '2000-01-01T00:00:00Z',
            'evidence': '现已过期的受控授权记录',
        }
    store.put(project, 'asset', asset, asset['id'])
    with pytest.raises(ValueError, match='权限|授权|用途'):
        service.produce(project, saved['id'], 1, 'after-rights-change', '拍摄负责人')
    assert store.list(project, 'production') == []


@pytest.mark.parametrize('dialogue', ['承重50公斤。', '防水防潮。'])
def test_model_output_and_saved_session_use_the_same_business_checks(core, dialogue):
    from creative_agent.agent import AnalysisAgent
    store, service, project = core
    facts = service.save_facts(project, 0, {'product': '收纳盒'})
    answer = AnalysisAgent(store, None).prepare_scripts(
        project, {'result': {'scripts': [body(dialogue)]}}, facts,
        {'own_assets': []}, {'project_id': project, 'asset_ids': []},
    )
    generated = answer['result']['scripts'][0]
    assert generated['confirmed'] is False and generated['validation_issues']
    session = store.put(project, 'session', {
        'scope': {'project_id': project, 'asset_ids': []},
        'turns': [{'answer': answer, 'evidence': [], 'product_facts_snapshot': facts}],
    })
    saved = service.from_session(project, session['id'], 0)
    assert saved['confirmed'] is False and saved['validation_issues']
    with pytest.raises(ValueError, match='依据|核实'):
        service.revise(project, saved['id'], 1, {'confirmed': True})


def test_zero_reshoot_duration_does_not_require_an_unneeded_reshoot_description(core, tmp_path):
    _, service, project = core
    data = with_footage(own_asset(core, tmp_path), end=8000)
    data['rows'][0]['reshoot_duration_ms'] = 0
    assert service.create(project, data)['confirmed']


@pytest.mark.parametrize('permission', ['missing', 'unknown', 'expired', 'valid'])
def test_footage_search_applies_current_reuse_rights_before_returning_candidates(core, tmp_path, permission):
    from creative_agent.creative_tasks import TaskSpec, freeze_selection
    from creative_agent.footage import FootageSearch
    store, _, project = core
    asset = own_asset(core, tmp_path)
    asset['inspection'] = 'ready'
    asset['metadata']['subtitles'] = [{'start_ms': 0, 'end_ms': 200, 'text': '整理桌面'}]
    asset['metadata']['transcript_source'] = 'user_import'
    import hashlib
    asset['sha256']=hashlib.sha256(__import__('pathlib').Path(asset['path']).read_bytes()).hexdigest()
    if permission == 'missing':
        del asset['metadata']['reuse_allowed']
    elif permission == 'unknown':
        asset['metadata']['reuse_allowed'] = None
    elif permission == 'expired':
        asset['metadata']['rights'] = {
            'derivative_create': True, 'expires_at': '2000-01-01T00:00:00Z',
            'evidence': '现已过期的受控授权记录',
        }
    store.put(project, 'asset', asset, asset['id'])
    selected = freeze_selection(store, project, {}, TaskSpec(own_asset_ids=[asset['id']]))
    candidates = FootageSearch(store, None).search(project, '整理桌面', selected)
    if permission == 'valid':
        assert len(candidates) == 1 and candidates[0]['asset_id'] == asset['id']
    else:
        assert candidates == []


@pytest.mark.parametrize('entry', ['production', 'reconfirmation'])
@pytest.mark.parametrize('change', ['revoked', 'rewritten', 'sku_changed', 'removed', 'expired'])
def test_current_referenced_product_fact_must_still_apply_before_production_or_reconfirmation(core, entry, change):
    store, service, project = core
    original = {'product': '收纳盒', 'sku_id': 'A', 'claims': [{
        'fact_id': 'water', 'statement': '防水防潮', 'source': 'A款有效测试报告', 'confirmed': True,
    }]}
    service.save_facts(project, 0, original)
    data = body('防水防潮。')
    data['rows'][0]['factual_claims'] = [{'statement': '防水防潮', 'fact_ids': ['water']}]
    script = service.create(project, data)
    changed = deepcopy(original)
    if change == 'revoked':
        changed['claims'][0]['confirmed'] = False
    elif change == 'rewritten':
        changed['claims'][0]['statement'] = '仅防潮，不防水'
    elif change == 'sku_changed':
        changed['sku_id'] = 'B'
    elif change == 'removed':
        changed['claims'] = []
    else:
        changed['claims'][0]['valid_until'] = '2000-01-01'
    service.save_facts(project, 1, changed)
    frozen = service.get(project, script['id'])
    assert frozen['product_facts_snapshot']['sku_id'] == 'A'
    assert frozen['product_facts_snapshot']['claims'][0]['statement'] == '防水防潮'
    assert json.loads(service.export(project, script['id'], 'json'))['product_facts_snapshot'] == frozen['product_facts_snapshot']
    if entry == 'production':
        with pytest.raises(ValueError, match='当前|撤销|变更|失效|过期|SKU|款式'):
            service.produce(project, script['id'], 1, 'current-fact-check', '负责人')
    else:
        draft = service.revise(project, script['id'], 1, {'title': '历史稿整理', 'confirmed': False})
        assert draft['product_facts_snapshot'] == frozen['product_facts_snapshot']
        with pytest.raises(ValueError, match='当前|撤销|变更|失效|过期|SKU|款式'):
            service.revise(project, script['id'], 2, {'confirmed': True})
    assert store.list(project, 'production') == []


def test_unrelated_fact_updates_do_not_invalidate_the_referenced_fact_or_historical_snapshot(core):
    _, service, project = core
    original = {'product': '收纳盒', 'sku_id': 'A', 'claims': [
        {'fact_id': 'water', 'statement': '防水防潮', 'source': 'A款有效测试报告', 'confirmed': True},
        {'fact_id': 'color', 'statement': '白色', 'source': '包装', 'confirmed': True},
    ]}
    service.save_facts(project, 0, original)
    data = body('防水防潮。')
    data['rows'][0]['claim_ids'] = ['water']
    script = service.create(project, data)
    changed = deepcopy(original)
    changed['claims'][1].update(statement='蓝色', confirmed=False)
    changed['audience'] = '补充不影响防水事实的受众备注'
    service.save_facts(project, 1, changed)
    task = service.produce(project, script['id'], 1, 'unrelated-update', '负责人')
    assert task['script_snapshot']['product_facts_snapshot'] == script['product_facts_snapshot']
    reconfirmed = service.revise(project, script['id'], 1, {'confirmed': True})
    assert reconfirmed['confirmed'] is True
    assert reconfirmed['product_facts_snapshot'] == script['product_facts_snapshot']
