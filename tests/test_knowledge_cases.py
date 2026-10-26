import hashlib
import json
import pytest


def package(tmp_path, review='pending', license='AGPL-3.0-only'):
    rows=[{'id':'method-1','type':'method','title':'先演示再解释','tags':['演示'],
           'content':'先展示一个可观察的动作，再解释已确认卖点。','review_state':review,
           'reviewed_by':'业务审阅人' if review=='approved' else None,
           'reviewed_at':'2026-10-25' if review=='approved' else None}]
    raw=json.dumps(rows,ensure_ascii=False).encode();(tmp_path/'cases.json').write_bytes(raw)
    (tmp_path/'manifest.json').write_text(json.dumps({'version':1,'license':license,'content_file':'cases.json',
        'content_sha256':hashlib.sha256(raw).hexdigest(),'provenance':'original_ai_assisted','not_performance_evidence':True}))


def test_unreviewed_methodology_cannot_become_customer_facts(tmp_path):
    from creative_agent.knowledge_cases import load_cases, methodology_context
    package(tmp_path)
    assert load_cases(tmp_path)==[]
    assert len(load_cases(tmp_path,include_drafts=True))==1
    package(tmp_path,review='approved')
    context=methodology_context(tmp_path,'怎么演示')
    assert context['not_product_facts'] is True and context['not_performance_evidence'] is True
    assert len(context['methods'])==1 and '先展示' in context['methods'][0]['content']


def test_noncommercial_content_and_tampered_package_are_rejected(tmp_path):
    from creative_agent.knowledge_cases import load_cases
    package(tmp_path,review='approved',license='CC-BY-NC-4.0')
    with pytest.raises(ValueError,match='许可'):load_cases(tmp_path)
    package(tmp_path,review='approved')
    (tmp_path/'cases.json').write_text('[]')
    with pytest.raises(ValueError,match='校验'):load_cases(tmp_path)


def test_package_cannot_escape_its_directory(tmp_path):
    from creative_agent.knowledge_cases import load_cases
    package(tmp_path)
    p=tmp_path/'manifest.json';manifest=json.loads(p.read_text());manifest['content_file']='../outside.json';p.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):load_cases(tmp_path)


def test_broken_optional_case_pack_does_not_block_local_creative_context(tmp_path):
    from creative_agent.knowledge_cases import methodology_context
    package(tmp_path,review='approved')
    (tmp_path/'cases.json').write_text('broken')
    context=methodology_context(tmp_path,'演示')
    assert context['methods']==[] and context['status']=='unavailable'
    assert context['not_product_facts'] is True


def test_bundled_content_is_complete_but_pending_business_review():
    from creative_agent.knowledge_cases import load_cases
    rows=load_cases(include_drafts=True)
    assert len([r for r in rows if r['type']=='method'])==30
    assert len([r for r in rows if r['type']=='example' and r['synthetic']])==12
    assert load_cases()==[]


def test_commercial_case_gate_requires_actual_review():
    from creative_agent.knowledge_cases import require_reviewed_cases
    with pytest.raises(ValueError,match='审核'):require_reviewed_cases()
