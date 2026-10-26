"""Catch project leaks, duplicate charges and recovery that silently resubmits paid work."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from creative_agent.contracts import CostQuote
from creative_agent.store import BudgetError, Conflict, NotFound, Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "app.sqlite")


def test_projects_persist_and_objects_cannot_cross_project(store):
    a = store.create_project("A", budget="10")
    b = store.create_project("B")
    obj = store.put(a["id"], "asset", {"title": "private"})
    with pytest.raises(NotFound):
        store.get(b["id"], "asset", obj["id"])
    with pytest.raises(NotFound):
        store.put(b["id"], "asset", {"title": "overwritten"}, obj["id"])
    reopened = Store(store.path)
    assert reopened.get(a["id"], "asset", obj["id"])["title"] == "private"


def test_credentials_are_not_allowed_in_records_or_project_exports(store):
    project = store.create_project("A")
    with pytest.raises(ValueError, match="凭证"):
        store.put(project["id"], "binding", {"nested": {"api_key": "test-value"}})
    store.put(project["id"], "binding", {"provider": "kimi", "secret_ref": "internal-id", "has_key": True})
    exported = store.export_project(project["id"])
    binding = exported["records"]["binding"][0]
    assert "secret_ref" not in binding
    assert binding["has_key"] is False


def test_budget_reservation_is_atomic_under_concurrent_jobs(store):
    p = store.create_project("A", budget="3", currency="CNY")
    def attempt(i):
        try:
            return store.create_job(p["id"], "analysis", {"i": i}, CostQuote(amount="1", basis="test"), str(i))
        except BudgetError:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        accepted = [x for x in pool.map(attempt, range(12)) if x]
    assert len(accepted) == 3
    assert store.budget(p["id"])["reserved"] == "3"
    assert store.budget(p["id"])["available"] == "0"


def test_same_idempotency_key_does_not_reserve_or_submit_twice(store):
    p = store.create_project("A", budget="10")
    quote = CostQuote(amount="2", basis="test")
    one = store.create_job(p["id"], "analysis", {"q": "one"}, quote, "same")
    two = store.create_job(p["id"], "analysis", {"q": "one"}, quote, "same")
    assert one["id"] == two["id"]
    assert store.budget(p["id"])["reserved"] == "2"
    with pytest.raises(Conflict):
        store.create_job(p["id"], "analysis", {"q": "changed"}, quote, "same")


def test_optional_budget_allows_work_but_enabled_limit_keeps_currency_guard(store):
    p = store.create_project("A")
    assert store.create_job(p["id"], "analysis", {}, CostQuote(amount="1", basis="test"), "first")['state']=='queued'
    store.update_project(p["id"], budget="10")
    with pytest.raises(BudgetError):
        store.create_job(p["id"], "analysis", {}, CostQuote(amount="1", currency="USD", basis="test"), "second")


def test_ambiguous_submission_is_held_for_attention_after_restart(store):
    p = store.create_project("A", budget="10")
    j = store.create_job(p["id"], "analysis", {}, CostQuote(amount="2", basis="test"), "one")
    store.transition(p["id"], j["id"], "running")
    store.transition(p["id"], j["id"], "submitting")
    Store(store.path).recover()
    result = store.job(p["id"], j["id"])
    assert result["state"] == "needs_attention"
    assert store.budget(p["id"])["reserved"] == "2"


def test_submitted_task_id_survives_restart_and_cancel_is_not_falsely_final(store):
    p = store.create_project("A", budget="10")
    j = store.create_job(p["id"], "generation", {}, CostQuote(amount="2", basis="test"), "one")
    store.transition(p["id"], j["id"], "running")
    store.transition(p["id"], j["id"], "submitting")
    store.transition(p["id"], j["id"], "submitted", provider_request_id="remote-123")
    Store(store.path).recover()
    assert store.job(p["id"], j["id"])["provider_request_id"] == "remote-123"
    assert store.cancel(p["id"], j["id"])["state"] == "cancelling"
    assert store.budget(p["id"])["reserved"] == "2"


def test_cancelling_unsubmitted_work_releases_reservation(store):
    p = store.create_project("A", budget="10")
    j = store.create_job(p["id"], "analysis", {}, CostQuote(amount="2", basis="test"), "one")
    assert store.cancel(p["id"], j["id"])["state"] == "cancelled"
    assert store.budget(p["id"])["available"] == "10"


def test_unknown_actual_cost_is_held_not_invented_as_zero(store):
    p = store.create_project("A", budget="10")
    j = store.create_job(p["id"], "analysis", {}, CostQuote(amount="2", basis="test"), "one")
    store.transition(p["id"], j["id"], "running")
    store.transition(p["id"], j["id"], "submitting")
    store.finish(p["id"], j["id"], result={"answer": "done"})
    assert store.budget(p["id"])["reserved"] == "2"
    store.settle(p["id"], j["id"], "1.25")
    assert Decimal(store.budget(p["id"])["available"]) == Decimal("8.75")


def test_events_are_scoped_and_monotonic(store):
    p = store.create_project("A")
    b = store.create_project("B")
    j = store.create_job(p["id"], "local", {}, CostQuote(amount="0", basis="local"), "one")
    store.event(p["id"], j["id"], "progress", {"processed": 1})
    events = store.events(p["id"], j["id"])
    assert events[-1]["data"] == {"processed": 1}
    assert len(store.events(p["id"], j["id"], after=events[-1]["id"])) == 0
    with pytest.raises(NotFound):
        store.events(b["id"], j["id"])



def test_archived_migration_events_remain_readable_but_not_executable(tmp_path):
    store=Store(tmp_path/'archive.db');p=store.create_project('p')['id']
    store.restore_record(p,'migration_job',{'kind':'analysis','state':'archived','archival':True,'replay_allowed':False,'events':[{'id':1,'type':'state','data':{'state':'submitting'},'created_at':'2026-10-19'}]},'historical')
    assert store.events(p,'historical')[0]['data']['state']=='submitting'
    assert store.events(p,'historical',after=1)==[]
