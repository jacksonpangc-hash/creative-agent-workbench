"""Scope changes must change the actual results, never just display chips."""
from test_agent import setup
import pytest
from creative_agent.contracts import AnalysisScope
from creative_agent.media import SearchService
from creative_agent.store import Store


def test_published_date_never_falls_back_to_import_date():
    scope = AnalysisScope(project_id='p', discovery=True, start_date='2026-10-01')
    asset = dict(id='a', project_id='p', source_kind='finished', created_at='2026-10-19T00:00:00', metadata={})
    assert not scope.allows(asset)


def test_import_and_statistics_dates_are_independent_of_publication(tmp_path):
    store = Store(tmp_path / 'db'); p = store.create_project('p')['id']
    a = store.put(p, 'asset', dict(title='a', source_kind='finished', metadata={'published_at':'2020-01-01'}))
    imported = AnalysisScope(project_id=p, discovery=True, date_basis='imported', start_date='2026-10-01')
    assert [x['id'] for x in SearchService(store).search(imported)] == [a['id']]
    stats = AnalysisScope(project_id=p, discovery=True, date_basis='statistics', start_date='2026-10-01', end_date='2026-10-31')
    assert SearchService(store).search(stats) == []
    store.put(p, 'metric', dict(asset_id=a['id'], date='2026-10-10', spend='10', revenue='20', currency='CNY'))
    assert [x['id'] for x in SearchService(store).search(stats)] == [a['id']]
    stats.start_date = __import__('datetime').date(2026,10,11)
    assert SearchService(store).search(stats) == []


def test_group_scope_is_resolved_at_tool_boundary_with_source_intersection(tmp_path):
    store = Store(tmp_path / 'db'); p = store.create_project('p')['id']; q = store.create_project('q')['id']
    a = store.put(p,'asset',dict(title='a',source_kind='finished',metadata={}))
    b = store.put(p,'asset',dict(title='b',source_kind='inspiration',metadata={}))
    g = store.put(p,'group',dict(title='g',asset_ids=[a['id'],b['id']]))
    foreign = store.put(q,'group',dict(title='private',asset_ids=[]))
    scope = AnalysisScope(project_id=p,group_ids=[g['id']],source_kinds=['finished'])
    assert [x['id'] for x in SearchService(store).search(scope)] == [a['id']]
    with pytest.raises(ValueError):
        SearchService(store).search(scope.model_copy(update={'group_ids':[foreign['id']]}))


def test_analysis_group_scope_freezes_group_union(setup):
    from creative_agent.agent import AnalysisAgent
    from test_agent import Answer
    store,p,media,a=setup
    g=store.put(p,'group',dict(title='g',asset_ids=[a['id']]))
    scope=AnalysisScope(project_id=p,group_ids=[g['id']])
    result=AnalysisAgent(store,media).analyze(scope,'directed','分析',Answer())
    assert result['scope']['asset_ids']==[a['id']]
    assert result['scope']['group_ids']==[]
