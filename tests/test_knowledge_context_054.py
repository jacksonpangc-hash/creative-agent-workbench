"""0.5.4 local knowledge acceptance cases; run once with the final delivery checks."""
from pathlib import Path
import hashlib
import json

import pytest

from creative_agent.store import Store
from creative_agent.knowledge_index import KnowledgeIndex
from creative_agent.creative_context import CreativeContextService
from creative_agent.local_embeddings import LocalEmbeddings, EmbeddingUnavailable


@pytest.fixture
def library(tmp_path):
    store = Store(tmp_path / 'main.sqlite3')
    project = store.create_project('当前项目')['id']
    other = store.create_project('另一项目')['id']
    index = KnowledgeIndex(store, embeddings=None)
    return store, project, other, index


def own(store, project, tmp_path, *, text='展示折叠收纳', sku='sku-a', product='product-a'):
    path = tmp_path / f'{project}-{len(store.list(project,"asset"))}.mp4'
    path.write_bytes(b'test-only-video')
    return store.put(project, 'asset', {
        'title': '标题不能成为画面证据', 'source_kind': 'own', 'media_type': 'video',
        'inspection': 'ready', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'path': str(path),
        'metadata': {'duration_ms': 3000, 'product_id': product, 'sku_id': sku, 'reuse_allowed': True,
                     'transcript': text, 'subtitles': [{'start_ms': 0, 'end_ms': 2000, 'text': text}],
                     'transcription': {'quality_state': 'usable', 'review_state': 'corrected',
                                       'reviewed': True, 'method': 'user_import'}}})


def test_durable_outbox_captures_raw_sql_and_survives_restart(library, tmp_path):
    store, p, _, index = library
    asset = own(store, p, tmp_path)
    assert index.jobs(p)[0]['state'] == 'queued'
    index.process_pending(limit=20)
    with store.connection(True) as db:
        changed = {**asset, 'metadata': {**asset['metadata'], 'reuse_allowed': False}}
        db.execute('UPDATE records SET data=? WHERE id=?', (json.dumps(changed), asset['id']))
    restarted = KnowledgeIndex(Store(store.path), embeddings=None)
    assert any(j['state'] == 'queued' for j in restarted.jobs(p))
    assert restarted.search(p, '收纳', pool='own', sku_id='sku-a', product_id='product-a') == []


def test_project_sku_and_live_rights_are_hard_filters(library, tmp_path):
    store, p, other, index = library
    wanted = own(store, p, tmp_path)
    own(store, other, tmp_path)
    own(store, p, tmp_path, sku='different')
    index.process_pending(limit=30)
    found = index.search(p, '折叠收纳', pool='own', sku_id='sku-a', product_id='product-a')
    assert {r['asset_id'] for r in found} == {wanted['id']}
    Path(wanted['path']).unlink()
    assert index.search(p, '折叠收纳', pool='own', sku_id='sku-a', product_id='product-a') == []


def test_explicit_selection_never_expands_and_default_uses_project(library, tmp_path):
    store, p, _, index = library
    a = own(store, p, tmp_path)
    b = own(store, p, tmp_path, text='折叠收纳箱展示')
    index.process_pending(limit=30)
    context = CreativeContextService(store, index=index)
    default = context.build_context(p, 'new_script', query='折叠收纳', product_id='product-a', sku_id='sku-a')
    assert {r['asset_id'] for r in default['own_footage']} == {a['id'], b['id']}
    selected = context.build_context(p, 'new_script', query='折叠收纳', product_id='product-a', sku_id='sku-a', selected_asset_ids=[a['id']])
    assert {r['asset_id'] for r in selected['own_footage']} == {a['id']}
    empty = context.build_context(p, 'new_script', query='折叠收纳', selected_asset_ids=[])
    assert not empty['own_footage']


def test_metadata_only_and_unconfirmed_history_never_become_observation(library):
    store, p, _, index = library
    store.put(p, 'public_reference', {'title': '折叠收纳爆款', 'url': 'https://example.test/video', 'content_basis': 'metadata_only'})
    store.put(p, 'script', {'title': '折叠收纳', 'rows': [{'dialogue': '折叠收纳'}], 'confirmed': False})
    index.process_pending(limit=30)
    result = CreativeContextService(store, index=index).build_context(p, 'new_script', query='折叠收纳')
    assert not result['evidence'] and not result['reference_patterns'] and not result['history_preferences']
    assert any('内容' in r['reason'] for r in result['exclusions'])


def test_revision_reuses_snapshot_but_rechecks_revocation(library, tmp_path):
    store, p, _, index = library
    a = own(store, p, tmp_path)
    index.process_pending(limit=20)
    context = CreativeContextService(store, index=index)
    first = context.build_context(p, 'new_script', query='折叠收纳', product_id='product-a', sku_id='sku-a')
    store.put(p, 'asset', {**a, 'metadata': {**a['metadata'], 'reuse_allowed': False}}, a['id'])
    revised = context.build_context(p, 'revise_current', previous_context_id=first['context_snapshot_id'], query='改短')
    assert not revised['own_footage']
    assert revised['previous_context_id'] == first['context_snapshot_id']
    assert revised['exclusions']


def test_job_cancellation_does_not_mark_semantic_ready(library, tmp_path):
    store, p, _, index = library
    own(store, p, tmp_path)
    job = index.jobs(p)[0]
    index.cancel(p, job['id'])
    index.process_pending(limit=30)
    assert next(j for j in index.jobs(p) if j['id'] == job['id'])['state'] == 'cancelled'
    assert index.status(p)['semantic_ready'] is False


def test_offline_model_requires_verified_files_and_real_tokenizer(tmp_path):
    local = LocalEmbeddings(tmp_path)
    with pytest.raises(EmbeddingUnavailable):
        local.split_text('不会静默联网或上传资料')


def test_rrf_uses_ranks_not_incompatible_raw_scores():
    from creative_agent.knowledge_index import reciprocal_rank_fusion
    ranked = reciprocal_rank_fusion(['a', 'b'], ['b', 'c'])
    assert ranked[0][0] == 'b'
    assert dict(ranked)['b'] == pytest.approx(1 / 62 + 1 / 61)


def test_selected_material_survives_keyword_miss_and_revalidation_rejects_changes(library, tmp_path):
    store, p, _, index = library
    a = own(store, p, tmp_path)
    context = CreativeContextService(store, index=index)
    snapshot = context.build_context(p, 'new_script', query='写一个脚本', selected_asset_ids=[a['id']])
    assert snapshot['own_footage']
    assert context.revalidate_context(p, snapshot)['context_snapshot_id'] == snapshot['context_snapshot_id']
    store.put(p, 'asset', {**a, 'metadata': {**a['metadata'], 'reuse_allowed': False}}, a['id'])
    with pytest.raises(ValueError, match='依据已变化'):
        context.revalidate_context(p, snapshot)


def test_candidate_account_filter_applies_before_retrieval(library, tmp_path):
    store, p, _, index = library
    a = own(store, p, tmp_path)
    hidden = own(store, p, tmp_path, text='折叠收纳设计')
    service = CreativeContextService(store, index=index,
        candidate_filter=lambda project_id, kind, record: record['id'] != hidden['id'])
    result = service.build_context(p, 'new_script', query='折叠收纳')
    assert {r['asset_id'] for r in result['own_footage']} == {a['id']}


@pytest.mark.local_model
def test_actual_offline_fastembed_tokenizer_and_qdrant_persistence(tmp_path):
    """Representative delivery check: real weights and real local vectors, no HTTP."""
    from creative_agent.knowledge_index import _VectorStore
    model = Path(__file__).resolve().parents[1] / '.state/creative-loop-054/models/bge-small-zh-v1.5'
    local = LocalEmbeddings(model)
    text = '这是一段完整的商品说明，记录衣物收纳与使用场景。' * 80 + '最后这一段也必须进入索引'
    split = local.split_text(text)
    assert len(split) > 1 and '最后这一段也必须进入索引' in split[-1]
    assert all(len(local._get_tokenizer().encode(part).ids) <= 450 for part in split)
    vector = local.query('整理衣物')
    assert len(vector) == 512 and sum(x*x for x in vector) == pytest.approx(1, abs=0.01)
    store = Store(tmp_path / 'main.sqlite3')
    p = store.create_project('真实本地模型检查')['id']
    asset = own(store, p, tmp_path, text='把毛衣放进箱子')
    index = KnowledgeIndex(store, embeddings=local)
    index.process_pending(limit=20)
    assert index.status(p)['semantic_ready'] is True
    # This phrase has no Chinese-bigram overlap: success requires real vectors.
    assert not set(__import__('creative_agent.footage', fromlist=['tokens']).tokens('整理衣物')) & set(__import__('creative_agent.footage', fromlist=['tokens']).tokens('把毛衣放进箱子'))
    found = index.search(p, '整理衣物', pool='own')
    assert found[0]['asset_id'] == asset['id'] and found[0]['matching_basis'] == 'hybrid_local'
    path = index._vectors.path
    with _VectorStore._lock:
        _VectorStore._clients.pop(path).close()
    reopened = KnowledgeIndex(Store(store.path), embeddings=local)
    assert reopened.search(p, '整理衣物', pool='own')[0]['asset_id'] == asset['id']
    with _VectorStore._lock:
        _VectorStore._clients.pop(path).close()


def test_explicit_own_range_overrides_material_selection_without_widening_empty(library, tmp_path):
    store, p, _, index = library
    a = own(store, p, tmp_path)
    b = own(store, p, tmp_path, text='折叠收纳箱展示')
    context = CreativeContextService(store, index=index)
    independent = context.build_context(p, 'new_script', query='折叠收纳',
        selected_asset_ids=[a['id']], selected_own_asset_ids=[b['id']])
    assert {row['asset_id'] for row in independent['own_footage']} == {b['id']}
    assert independent['selection_snapshot']['selected_own_asset_ids'] == [b['id']]
    assert context.revalidate_context(p, independent)['context_snapshot_id'] == independent['context_snapshot_id']
    empty = context.build_context(p, 'new_script', query='折叠收纳',
        selected_asset_ids=[a['id']], selected_own_asset_ids=[])
    assert not empty['own_footage']
    assert empty['selection_snapshot']['selected_own_asset_ids'] == []


def test_explicit_own_range_is_scoped_and_works_without_keyword_overlap(library, tmp_path):
    store, p, other, index = library
    a = own(store, p, tmp_path)
    other_asset = own(store, other, tmp_path)
    context = CreativeContextService(store, index=index)
    selected = context.build_context(p, 'new_script', query='写一个脚本', selected_own_asset_ids=[a['id']])
    assert {row['asset_id'] for row in selected['own_footage']} == {a['id']}
    with pytest.raises(ValueError, match='选材列表无效'):
        context.build_context(p, 'new_script', selected_own_asset_ids='project')
    with pytest.raises(ValueError, match='记录不存在'):
        context.build_context(p, 'new_script', selected_own_asset_ids=[other_asset['id']])
