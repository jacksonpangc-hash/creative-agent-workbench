"""The bundled reference corpus is structural context, never commerce evidence."""

import hashlib
import importlib
import importlib.util
import json
import shutil
import socket
from pathlib import Path

import pytest

PACKAGE = Path(__file__).parents[1] / 'src/creative_agent/data/clipcat_patterns'
COMMIT = '6afd5abb040768c7cb41a70b835bb0711971941e'
UPSTREAM_SHA256 = '1ec9da8857fed927552b09fdf603a62262b145c4597508d1712a5620d5e68594'
EXCLUDED = {'title', 'hook_line', 'hook_line_kind', 'voiceover', 'voiceover_lang', 'prompt_en',
            'prompt_zh', 'rank', 'rank_period', 'sales', 'gmv', 'views', 'likes', 'currency'}


def library(**kwargs):
    assert importlib.util.find_spec('creative_agent.pattern_library') is not None, 'Offline library is missing'
    return importlib.import_module('creative_agent.pattern_library').PatternLibrary(**kwargs)


def test_default_library_is_pinned_attributed_and_loaded_without_network(monkeypatch):
    def refuse(*args, **kwargs):
        pytest.fail('The offline library attempted a network connection')

    monkeypatch.setattr(socket, 'create_connection', refuse)
    result = library().search()
    assert result['total'] == 647
    assert len(result['items']) == 20 and result['next_offset'] == 20
    source = result['source']
    assert source['provider'] == 'clipcat_public_patterns'
    assert source['commit'] == COMMIT and source['upstream_sha256'] == UPSTREAM_SHA256
    assert source['record_count'] == 647
    assert source['license'] == 'CC-BY-4.0'
    assert COMMIT in source['license_url'] and COMMIT in source['notice_url']
    assert 'by Clipcat' in source['attribution']
    assert source['not_performance_evidence'] is True
    assert source['metrics_status'] == 'not_provided'
    assert source['human_review_status'] == 'not_individually_reviewed'


def test_safe_package_excludes_speech_metrics_and_unverified_rankings():
    service = library()
    rows = []
    offset = 0
    while True:
        page = service.search(offset=offset, limit=100)
        rows.extend(page['items'])
        if page['next_offset'] is None:
            break
        offset = page['next_offset']
    assert len(rows) == len({row['id'] for row in rows}) == 647
    for row in rows:
        assert EXCLUDED.isdisjoint(row)
        assert row['source'].startswith('https://www.tiktok.com/share/video/')
        assert row['not_performance_evidence'] is True
        assert row['metrics_status'] == 'not_provided'
        assert row['reference_kind'] == 'historical_creative_structure'
        assert row['library_source']['commit'] == COMMIT
    raw = (PACKAGE / 'patterns.json').read_text()
    assert not any(f'"{key}"' in raw for key in EXCLUDED)


def test_chinese_query_and_filters_return_existing_structural_references():
    service = library()
    result = service.search('美国 美妆 痛点')
    assert {row['id'] for row in result['items']} == {'us-beauty-405005', 'us-beauty-773087'}
    assert result['total'] == 2 and result['next_offset'] is None
    assert all(row['market'] == 'us' and row['category'] == 'beauty-personal-care'
               for row in result['items'])
    filtered = service.search(market='US', category='beauty-personal-care')
    assert [row['id'] for row in filtered['items']] == [row['id'] for row in result['items']]
    assert service.search('不存在的结构检索词')['items'] == []
    assert service.search('巴西', market='us')['total'] == 0


def test_category_slug_and_market_alias_filters_preserve_original_values():
    service = library()
    canonical = service.search(market='gb', category='automotive-motorcycle')
    alias = service.search(market='英国', category='automotive--motorcycle')
    assert canonical['total'] > 0
    assert canonical == alias
    assert service.search(market='CN')['total'] == 0


def test_stable_pagination_has_no_overlap_or_fabricated_total():
    service = library()
    first = service.search(limit=7)
    second = service.search(offset=first['next_offset'], limit=7)
    assert first['total'] == second['total'] == 647
    assert not {row['id'] for row in first['items']} & {row['id'] for row in second['items']}
    assert [row['id'] for row in service.search(limit=14)['items']] == [
        row['id'] for row in first['items'] + second['items']]
    last = service.search(offset=640, limit=20)
    assert len(last['items']) == 7 and last['next_offset'] is None
    empty = service.search(offset=900, limit=20)
    assert empty['items'] == [] and empty['total'] == 647 and empty['next_offset'] is None


@pytest.mark.parametrize('arguments', [
    {'offset': -1}, {'offset': True}, {'offset': '1'}, {'limit': 0}, {'limit': 101},
    {'limit': False}, {'query': None}, {'market': []}, {'category': {}},
])
def test_invalid_search_parameters_fail_before_loading_results(arguments):
    with pytest.raises(ValueError):
        library().search(**arguments)


def test_detail_is_attributed_missing_is_none_and_callers_cannot_mutate_library():
    service = library()
    row = service.get('br-automotive-192404')
    assert row['market'] == 'br' and row['duration_sec'] == 23
    assert row['hook'] == 'result-first'
    assert row['library_source']['license'] == 'CC-BY-4.0'
    row['visuals'].clear()
    row['library_source']['commit'] = 'changed'
    assert service.get(row['id'])['visuals']
    assert service.get(row['id'])['library_source']['commit'] == COMMIT
    page = service.search()
    page['source']['commit'] = 'also changed'
    assert service.search()['source']['commit'] == COMMIT
    assert service.get('missing-pattern') is None
    assert service.get('../manifest.json') is None


def test_derived_pain_point_that_copies_an_excluded_opening_line_is_omitted():
    assert library().get('gb-home-668758')['pain_point'] is None


def test_detail_rejects_nontext_identifier():
    with pytest.raises(TypeError):
        library().get(7)


def test_bundled_manifest_matches_safe_projection_and_preserves_upstream_licensing():
    library()
    manifest = json.loads((PACKAGE / 'manifest.json').read_text())
    raw = (PACKAGE / manifest['content_file']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == manifest['content_sha256']
    assert len(json.loads(raw)) == manifest['record_count'] == 647
    assert manifest['upstream_sha256'] == UPSTREAM_SHA256
    assert manifest['source_path'] == 'data/prompts.jsonl'
    assert manifest['modifications']
    assert {'hook', 'hooks'} <= set(manifest['projection_fields'])
    assert EXCLUDED.isdisjoint(manifest['projection_fields'])
    assert 'Section 3 -- License Conditions.' in (PACKAGE / 'LICENSE').read_text()
    notice = (PACKAGE / 'NOTICE.md').read_text()
    assert 'Quoted spoken lines are excluded' in notice
    assert 'not individually reviewed by a human' in notice


def test_tampered_content_and_unsafe_projection_are_rejected(tmp_path):
    library()
    shutil.copytree(PACKAGE, tmp_path / 'patterns')
    target = tmp_path / 'patterns'
    (target / 'patterns.json').write_text('[]')
    with pytest.raises(ValueError, match='校验'):
        library(data_dir=target)
    shutil.copyfile(PACKAGE / 'patterns.json', target / 'patterns.json')
    manifest_path = target / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['content_file'] = '../outside.json'
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        library(data_dir=target)


def test_rehashing_changed_data_cannot_claim_the_pinned_upstream_identity(tmp_path):
    library()
    target = tmp_path / 'patterns'
    shutil.copytree(PACKAGE, target)
    content_path = target / 'patterns.json'
    rows = json.loads(content_path.read_text())
    rows[0]['pain_point'] = 'This is a different dataset'
    raw = json.dumps(rows).encode()
    content_path.write_bytes(raw)
    manifest_path = target / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['content_sha256'] = hashlib.sha256(raw).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='校验'):
        library(data_dir=target)
