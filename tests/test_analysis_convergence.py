"""Analysis accepts authorized upstream content and saves reusable outputs once."""
from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from creative_agent.agent import AnalysisAgent, Instructions, Reports
from creative_agent.contracts import AnalysisScope
from test_agent import Answer, setup


def test_reference_only_scope_accepts_up_to_200_references():
    scope = AnalysisScope(project_id='project', reference_ids=[f'ref-{i}' for i in range(200)])
    assert len(scope.reference_ids) == 200
    with pytest.raises(ValueError):
        AnalysisScope(project_id='project', reference_ids=[f'ref-{i}' for i in range(201)])


def test_scope_caps_assets_and_references_together():
    with pytest.raises(ValueError, match='200'):
        AnalysisScope(project_id='project', asset_ids=[f'asset-{i}' for i in range(100)],
                      reference_ids=[f'ref-{i}' for i in range(101)])


def reference(project_id, reference_id='reference-1'):
    return dict(id=reference_id, project_id=project_id, title='参考绿豆糕口播',
                provider='cmm', source_kind='industry', content_basis='transcript',
                provenance={'provider': 'cmm', 'operation': 'extract_video_copywriting',
                            'schema_version': '2026-10-14', 'provider_task_id': None, 'content_basis': 'transcript'},
                text='打开包装，掰开绿豆糕。',
                source_url='https://example.test/video/1', operation='extract_video_copywriting',
                metrics={}, untrusted_source_content=True)


def test_reference_only_analysis_preserves_upstream_content_and_freezes_followup(setup):
    store, project, media, _ = setup
    external = reference(project)
    # A selected supplier snapshot needs both its durable source and current rights.
    from creative_agent.cmm import CMMService
    from test_industry import Keys,rights
    svc=CMMService(store,Keys());svc.connect(project,api_key='offline-only');svc.update_rights(project,rights())
    job=store.put(project,'cmm_job',{'operation':'extract_video_copywriting','rights_snapshot':rights()})
    store.put_if_absent(project,'cmm_reference',{**external,'job_id':job['id'],'rights_snapshot':rights()},external['id'])
    agent = AnalysisAgent(store, media)
    client = Answer()
    session = agent.analyze(AnalysisScope(project_id=project, reference_ids=[external['id']]),
                            'directed', '比较口播结构', client, external_evidence=[external])
    assert len(client.questions) == 1  # Upstream text is never sent as a fabricated media observation.
    assert session['scope']['asset_ids'] == []
    assert session['scope']['reference_ids'] == ['reference-1']
    assert session['turns'][0]['evidence'] == [external]
    assert json.loads(client.questions[0])['context']['evidence'][0]['provenance']['operation'] == 'extract_video_copywriting'
    external['text'] = '外部内容后来改变'
    followup = agent.followup(project, session['id'], '继续给拍摄建议', client)
    assert followup['turns'][-1]['evidence'][0]['text'] == '打开包装，掰开绿豆糕。'
    assert 'asset_id' not in followup['turns'][-1]['evidence'][0]


@pytest.mark.parametrize('change', [
    {'project_id': 'another-project'}, {'id': 'not-selected'},
    {'content_basis': 'metadata'}, {'text': '   '}, {'provenance': 'native_video'},
])
def test_invalid_external_evidence_refuses_before_model_call(setup, change):
    store, project, media, _ = setup
    external = {**reference(project), **change}
    client = Answer()
    with pytest.raises(ValueError):
        AnalysisAgent(store, media).analyze(
            AnalysisScope(project_id=project, reference_ids=['reference-1']),
            'directed', '分析参考', client, external_evidence=[external])
    assert client.questions == []
    assert store.list(project, 'session') == []


def test_reference_selection_requires_exact_external_evidence_match(setup):
    store, project, media, _ = setup
    client = Answer()
    with pytest.raises(ValueError):
        AnalysisAgent(store, media).analyze(
            AnalysisScope(project_id=project, reference_ids=['reference-1']),
            'directed', '分析参考', client)
    assert client.questions == []


def test_discussion_cannot_include_industry_references(setup):
    store, project, media, _ = setup
    client = Answer()
    with pytest.raises(ValueError):
        AnalysisAgent(store, media).analyze(
            AnalysisScope(project_id=project, reference_ids=['reference-1']),
            'inspiration', '讨论', client, discussion_only=True)
    assert client.questions == []


def test_discovered_assets_and_references_share_runtime_limit(setup):
    store, project, media, asset = setup
    for index in range(199):
        store.put(project, 'asset', {**asset, 'title': f'素材{index}'})
    with pytest.raises(ValueError, match='200'):
        AnalysisAgent(store, media).collect(AnalysisScope(
            project_id=project, discovery=True, reference_ids=['reference-1']))


class ContentAnswer(Answer):
    def __init__(self, content):
        super().__init__()
        self.content = content

    def chat(self, *args, **kwargs):
        response = super().chat(*args, **kwargs)
        if not kwargs:
            response['data']['content'] = self.content
        return response


def test_markdown_script_delivery_survives_followup_and_note_export(setup):
    store, project, media, _ = setup
    initial = '## 创作草稿\n\n| 镜头 | 口播 |\n| --- | --- |\n| 打开包装 | 今天拆开看看 |\n\n产品价格：[待确认]'
    client = ContentAnswer(initial)
    agent = AnalysisAgent(store, media)
    session = agent.analyze(AnalysisScope(project_id=project), 'exploration', '直接给完整脚本',
                            client, discussion_only=True)
    reports = Reports(store)
    note = reports.from_session(project, session['id'], '脚本笔记')
    assert initial in reports.export(project, note['id'], 'md').decode()
    assert note['answer_snapshot']['content'] == initial
    client.content = '## 拍摄建议\n\n用自然光拍摄开箱镜头。'
    updated = agent.followup(project, session['id'], '再给拍摄建议', client)
    assert updated['turns'][-1]['answer']['content'] == client.content


@pytest.mark.parametrize('content', [[], {'text': 'script'}, 42, 'x' * 200001], ids=['list', 'object', 'number', 'oversize'])
def test_malformed_markdown_content_is_not_persisted(setup, content):
    store, project, media, _ = setup
    with pytest.raises(ValueError, match='正文'):
        AnalysisAgent(store, media).analyze(AnalysisScope(project_id=project), 'exploration',
            '给脚本', ContentAnswer(content), discussion_only=True)
    assert store.list(project, 'session') == []


def report_session(store, project):
    return store.put(project, 'session', dict(mode='directed', scope={'asset_ids': []}, turns=[dict(
        answer={'summary': '摘要', 'findings': [
            {'type': 'hypothesis', 'text': '测试开箱', 'evidence_ids': []},
            {'type': 'missing', 'text': '价格待确认', 'evidence_ids': []},
        ], 'content': '## 完整脚本\n\n直接可拍的分镜'}, evidence=[])]))


def test_note_repeated_save_returns_edited_record_and_selection_has_own_identity(setup):
    store, project, _, _ = setup
    session = report_session(store, project)
    reports = Reports(store)
    first = reports.from_session(project, session['id'], '初次标题')
    edited = reports.edit(project, first['id'], '用户手工修订')
    repeated = reports.from_session(project, session['id'], '点击保存时的新标题', turn_index=0)
    assert repeated == edited
    selected = reports.from_session(project, session['id'], '只存结论', finding_indexes=[1, 0, 1])
    assert selected['id'] != first['id']
    assert '完整脚本' not in selected['markdown']
    assert reports.from_session(project, session['id'], '同一选择', finding_indexes=[0, 1]) == selected
    assert len(store.list(project, 'note')) == 2


@pytest.mark.parametrize('selection', [[-1], [2], ['0'], [True]])
def test_note_rejects_invalid_finding_selections_without_writing(setup, selection):
    store, project, _, _ = setup
    session = report_session(store, project)
    with pytest.raises(ValueError):
        Reports(store).from_session(project, session['id'], '结论', finding_indexes=selection)
    assert store.list(project, 'note') == []


def test_parallel_note_saves_create_one_record(setup):
    store, project, _, _ = setup
    session = report_session(store, project)
    with ThreadPoolExecutor(max_workers=4) as pool:
        notes = list(pool.map(lambda _: Reports(store).from_session(project, session['id'], '笔记'), range(8)))
    assert len({note['id'] for note in notes}) == 1
    assert len(store.list(project, 'note')) == 1


def test_instruction_duplicate_create_retains_edit_and_changed_content_is_new(setup):
    store, project, _, _ = setup
    instructions = Instructions(store)
    original = dict(title='分析开头', template='请分析 {{品类}} 的开头', variables=['品类'])
    saved = instructions.save(project, **original)
    edited = instructions.save(project, title='我的修订', template='只分析画面', record_id=saved['id'])
    assert instructions.save(project, **original) == edited
    distinct = instructions.save(project, **{**original, 'template': '请比较 {{品类}} 的开头'})
    assert distinct['id'] != saved['id']
    assert len(store.list(project, 'instruction')) == 2


def test_parallel_instruction_saves_create_one_record(setup):
    store, project, _, _ = setup
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda _: Instructions(store).save(
            project, title='开头分析', template='分析开头'), range(8)))
    assert len({row['id'] for row in rows}) == 1
    assert len(store.list(project, 'instruction')) == 1


def test_upstream_report_never_invents_observation_timestamps(setup):
    store, project, _, _ = setup
    session = report_session(store, project)
    session['turns'][0]['evidence'] = [reference(project)]
    store.put(project, 'session', session, session['id'])
    note = Reports(store).from_session(project, session['id'], '上游文字分析')
    assert 'extract_video_copywriting' in note['markdown']
    assert 'https://example.test/video/1' in note['markdown']
    assert 'ms' not in note['markdown']


def test_bundled_whisper_transcript_keeps_local_asr_provenance(setup):
    store, project, media, asset = setup
    asset['metadata'].update(transcript='打开包装',duration_ms=1000,subtitles=[{'start_ms':0,'end_ms':1000,'text':'打开包装'}],transcription={
        'method': 'whisper.cpp-local', 'reviewed': False,'execution_state':'succeeded','quality_state':'usable'})
    store.put(project, 'asset', asset, asset['id'])
    session = AnalysisAgent(store, media).analyze(
        AnalysisScope(project_id=project, asset_ids=[asset['id']]),
        'directed', '分析口播', Answer(), analysis_kind='spoken')
    evidence = session['turns'][0]['evidence'][0]
    assert evidence['provenance'] == 'local_asr'
    assert evidence['source'] == 'whisper.cpp-local'
    assert evidence['reviewed'] is False

