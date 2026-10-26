"""Structural regressions derived from the 2026-10-24 account response.

The records below are synthetic. They are not business quality or billing proof.
"""
from copy import deepcopy

import pytest

from creative_agent.cmm_schema import SEARCH, parse_response


def response():
    return {
        "rid": "synthetic-request",
        "code": 0,
        "msg": "success",
        "data": {
            "remaining_ai_score_decimal": "983.00",
            "data": {
                "list": [{
                    "视频信息": {
                        "视频ID": "supplier-opaque-1",
                        "视频标题": "结构测试标题",
                        "抖音播放地址": "https://www.iesdouyin.com/share/video/1234567890123456789/?share_sign=must-not-persist&did=private-tracking",
                        "视频发布时间": "2026-10-01 10:20:30",
                        "视频时长": 23.8,
                        "点赞数": 0,
                        "视频销量": "1000~2500",
                        "视频销售额": "1w~2.5w",
                        "视频销量指数": "364.74",
                        "预估曝光": 390000,
                        "视频文案": "",
                    },
                    "达人信息": {"达人名称": "测试作者", "达人ID": "author-id", "视频销售额": "1000w+"},
                    "带货商品信息": {"商品ID": "product-id", "商品标题": "仅参考商品"},
                }],
                "分页信息": {"page": 1, "size": 50, "totalCount": 1, "totalPage": 1},
            },
        },
    }


@pytest.mark.parametrize("operation", [SEARCH["commerce"], SEARCH["popular"]])
def test_live_chinese_envelope_keeps_identity_and_metric_meaning(operation):
    result = parse_response(operation, response())
    assert len(result["results"]) == 1
    item = result["results"][0]
    assert item["supplier_video_id"] == "supplier-opaque-1"
    assert item["platform_video_id"] == "1234567890123456789"
    assert item["source_url"] == "https://www.douyin.com/video/1234567890123456789"
    assert item["content_basis"] == "metadata"
    assert item.get("media_url") is None and not item.get("text")
    assert item["metrics"]["likes"]["value"] == 0
    assert item["metrics"]["comments"]["kind"] == "missing"
    assert item["metrics"]["sales"]["raw_value"] == "1000~2500"
    assert item["metrics"]["revenue"]["raw_value"] == "1w~2.5w"
    assert item["metrics"]["estimated_exposure"]["kind"] == "estimated"
    assert item["metrics"]["sales"]["object_type"] == "video"
    assert item["metrics"]["sales_index"]["kind"] == "index"
    assert item["metrics"]["sales_index"]["value"] == "364.74"
    assert item["metrics"].get("observed_at") is None
    assert "share_sign" not in str(result) and "private-tracking" not in str(result)


def test_live_list_empty_is_distinct_from_unknown_response():
    data = response()
    data["data"]["data"]["list"] = []
    assert parse_response(SEARCH["commerce"], data)["results"] == []
    del data["data"]["data"]["list"]
    with pytest.raises(ValueError, match="schema"):
        parse_response(SEARCH["commerce"], data)


def test_live_free_video_lookup_does_not_invent_platform_id_or_title():
    data = {"code": 0, "data": {"data": [{"video_id": "opaque", "desc": "", "video_type": 1}]}}
    item = parse_response("video_search", data)["results"][0]
    assert item["supplier_video_id"] == "opaque"
    assert item["title"] is None and item.get("platform_video_id") is None


def test_malformed_or_foreign_list_record_stops_the_operation():
    for change in ({"视频信息": []}, {"视频信息": {"视频ID": "x"}}):
        data = deepcopy(response())
        data["data"]["data"]["list"][0] = change
        with pytest.raises(ValueError, match="schema"):
            parse_response(SEARCH["commerce"], data)


def test_detail_uses_millisecond_duration_and_labels_supplier_transcript():
    row = response()["data"]["data"]["list"][0]
    video = row["视频信息"]
    video["视频id"] = video.pop("视频ID")
    video["发布时间"] = video.pop("视频发布时间")
    video["视频时长（毫秒）"] = 23800
    video["带货商品"] = [{"商品ID": "product-id", "商品名称": "参考商品"}]
    detail = {"视频信息": video, "达人信息": {"达人名称": "测试作者", "达人id": "author-id"},
              "视频脚本文案": "上游提取的台词，没有逐句时间戳"}
    data = {"code": 0, "data": {"data": detail}}
    item = parse_response("video_detail_info", data)["results"][0]
    assert item["duration"] == 23.8
    assert item["text"] == detail["视频脚本文案"]
    assert item["content_basis"] == "supplier_transcript"
    assert item["author_id"] == "author-id"
    assert item["products"] == [{"supplier_product_id": "product-id", "title": "参考商品"}]
    assert not item.get("scenes") and item.get("media_url") is None
