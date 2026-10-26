"""Catch false modality routing, widened scopes and fabricated aggregate metrics."""
from decimal import Decimal

import pytest
from pydantic import ValidationError

from creative_agent.contracts import (
    AnalysisScope, EvidenceRef, GenerationRequest, Metric, aggregate_metrics,
    require_capability, provider_catalog,
)


def test_image_model_cannot_be_used_as_native_video_generator():
    catalog = provider_catalog()
    assert {p["id"] for p in catalog} == {
        "openai", "google", "anthropic", "alibaba", "volcengine", "zhipu", "minimax", "kimi", "deepseek", "kling"
    }
    require_capability("deepseek", "image_input")
    with pytest.raises(ValueError, match="不支持"):
        require_capability("deepseek", "video_input")
    with pytest.raises(ValueError, match="不支持"):
        require_capability("openai", "video_generation")
    require_capability("minimax", "video_generation")


def test_scope_rejects_cross_project_sources_and_unselected_assets():
    scope = AnalysisScope(project_id="a", source_kinds=["own"], asset_ids=["one"])
    assert scope.allows({"project_id": "a", "source_kind": "own", "id": "one"})
    assert not scope.allows({"project_id": "b", "source_kind": "own", "id": "one"})
    assert not scope.allows({"project_id": "a", "source_kind": "industry", "id": "one"})
    assert not scope.allows({"project_id": "a", "source_kind": "own", "id": "two"})


def test_empty_selected_scope_is_not_an_implicit_all_sources_query():
    scope = AnalysisScope(project_id="a", source_kinds=["own"], asset_ids=[])
    assert not scope.allows({"project_id": "a", "source_kind": "own", "id": "one"})
    exploratory = AnalysisScope(project_id="a", source_kinds=["own"], discovery=True)
    assert exploratory.allows({"project_id": "a", "source_kind": "own", "id": "one"})


def test_analysis_capacity_and_time_span_validation():
    AnalysisScope(project_id="a", asset_ids=[str(i) for i in range(200)])
    with pytest.raises(ValidationError):
        AnalysisScope(project_id="a", asset_ids=[str(i) for i in range(201)])
    with pytest.raises(ValidationError):
        AnalysisScope(project_id="a", start_date="2026-10-19", end_date="2026-10-01")
    with pytest.raises(ValidationError):
        EvidenceRef(asset_id="one", start_ms=500, end_ms=100)


@pytest.mark.parametrize("field,value", [("duration", 16), ("batch", 9), ("batch", 0)])
def test_generation_rejects_out_of_bounds_parameters(field, value):
    with pytest.raises(ValidationError):
        GenerationRequest(project_id="a", reference_asset_id="one", **{field: value})


def test_generation_preserves_six_image_references_and_two_routes():
    req = GenerationRequest(project_id="a", reference_asset_id="one", reference_image_ids=list("abcdef"), batch=8)
    assert len(req.reference_image_ids) == 6
    assert req.mode == "faithful"
    with pytest.raises(ValidationError):
        GenerationRequest(project_id="a", reference_asset_id="one", reference_image_ids=list("abcdefg"))


def test_missing_range_and_nonfinite_metrics_are_never_exact_numbers():
    assert Metric(kind="missing").display() == "缺失"
    assert Metric(kind="range", lower="100000", upper="250000", unit="CNY").display() == "100000–250000 CNY"
    with pytest.raises(ValidationError):
        Metric(kind="exact")
    with pytest.raises(ValidationError):
        Metric(kind="range", lower="20", upper="10")
    with pytest.raises(ValidationError):
        Metric(kind="exact", value="NaN")


def test_review_ratios_use_sum_of_numerators_and_exclude_missing_denominators():
    result = aggregate_metrics([
        {"spend": "100", "revenue": "200", "impressions": 1000, "clicks": 100},
        {"spend": "900", "revenue": "900", "impressions": 9000, "clicks": 100},
        {"spend": None, "revenue": None, "impressions": None, "clicks": None},
    ])
    assert Decimal(result["roas"]["value"]) == Decimal("1.1")
    assert Decimal(result["ctr"]["value"]) == Decimal("0.02")
    assert result["missing_rows"] == 1
    assert aggregate_metrics([{"spend": "0", "revenue": "10"}])["roas"]["kind"] == "missing"


def test_mixed_currency_revenue_only_rows_cannot_be_relabelled_as_project_currency():
    with pytest.raises(ValueError,match='币种'):
        aggregate_metrics([{'spend':'10','revenue':'20','currency':'CNY'},{'revenue':'100','currency':'USD'}])

def test_model_address_cannot_store_embedded_credentials_or_query():
    from creative_agent.contracts import ModelBinding
    for url in ('https://user:key@example.org/api','https://example.org/api?api_key=secret'):
        with pytest.raises(ValidationError):
            ModelBinding(provider='deepseek',label='invalid',model_id='x',role='strategy',base_url=url)
