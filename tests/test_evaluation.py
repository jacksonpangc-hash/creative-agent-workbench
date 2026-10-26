"""Blinded review evaluation uses only hand-labelled CSV; no invented pilot."""

import csv
import importlib.util
import io

import pytest


def evaluation():
    assert importlib.util.find_spec("creative_agent.evaluation"), "Blinded evaluation harness is missing"
    from creative_agent import evaluation as module

    return module


def csv_text(fields, rows):
    data = io.StringIO()
    writer = csv.DictWriter(data, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return io.StringIO(data.getvalue())


def samples():
    return csv_text(
        ["blind_id", "sample_id", "source", "category", "source_reference"],
        [
            {
                "blind_id": "b1",
                "sample_id": "s1",
                "source": "cmm",
                "category": "beauty",
                "source_reference": "source-1",
            },
            {
                "blind_id": "b2",
                "sample_id": "s2",
                "source": "cmm",
                "category": "beauty",
                "source_reference": "source-2",
            },
            {
                "blind_id": "b3",
                "sample_id": "s3",
                "source": "datax",
                "category": "food",
                "source_reference": "source-3",
            },
            {
                "blind_id": "b4",
                "sample_id": "s4",
                "source": "datax",
                "category": "food",
                "source_reference": "source-4",
            },
        ],
    )


def labels(changes=None):
    rows = []
    for i in range(1, 5):
        for reviewer in ["r1", "r2"]:
            row = {
                "blind_id": f"b{i}",
                "reviewer_id": reviewer,
                "relevant": "1",
                "ecommerce": "1",
                "saveworthy": "1" if i <= 2 else "0",
                "traced": "1",
            }
            if changes:
                row.update(changes(i, reviewer))
            rows.append(row)
    return csv_text(["blind_id", "reviewer_id", "relevant", "ecommerce", "saveworthy", "traced"], rows)


def test_blinded_review_uses_unique_sample_denominators_and_literal_thresholds():
    report = evaluation().evaluate_csv(labels(), samples())
    assert report["sample_count"] == 4 and report["review_count"] == 8
    assert report["overall"]["saveworthy"] == {
        "positive": 2,
        "evaluated": 4,
        "missing": 0,
        "total": 4,
        "rate": "0.5",
        "threshold": "0.5",
        "passed": True,
    }
    assert (
        report["overall"]["relevant"]["threshold"] == "0.85"
        and report["overall"]["ecommerce"]["threshold"] == "0.8"
    )
    assert report["overall"]["traced"]["threshold"] == "1"
    assert report["thresholds_passed"] is True and report["evidence_level"] == "provided_reviewer_labels"
    assert report["costs"]["total_actual_cost"] is None and report["costs"]["status"] == "unknown"
    assert report["by_source"]["cmm"]["metrics"]["saveworthy"]["positive"] == 2
    assert report["by_category"]["food"]["metrics"]["saveworthy"]["positive"] == 0


def test_missing_or_disputed_labels_cannot_inflate_acceptance():
    report = evaluation().evaluate_csv(
        labels(
            lambda i, r: (
                {"relevant": ""} if i == 4 and r == "r2" else {"traced": "0"} if i == 3 and r == "r2" else {}
            )
        ),
        samples(),
    )
    assert report["overall"]["relevant"]["evaluated"] == 3 and report["overall"]["relevant"]["missing"] == 1
    assert report["overall"]["relevant"]["passed"] is False
    assert report["overall"]["traced"]["missing"] == 1 and report["thresholds_passed"] is False


def test_known_zero_and_unknown_actual_costs_have_separate_denominators():
    costs = csv_text(
        ["call_id", "source", "category", "currency", "actual_cost", "returned_count", "selected_count"],
        [
            {
                "call_id": "c1",
                "source": "cmm",
                "category": "beauty",
                "currency": "CNY",
                "actual_cost": "0",
                "returned_count": "2",
                "selected_count": "2",
            },
            {
                "call_id": "c2",
                "source": "datax",
                "category": "food",
                "currency": "USD",
                "actual_cost": "0.004",
                "returned_count": "2",
                "selected_count": "0",
            },
            {
                "call_id": "c3",
                "source": "cmm",
                "category": "beauty",
                "currency": "CNY",
                "actual_cost": "",
                "returned_count": "",
                "selected_count": "",
            },
        ],
    )
    report = evaluation().evaluate_csv(labels(), samples(), costs_csv=costs)
    yuan = report["costs"]["by_currency"]["CNY"]
    dollars = report["costs"]["by_currency"]["USD"]
    assert (
        yuan["known_actual_cost"] == "0" and yuan["unknown_calls"] == 1 and yuan["total_actual_cost"] is None
    )
    assert yuan["returned_count"] is None and yuan["cost_per_returned"] is None
    assert dollars["total_actual_cost"] == "0.004" and dollars["cost_per_returned"] == "0.002"
    assert dollars["cost_per_selected"] is None
    assert report["costs"]["total_actual_cost"] is None  # currencies cannot be added


def test_duplicate_reviewers_unknown_samples_unblinded_labels_and_bad_costs_rejected():
    module = evaluation()
    bad = csv_text(
        ["blind_id", "reviewer_id", "relevant", "ecommerce", "saveworthy", "traced"],
        [
            {
                "blind_id": "b1",
                "reviewer_id": "r1",
                "relevant": "1",
                "ecommerce": "1",
                "saveworthy": "0",
                "traced": "1",
            },
            {
                "blind_id": "b1",
                "reviewer_id": "r1",
                "relevant": "1",
                "ecommerce": "1",
                "saveworthy": "1",
                "traced": "1",
            },
        ],
    )
    with pytest.raises(ValueError):
        module.evaluate_csv(bad, samples())
    bad = csv_text(["blind_id", "reviewer_id", "source", "relevant", "ecommerce", "saveworthy", "traced"], [])
    with pytest.raises(ValueError):
        module.evaluate_csv(bad, samples())
    with pytest.raises(ValueError):
        module.evaluate_csv(labels(lambda i, r: {"relevant": "0.9"}), samples())
    badcost = csv_text(
        ["call_id", "source", "category", "currency", "actual_cost", "returned_count", "selected_count"],
        [
            {
                "call_id": "c",
                "source": "cmm",
                "category": "beauty",
                "currency": "CNY",
                "actual_cost": "-1",
                "returned_count": "2",
                "selected_count": "1",
            }
        ],
    )
    with pytest.raises(ValueError):
        module.evaluate_csv(labels(), samples(), costs_csv=badcost)


def test_cost_csv_export_reads_actual_source_events_and_never_uses_quotes(tmp_path):
    from creative_agent.contracts import CostQuote
    from creative_agent.store import Store

    s = Store(tmp_path / "db")
    p = s.create_project("p", "10")["id"]
    job = s.create_job(
        p,
        "industry_search",
        {"plan": {"category": "beauty"}, "connection_snapshot": {"provider": "cmm"}},
        CostQuote(amount="5", basis="cap"),
        "key",
    )
    s.event(p, job["id"], "industry_call", {"index": 1})
    s.event(
        p, job["id"], "industry_rows", {"count": 7, "billing_amount": None, "provider_request_id": "vendor-1"}
    )
    output = io.StringIO()
    evaluation().write_source_cost_csv(s, p, output)
    row = next(iter(csv.DictReader(io.StringIO(output.getvalue()))))
    assert row["actual_cost"] == "" and row["reserved_quote"] == "5"
    assert row["returned_count"] == "7" and row["call_id"] == "vendor-1"


def test_cost_selection_denominator_cannot_exceed_real_returned_count():
    costs = csv_text(
        ["call_id", "source", "category", "currency", "actual_cost", "returned_count", "selected_count"],
        [
            {
                "call_id": "c1",
                "source": "cmm",
                "category": "beauty",
                "currency": "CNY",
                "actual_cost": "1",
                "returned_count": "1",
                "selected_count": "3",
            }
        ],
    )
    with pytest.raises(ValueError):
        evaluation().evaluate_csv(labels(), samples(), costs_csv=costs)


def test_trace_labels_without_source_evidence_stay_unresolved():
    sample = csv_text(
        ["blind_id", "sample_id", "source", "category", "source_reference"],
        [
            {
                "blind_id": "b1",
                "sample_id": "s1",
                "source": "cmm",
                "category": "beauty",
                "source_reference": "",
            }
        ],
    )
    lab = csv_text(
        ["blind_id", "reviewer_id", "relevant", "ecommerce", "saveworthy", "traced"],
        [
            {
                "blind_id": "b1",
                "reviewer_id": r,
                "relevant": "1",
                "ecommerce": "1",
                "saveworthy": "1",
                "traced": "1",
            }
            for r in ["r1", "r2"]
        ],
    )
    report = evaluation().evaluate_csv(lab, sample)
    assert report["overall"]["traced"]["missing"] == 1 and report["overall"]["traced"]["passed"] is False


def test_runnable_evaluation_cli_writes_label_report_without_opening_a_store(tmp_path, capsys):
    assert importlib.util.find_spec("creative_agent.tools_cli"), "Runnable evaluation CLI is missing"
    from creative_agent.tools_cli import main

    sample_file = tmp_path / "samples.csv"
    sample_file.write_text(samples().getvalue())
    label_file = tmp_path / "labels.csv"
    label_file.write_text(labels().getvalue())
    assert main(["evaluate", "--samples", str(sample_file), "--labels", str(label_file)]) == 0
    import json

    report = json.loads(capsys.readouterr().out)
    assert report["sample_count"] == 4 and report["live_validation"] is False
