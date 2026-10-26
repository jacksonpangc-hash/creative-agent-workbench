"""Citable product statements from a saved, server-frozen facts version.

These records are never footage references or proof that a model watched media.
"""
from collections import Counter
from copy import deepcopy
from datetime import date, datetime
import hashlib
import json


def model_product_facts(facts):
    """The model receives the frozen version, never other versions in its audit trail."""
    return {key: deepcopy(value) for key, value in facts.items() if key != 'revisions'}


def _valid_claim(claim):
    if (claim.get('confirmed') is not True or claim.get('withdrawn')
            or any(not isinstance(claim.get(k), str) or not claim[k].strip()
                   for k in ('fact_id', 'statement', 'source'))):
        return False
    if claim.get('valid_until') is not None:
        try:
            if date.fromisoformat(str(claim['valid_until'])) < date.today():
                return False
        except ValueError:
            return False
    return True


def build_product_evidence(facts: dict, project_id: str) -> list[dict]:
    """Build deterministic IDs; caller must supply the saved server snapshot only."""
    if (facts.get('project_id') != project_id or not project_id
            or type(facts.get('version')) is not int or facts['version'] < 1
            or not facts.get('product_id') or not isinstance(facts.get('claims'), list)):
        return []
    try:
        timestamp = datetime.fromisoformat(facts.get('updated_at', ''))
        if timestamp.tzinfo is None:
            return []
    except (TypeError, ValueError):
        return []
    counts = Counter(c.get('fact_id') for c in facts['claims'] if isinstance(c, dict) and isinstance(c.get('fact_id'), str))
    records = []
    for claim in facts['claims']:
        if not isinstance(claim, dict) or not _valid_claim(claim) or counts[claim['fact_id']] != 1:
            continue
        record = dict(evidence_kind='product_fact', title='商品资料', provenance='confirmed_product_facts',
            project_id=project_id, product_id=facts['product_id'], sku_id=facts.get('sku_id', ''),
            facts_version=facts['version'], snapshot_at=facts['updated_at'], fact_id=claim['fact_id'],
            statement=claim['statement'], source=claim['source'], valid_until=claim.get('valid_until'), confirmed=True)
        identity = json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        record['id'] = 'product-evidence-' + hashlib.sha256(identity.encode()).hexdigest()
        records.append(record)
    return records


def available_product_evidence(records, current, project_id, *, product_id='', sku_id=''):
    """Retain frozen identities for unchanged claims; explain current exclusions.

    An unrelated version edit does not invalidate a statement. Changed statements,
    sources, expiry or product/SKU identities require a new analysis snapshot.
    """
    counts = Counter(c.get('fact_id') for c in current.get('claims', []) if isinstance(c, dict) and isinstance(c.get('fact_id'), str))
    claims = {c['fact_id']: c for c in current.get('claims', []) if isinstance(c, dict) and isinstance(c.get('fact_id'), str)}
    available, unavailable = [], []
    for record in records:
        claim = claims.get(record.get('fact_id'))
        if ((product_id and record.get('product_id') != product_id)
                or (sku_id and record.get('sku_id') != sku_id)):
            reason = '商品资料不适用于本轮指定商品或SKU'
        elif (record.get('project_id') != project_id or current.get('project_id') != project_id
                or any(record.get(k, '') != current.get(k, '') for k in ('product_id', 'sku_id'))):
            reason = '当前商品或SKU身份已变更；原资料仅作历史记录'
        elif not claim or counts[claim['fact_id']] != 1 or not _valid_claim(claim):
            reason = '当前商品说法已撤回、过期或缺少已确认依据'
        elif any(record.get(k) != claim.get(k) for k in ('statement', 'source', 'valid_until')):
            reason = '当前商品说法或依据已变更，需要重新核实'
        elif not _valid_claim(record):
            reason = '原商品资料已过期或不可用'
        else:
            available.append(record)
            continue
        unavailable.append(dict(id=record['id'], fact_id=record.get('fact_id'), reason=reason))
    return available, unavailable
