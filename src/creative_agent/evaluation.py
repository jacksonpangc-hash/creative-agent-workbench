"""Offline analysis of real, separately supplied blinded reviewer labels and cost receipts.

This module never obtains samples, calls vendors, or invents pilot outcomes.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from .contracts import decimal

THRESHOLDS = {
    "relevant": Decimal("0.85"),
    "ecommerce": Decimal("0.8"),
    "saveworthy": Decimal("0.5"),
    "traced": Decimal(1),
}
LABEL_FIELDS = {"blind_id", "reviewer_id", *THRESHOLDS}
SAMPLE_FIELDS = {"blind_id", "sample_id", "source", "category", "source_reference"}
COST_FIELDS = {"call_id", "source", "category", "currency", "actual_cost", "returned_count", "selected_count"}


def _csv(value, required, *, blinded=False):
    owned = not hasattr(value, "read")
    stream = Path(value).open(encoding="utf-8-sig", newline="") if owned else value  # noqa: SIM115 - caller-owned streams stay open
    try:
        reader = csv.DictReader(stream)
        names = reader.fieldnames or []
        if len(names) != len(set(names)) or not required <= set(names):
            raise ValueError("CSV 缺少必要字段或含重复列")
        if blinded and set(names) & {"source", "provider", "sample_id", "source_reference"}:
            raise ValueError("评审标签表含来源身份，无法按盲评表处理")
        rows = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("CSV 行与表头列数不一致")
            rows.append({key: value.strip() for key, value in row.items()})
        return rows
    finally:
        if owned:
            stream.close()


def _label(value):
    if value == "":
        return None
    if value not in {"0", "1"}:
        raise ValueError("评审标签只能为 0、1 或空缺")
    return int(value)


def _number(value, *, integer=False):
    if value == "":
        return None
    parsed = decimal(value)
    if parsed < 0 or (integer and parsed != parsed.to_integral_value()):
        raise ValueError("成本与计数必须为非负有效数字")
    return int(parsed) if integer else parsed


def _metrics(samples, votes, min_reviewers):
    result = {}
    for name, threshold in THRESHOLDS.items():
        positive = evaluated = 0
        for sample in samples:
            reviewers = votes.get(sample["blind_id"], [])
            values = [review[name] for review in reviewers]
            if len(values) < min_reviewers or any(v is None for v in values) or len(set(values)) != 1:
                continue
            if name == "traced" and values[0] == 1 and not sample["source_reference"]:
                continue
            evaluated += 1
            positive += values[0]
        total = len(samples)
        rate = Decimal(positive) / evaluated if evaluated else None
        result[name] = {
            "positive": positive,
            "evaluated": evaluated,
            "missing": total - evaluated,
            "total": total,
            "rate": str(rate) if rate is not None else None,
            "threshold": str(threshold),
            "passed": bool(total and evaluated == total and rate >= threshold),
        }
    return result


def _cost_currency(rows):
    known = [r["amount"] for r in rows if r["amount"] is not None]
    unknown = len(rows) - len(known)
    partial = sum(known, Decimal(0))
    total = partial if not unknown else None

    def denominator(field):
        values = [r[field] for r in rows]
        return None if any(v is None for v in values) else sum(values)

    returned, selected = denominator("returned"), denominator("selected")
    return {
        "recorded_calls": len(rows),
        "known_calls": len(known),
        "unknown_calls": unknown,
        "known_actual_cost": str(partial),
        "total_actual_cost": str(total) if total is not None else None,
        "returned_count": returned,
        "selected_count": selected,
        "cost_per_returned": str(total / returned) if total is not None and returned else None,
        "cost_per_selected": str(total / selected) if total is not None and selected else None,
    }


def _cost_report(rows):
    if not rows:
        return {
            "status": "unknown",
            "recorded_calls": 0,
            "unknown_calls": None,
            "total_actual_cost": None,
            "by_currency": {},
        }
    groups = defaultdict(list)
    for row in rows:
        groups[row["currency"]].append(row)
    grouped = {currency: _cost_currency(values) for currency, values in sorted(groups.items())}
    all_known = all(r["amount"] is not None for r in rows)
    overall = next(iter(grouped.values()))["total_actual_cost"] if len(grouped) == 1 and all_known else None
    return {
        "status": "complete"
        if all_known and len(grouped) == 1
        else "mixed_currency"
        if all_known
        else "partial",
        "recorded_calls": len(rows),
        "unknown_calls": sum(r["amount"] is None for r in rows),
        "total_actual_cost": overall,
        "by_currency": grouped,
    }


def evaluate_csv(labels_csv, samples_csv, *, costs_csv=None, min_reviewers=2):
    """Strict per-sample consensus; missing/disputed data never makes a gate pass."""
    if not isinstance(min_reviewers, int) or isinstance(min_reviewers, bool) or min_reviewers < 1:
        raise ValueError("需要有效的最低评审人数")
    samples = _csv(samples_csv, SAMPLE_FIELDS)
    if not samples or any(not s[k] for s in samples for k in ("blind_id", "sample_id", "source", "category")):
        raise ValueError("样本映射必须非空且包含盲号、来源与品类")
    if len({s["blind_id"] for s in samples}) != len(samples) or len({s["sample_id"] for s in samples}) != len(
        samples
    ):
        raise ValueError("盲号或实际样本重复，不能重复计入分母")
    index = {s["blind_id"]: s for s in samples}
    raw_labels = _csv(labels_csv, LABEL_FIELDS, blinded=True)
    votes = defaultdict(list)
    seen = set()
    for row in raw_labels:
        identity = (row["blind_id"], row["reviewer_id"])
        if row["blind_id"] not in index or not row["reviewer_id"] or identity in seen:
            raise ValueError("标签引用未知样本、无评审人或重复评审")
        seen.add(identity)
        votes[row["blind_id"]].append({name: _label(row[name]) for name in THRESHOLDS})
    costs = []
    seen_calls = set()
    if costs_csv is not None:
        for row in _csv(costs_csv, COST_FIELDS):
            identity = (row["source"], row["call_id"])
            if (
                not all(row[k] for k in ("source", "category", "call_id"))
                or row["currency"] not in {"CNY", "USD"}
                or identity in seen_calls
            ):
                raise ValueError("费用账单币种、来源或唯一调用标识无效")
            seen_calls.add(identity)
            costs.append(
                {
                    "source": row["source"],
                    "category": row["category"],
                    "currency": row["currency"],
                    "amount": _number(row["actual_cost"]),
                    "returned": _number(row["returned_count"], integer=True),
                    "selected": _number(row["selected_count"], integer=True),
                }
            )
    if any(
        row["returned"] is not None and row["selected"] is not None and row["selected"] > row["returned"]
        for row in costs
    ):
        raise ValueError("选择数量不能超过实际返回数量")
    overall = _metrics(samples, votes, min_reviewers)

    def groups(field):
        return {
            value: {
                "sample_count": len(group),
                "metrics": _metrics(group, votes, min_reviewers),
                "costs": _cost_report([r for r in costs if r[field] == value]),
            }
            for value in sorted({s[field] for s in samples})
            for group in [[s for s in samples if s[field] == value]]
        }

    strata = {
        source + " / " + category: {
            "sample_count": len(group),
            "metrics": _metrics(group, votes, min_reviewers),
            "costs": _cost_report([r for r in costs if r["source"] == source and r["category"] == category]),
        }
        for source, category in sorted({(s["source"], s["category"]) for s in samples})
        for group in [[s for s in samples if s["source"] == source and s["category"] == category]]
    }
    return {
        "evidence_level": "provided_reviewer_labels",
        "live_validation": False,
        "blinding": "source_identity_excluded_from_label_columns; procedure_requires_external_verification",
        "min_reviewers": min_reviewers,
        "sample_count": len(samples),
        "review_count": len(raw_labels),
        "overall": overall,
        "thresholds_passed": all(m["passed"] for m in overall.values()),
        "all_source_categories_passed": all(
            m["passed"] for g in strata.values() for m in g["metrics"].values()
        ),
        "by_source": groups("source"),
        "by_category": groups("category"),
        "by_source_category": strata,
        "costs": _cost_report(costs),
    }


def write_source_cost_csv(store, project_id, output):
    """Export only observed attempts/receipt costs; reserve/quote is never actual cost."""
    store.project(project_id)
    fields = [
        "call_id",
        "source",
        "category",
        "currency",
        "actual_cost",
        "returned_count",
        "selected_count",
        "job_id",
        "call_index",
        "reserved_quote",
        "state",
    ]
    owned = not hasattr(output, "write")
    stream = Path(output).open("x", encoding="utf-8", newline="") if owned else output  # noqa: SIM115 - caller-owned streams stay open
    try:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        jobs = [j for j in store.jobs(project_id) if j["kind"] == "industry_search"]
        jobs.extend(j for j in store.list(project_id, "migration_job") if j.get("kind") == "industry_search")
        refs = store.list(project_id, "industry_reference")
        for job in jobs:
            events = job.get("events")
            if events is None:
                events = []
                cursor = 0
                while True:
                    batch = store.events(project_id, job["id"], after=cursor)
                    if not batch:
                        break
                    events.extend(batch)
                    cursor = batch[-1]["id"]
            calls = []
            for event in events:
                if event["type"] == "industry_call":
                    calls.append({"index": event["data"]["index"], "receipt": None})
                elif event["type"] == "industry_rows" and calls:
                    calls[-1]["receipt"] = event["data"]
            for call in calls:
                receipt = call["receipt"] or {}
                writer.writerow(
                    {
                        "call_id": receipt.get("provider_request_id") or f"local:{job['id']}:{call['index']}",
                        "source": job["payload"]["connection_snapshot"]["provider"],
                        "category": job["payload"]["plan"].get("category") or "unspecified",
                        "currency": job["quote"]["currency"],
                        "actual_cost": receipt.get("billing_amount")
                        if receipt.get("billing_amount") is not None
                        else "",
                        "returned_count": receipt.get("count", ""),
                        "selected_count": sum(r["job_id"] == job["id"] for r in refs)
                        if len(calls) == 1
                        else "",
                        "job_id": job["id"],
                        "call_index": call["index"],
                        "reserved_quote": job["quote"]["amount"] if call["index"] == 1 else "",
                        "state": job["state"],
                    }
                )
    finally:
        if owned:
            stream.close()
