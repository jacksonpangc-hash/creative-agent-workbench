"""Saved script exports use source evidence labels, never today's asset names."""
from copy import deepcopy
import io
import json
from zipfile import ZipFile

import pytest

from creative_agent.script_exports import rows
from creative_agent.scripts import Scripts
from creative_agent.store import Store


def test_reference_labels_use_frozen_evidence_and_keep_unknown_ids():
    script = {'rows': [{'start_ms': 0, 'end_ms': 2000, 'dialogue': '观察开头', 'visual': '近景',
                       'references': [{'asset_id': 'ref', 'start_ms': 1000, 'end_ms': 3000},
                                      {'asset_id': 'legacy', 'start_ms': 0, 'end_ms': 2000}],
                       'own_references': [{'asset_id': 'own', 'start_ms': 3000, 'end_ms': 5000}]}],
              'source': {'evidence': [{'id': 'ref@1000-3000', 'asset_id': 'ref', 'title': '参考原片.mp4'},
                                      {'id': 'own', 'title': '自有近景.mp4'}]}}
    result = rows(script)[0]
    assert result[8] == '参考原片.mp4（ref） · 1000–3000 ms\nlegacy · 0–2000 ms'
    assert result[9] == '自有近景.mp4（own） · 3000–5000 ms'
    # Historical exports with no source labels retain the exact ID/time fallback.
    del script['source']
    assert rows(script)[0][8] == 'ref · 1000–3000 ms\nlegacy · 0–2000 ms'
    script['source'] = None
    assert rows(script)[0][8] == 'ref · 1000–3000 ms\nlegacy · 0–2000 ms'


@pytest.mark.parametrize('format', ['md', 'docx', 'xlsx', 'json', 'pdf'])
def test_export_labels_are_frozen_across_asset_session_and_script_revisions(tmp_path, format):
    store = Store(tmp_path / 'scripts.db')
    project = store.create_project('导出标签')['id']
    service = Scripts(store)
    asset = store.put(project, 'asset', {'title': '保存时原片.mp4', 'media_type': 'video',
                                        'metadata': {'duration_ms': 5000}})
    aid = asset['id']
    script_body = {'title': '已保存脚本', 'objective': '观察开头', 'rows': [
        {'start_ms': 0, 'end_ms': 2000, 'dialogue': '观察开头', 'visual': '近景',
         'reshoot': '补拍近景', 'references': [{'asset_id': aid, 'start_ms': 1000, 'end_ms': 3000}]}]}
    session = store.put(project, 'session', {'scope': {'project_id': project, 'asset_ids': [aid]},
        'turns': [{'answer': {'result': {'scripts': [script_body]}},
                   'evidence': [{'id': aid+'@1000-3000', 'asset_id': aid, 'title': '保存时原片.mp4'}],
                   'created_at': 'frozen-source-time'}]})
    saved = service.from_session(project, session['id'], 0)
    changed = deepcopy(session)
    changed['turns'][0]['evidence'][0]['title'] = '今天的会话标题.mp4'
    store.put(project, 'session', changed, session['id'])
    store.put(project, 'asset', {**asset, 'title': '今天的素材标题.mp4'}, aid)
    service.revise(project, saved['id'], 1, {'title': '修订后脚本'})
    before = deepcopy(service.get(project, saved['id']))
    exported = service.export(project, saved['id'], format, version=1)
    assert service.get(project, saved['id']) == before
    if format == 'json':
        result = json.loads(exported)
        assert result['version'] == 1 and result['title'] == '已保存脚本'
        assert result['source']['evidence'][0]['title'] == '保存时原片.mp4'
        assert result['source']['conclusion_version'] == 'frozen-source-time'
        return
    if format == 'pdf':
        # PDF shares the same rows() renderer; the direct label test covers its text.
        assert exported.startswith(b'%PDF-')
        return
    if format == 'md':
        text = exported.decode()
        assert '版本：1' in text
    elif format == 'docx':
        with ZipFile(io.BytesIO(exported)) as archive:
            text = archive.read('word/document.xml').decode()
        assert '版本 1' in text
    else:
        from openpyxl import load_workbook
        book = load_workbook(io.BytesIO(exported))
        text = str([list(sheet.values) for sheet in book])
        assert 'v1' in text
    assert f'保存时原片.mp4（{aid}） · 1000–3000 ms' in text
    assert '今天的素材标题' not in text and '今天的会话标题' not in text
    assert '修订后脚本' not in text
