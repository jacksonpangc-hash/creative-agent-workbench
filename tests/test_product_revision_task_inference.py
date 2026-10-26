import pytest
from creative_agent.creative_tasks import TaskSpec

@pytest.mark.parametrize('question', ['拆解开头，不要写脚本','只分析开头，先别给三个方向','不要写分镜稿，比较卖点'])
def test_negated_deliverable_is_not_an_requested_task(question):
    assert TaskSpec.infer(question) is None

@pytest.mark.parametrize('question', ['先别给三个方向，写一条30秒脚本','不要写脚本，给两个创意方案','不要三个方向，我要两条脚本'])
def test_only_positive_deliverable_controls_type_and_count(question):
    spec=TaskSpec.infer(question)
    if '创意方案' in question:
        assert (spec.output_type,spec.count)==('directions',2)
    else:
        assert (spec.output_type,spec.count)==('scripts',2 if '两条' in question else 1)
        if '30秒' in question:assert spec.target_duration_ms==30000

@pytest.mark.parametrize('question,count,duration', [
    ('写一条不需要补拍的30秒脚本',1,30000),
    ('写一条不用额外素材的脚本',1,None),
    ('写两条不需要补拍的30秒脚本',2,30000),
    ('创作三个不用额外素材的20秒分镜稿',3,20000),
    ('用现有素材写一条脚本，不需要补拍',1,None),
])
def test_negated_production_modifier_preserves_positive_deliverable(question,count,duration):
    spec=TaskSpec.infer(question)
    assert spec is not None
    assert (spec.output_type,spec.count,spec.target_duration_ms)==('scripts',count,duration)
