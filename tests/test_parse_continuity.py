"""Continue only from current saved parse evidence and its frozen source scope."""

import importlib.util
import json
from copy import deepcopy

import pytest

from creative_agent.evidence_policy import transcript_policy
from creative_agent.industry import Industry
from creative_agent.licensing_access import LicensedAccess
from creative_agent.store import NotFound, Store


class Keys:
    def get(self, _key):
        return None


@pytest.fixture
def parsed_env(tmp_path):
    store = Store(tmp_path / "db")
    p, q = store.create_project("P")["id"], store.create_project("Q")["id"]
    licensing = LicensedAccess(store, Industry(store, None, Keys()))
    asset = store.put(p, "asset", {"title": "Current", "media_type": "video", "source_kind": "own",
                                  "inspection": "ready", "sha256": "media-version",
                                  "path": "/Users/private/video.mp4", "metadata": {
                                      "duration_ms": 3000, "transcript_source": "user_import",
                                      "subtitles": [{"start_ms": 0, "end_ms": 1000, "text": "INSIDE_WORDS"},
                                                    {"start_ms": 1000, "end_ms": 2000, "text": "OUTSIDE_WORDS"}]}})
    policy = transcript_policy(asset)
    manifest = store.put(p, "media_input_manifest", {
        "asset_id": asset["id"], "asset_sha256": asset["sha256"], "transcript": policy,
        "execution_state": "succeeded", "pipeline_version": "video-observation-v2",
        "model_binding_revision": "binding-current", "requested_range": {"start_ms": 0, "end_ms": 3000},
        "frames": [{"frame_id": "inside-frame", "time_ms": 100}, {"frame_id": "outside-frame", "time_ms": 1500}],
    })
    base = {"asset_id": asset["id"], "asset_sha256": asset["sha256"], "input_manifest_id": manifest["id"],
            "pipeline_version": "video-observation-v2", "model_binding_revision": "binding-current",
            "claim_kind": "visual_observation", "interpretation": "observed", "quality_state": "usable",
            "execution_state": "succeeded", "allowed_uses": ["retrieve", "inspiration"]}
    inside = {**base, "observation_id": "inside-observation", "statement": "INSIDE_VISUAL",
              "support": [{"frame_id": "inside-frame", "time_ms": 100}]}
    outside = {**base, "observation_id": "outside-observation", "statement": "OUTSIDE_VISUAL",
               "support": [{"frame_id": "outside-frame", "time_ms": 1500}]}
    spoken = {**base, "observation_id": "inside-spoken", "claim_kind": "spoken_content", "statement": "INSIDE_WORDS",
              "support": [{"transcript_segment_id": policy["segments"][0]["segment_id"], "start_ms": 0, "end_ms": 1000}]}
    row = {"asset_id": asset["id"], "title": asset["title"], "input_manifest_id": manifest["id"],
           "input_manifest": manifest, "observations": [inside, outside, spoken], "provenance": "sampled_frames",
           "fields": {"Overall": "OUTSIDE_OVERALL_FIELD"}, "transcript_policy": policy,
           "observation_details": {"subtitles": policy["segments"], "transcript_policy": policy},
           "observed_timestamps_ms": [100, 1500], "requested_range": None,
           "scenes": [{"start_ms": 0, "end_ms": 1000, "description": "INSIDE_SCENE", "observations": [inside, spoken]},
                      {"start_ms": 1000, "end_ms": 2000, "description": "OUTSIDE_SCENE", "observations": [outside]}]}
    parsed = store.put(p, "parse", {"version": 2, "state": "succeeded", "asset_ids": [asset["id"]],
                                    "rows": [row], "fields": ["Overall"], "template_snapshot": {"version": 2}})
    return store, licensing, p, q, asset, parsed


def prepare(env, *, project=None, version=2, asset_ids=None, selected_segments=None):
    assert importlib.util.find_spec("creative_agent.parse_continuity"), "Saved parse continuation is missing"
    from creative_agent.parse_continuity import prepare_parse_continuation

    store, licensing, p, _q, asset, parsed = env
    return prepare_parse_continuation(store, licensing, project or p, parsed["id"], version,
                                      asset_ids if asset_ids is not None else [asset["id"]], selected_segments or [])


def segment(aid, start=0, end=1000):
    return {"asset_id": aid, "start_ms": start, "end_ms": end}


def test_full_row_continuation_reuses_current_original_evidence_and_stable_identity(parsed_env):
    store, _licensing, p, _q, asset, parsed = parsed_env
    before = store.get(p, "parse", parsed["id"])
    result = prepare(parsed_env)
    assert result["id"] == parsed["id"] and result["version"] == 2
    assert result["scope"]["asset_ids"] == [asset["id"]]
    carrier = next(item for item in result["evidence"] if item.get("observations"))
    assert carrier["input_manifest"]["id"] == parsed["rows"][0]["input_manifest_id"]
    assert carrier["fields"] == {"Overall": "OUTSIDE_OVERALL_FIELD"}
    assert {item["id"] for item in result["evidence"] if item.get("observation_id")} == {
        "inside-observation", "outside-observation", "inside-spoken"}
    assert prepare(parsed_env) == result
    assert store.get(p, "parse", parsed["id"]) == before
    assert "/Users/" not in json.dumps(result)
    assert "reobserve_asset_ids" not in result and "prior_image_rows" not in result


def test_selected_scene_contains_only_verified_in_range_observations_and_transcript(parsed_env):
    _store, _licensing, _p, _q, asset, _parsed = parsed_env
    result = prepare(parsed_env, selected_segments=[segment(asset["id"])])
    text = json.dumps(result)
    assert "INSIDE_WORDS" in text and "INSIDE_VISUAL" in text
    assert "OUTSIDE_" not in text
    assert result["scope"]["selected_segments"] == [segment(asset["id"])]
    carrier = next(item for item in result["evidence"] if item.get("observations"))
    assert "fields" not in carrier
    assert carrier["input_manifest"]["frames"] == [{"frame_id": "inside-frame", "time_ms": 100}]
    assert len(carrier["input_manifest"]["transcript"]["segments"]) == 1
    assert all(item.get("asset_id") == asset["id"] for item in result["evidence"])


@pytest.mark.parametrize("change", ["media", "transcript", "manifest_failed"])
def test_continuation_refuses_stale_evidence(parsed_env, change):
    store, _licensing, p, _q, asset, parsed = parsed_env
    if change == "media":
        store.put(p, "asset", {**asset, "sha256": "new-media"}, asset["id"])
    elif change == "transcript":
        asset["metadata"]["subtitles"][0]["text"] = "Changed words"
        store.put(p, "asset", asset, asset["id"])
    else:
        manifest = deepcopy(parsed["rows"][0]["input_manifest"])
        store.put(p, "media_input_manifest", {**manifest, "execution_state": "failed"}, manifest["id"])
    with pytest.raises(ValueError, match="重新|变化|有效|更新"):
        prepare(parsed_env)


def test_continuation_refuses_cross_project_and_wrong_version(parsed_env):
    with pytest.raises(NotFound):
        prepare(parsed_env, project=parsed_env[3])
    with pytest.raises(ValueError, match="版本"):
        prepare(parsed_env, version=1)


@pytest.mark.parametrize("bounds", [(0, 1100), (500, 1000), (2000, 2500), (-1, 1000), (1000, 1000)])
def test_selected_ranges_must_match_saved_scene_boundaries(parsed_env, bounds):
    with pytest.raises(ValueError, match="片段|范围|镜头"):
        prepare(parsed_env, selected_segments=[segment(parsed_env[4]["id"], *bounds)])


def test_selected_range_cannot_expand_source_scope_even_if_saved_row_has_scene(parsed_env):
    store, _licensing, p, _q, asset, parsed = parsed_env
    source = {"scope": {"project_id": p, "asset_ids": [asset["id"]],
                         "selected_segments": [segment(asset["id"])]}}
    store.put(p, "parse", {**parsed, "source_context": source}, parsed["id"])
    with pytest.raises(ValueError, match="范围"):
        prepare(parsed_env, selected_segments=[segment(asset["id"], 1000, 2000)])
    inherited = prepare(parsed_env)
    assert inherited["scope"]["selected_segments"] == [segment(asset["id"])]
    assert "OUTSIDE_" not in json.dumps(inherited)


def test_source_filters_remain_part_of_the_reused_scope(parsed_env):
    store, _licensing, p, _q, asset, parsed = parsed_env
    source_scope = {"project_id": p, "asset_ids": [asset["id"]], "source_kinds": ["own"],
                    "account_ids": ["approved-account"], "date_basis": "imported", "start_date": "2026-01-01"}
    store.put(p, "parse", {**parsed, "source_context": {"scope": source_scope}}, parsed["id"])
    scope = prepare(parsed_env)["scope"]
    assert scope["source_kinds"] == ["own"]
    assert scope["account_ids"] == ["approved-account"] and scope["start_date"] == "2026-01-01"
    assert scope["discovery"] is False


def test_selected_asset_requires_a_saved_successful_row(parsed_env):
    store, _licensing, p, _q, asset, parsed = parsed_env
    other = store.put(p, "asset", {**asset, "title": "Not parsed"})
    with pytest.raises(ValueError, match="素材|范围"):
        prepare(parsed_env, asset_ids=[other["id"]])
    row = {**parsed["rows"][0], "observations": [], "scenes": []}
    store.put(p, "parse", {**parsed, "rows": [row]}, parsed["id"])
    with pytest.raises(ValueError, match="依据|观察|重新"):
        prepare(parsed_env)


def test_mixed_support_observation_is_dropped_rather_than_bringing_outside_statement(parsed_env):
    store, _licensing, p, _q, asset, parsed = parsed_env
    row = deepcopy(parsed["rows"][0])
    mixed = {**row["observations"][0], "observation_id": "mixed-observation", "statement": "OUTSIDE_MIXED_STATEMENT",
             "support": row["observations"][0]["support"] + row["observations"][1]["support"]}
    row["observations"].append(mixed)
    row["scenes"][0]["observations"].append(mixed)
    store.put(p, "parse", {**parsed, "rows": [row]}, parsed["id"])
    result = prepare(parsed_env, selected_segments=[segment(asset["id"])])
    assert "OUTSIDE_" not in json.dumps(result)


def test_continuation_rechecks_current_model_permission(parsed_env):
    from test_industry import archived_scope, rights

    store, licensing, p, _q, asset, _parsed = parsed_env
    allowed = {**rights(), "retain_media": True, "model_process": False}
    _conn, job, raw = archived_scope(licensing.industry, p, allowed=allowed)
    ref = store.put(p, "industry_reference", {"job_id": job["id"], "result_id": raw["id"], "asset_id": asset["id"]})
    asset["metadata"]["industry_reference_ids"] = [ref["id"]]
    store.put(p, "asset", {**asset, "source_kind": "industry"}, asset["id"])
    with pytest.raises(ValueError):
        prepare(parsed_env)


def add_legacy_image(parsed_env, *, keep_video=False):
    store, _licensing, p, _q, _video, parsed = parsed_env
    image = store.put(p, "asset", {"title": "Legacy image", "media_type": "image", "source_kind": "own",
                                  "inspection": "ready", "sha256": "image-version",
                                  "path": "/Users/private/image.png", "metadata": {"width": 640, "height": 480}})
    row = {"asset_id": image["id"], "title": image["title"], "provenance": "image", "legacy_evidence": True,
           "input_manifest_id": None, "input_manifest": None, "observations": [], "requested_range": None,
           "transcript_policy": transcript_policy(image), "observed_timestamps_ms": [0], "scenes": [],
           "fields": {"Overall": "Old image interpretation; /Users/private/reference.png"},
           "cells": {"overall": {"value": "Old provider analysis", "basis": "observed", "state": "available"}}}
    rows = [*parsed["rows"], row] if keep_video else [row]
    ids = [*parsed["asset_ids"], image["id"]] if keep_video else [image["id"]]
    saved = store.put(p, "parse", {**parsed, "asset_ids": ids, "rows": rows}, parsed["id"])
    return image, saved


def test_current_image_without_manifest_declares_reobservation_and_never_fabricates_evidence(parsed_env):
    image, _parsed = add_legacy_image(parsed_env)
    result = prepare(parsed_env, asset_ids=[image["id"]])
    assert result["reobserve_asset_ids"] == [image["id"]]
    assert result["evidence"] == []
    prior = result["prior_image_rows"][0]
    assert prior["asset_id"] == image["id"]
    assert prior["provenance"] == "legacy_unverified_provider_analysis"
    assert prior["fields"]["Overall"].startswith("Old image interpretation")
    assert "observations" not in prior and "input_manifest_id" not in prior
    assert "observed_timestamps_ms" not in prior and "start_ms" not in json.dumps(prior)
    assert "basis" not in prior["cells"]["overall"]
    assert "/Users/" not in json.dumps(result)
    assert result["scope"]["selected_segments"] == []


def test_mixed_image_and_video_reuses_only_video_evidence_and_lists_image_call(parsed_env):
    image, _parsed = add_legacy_image(parsed_env, keep_video=True)
    video = parsed_env[4]
    result = prepare(parsed_env, asset_ids=[video["id"], image["id"]], selected_segments=[segment(video["id"])])
    assert result["reobserve_asset_ids"] == [image["id"]]
    assert result["scope"]["asset_ids"] == [video["id"], image["id"]]
    assert result["scope"]["selected_segments"] == [segment(video["id"])]
    assert all(item["asset_id"] == video["id"] for item in result["evidence"])
    assert "OUTSIDE_" not in json.dumps(result["evidence"])
    assert len(result["prior_image_rows"]) == 1


@pytest.mark.parametrize("origin", ["selected", "source_scope", "row"])
def test_image_cannot_be_requested_or_inherited_as_video_time_ranges(parsed_env, origin):
    store, _licensing, p, _q, _video, _original = parsed_env
    image, parsed = add_legacy_image(parsed_env)
    selection = []
    if origin == "selected":
        selection = [segment(image["id"])]
    elif origin == "source_scope":
        parsed["source_context"] = {"scope": {"project_id": p, "asset_ids": [image["id"]],
                                               "selected_segments": [segment(image["id"])]}}
    else:
        parsed["rows"][0]["requested_range"] = {"start_ms": 0, "end_ms": 1000}
    if origin != "selected":
        store.put(p, "parse", parsed, parsed["id"])
    with pytest.raises(ValueError, match="图片|时间|片段"):
        prepare(parsed_env, asset_ids=[image["id"]], selected_segments=selection)


def test_image_reobservation_branch_still_requires_current_row_version(parsed_env):
    store, _licensing, p, _q, _video, _original = parsed_env
    image, _parsed = add_legacy_image(parsed_env)
    store.put(p, "asset", {**image, "sha256": "replaced-image"}, image["id"])
    with pytest.raises(ValueError, match="变化|重新"):
        prepare(parsed_env, asset_ids=[image["id"]])


def test_unverified_video_without_manifest_cannot_use_image_reobservation_fallback(parsed_env):
    store, _licensing, p, _q, video, parsed = parsed_env
    row = {"asset_id": video["id"], "title": video["title"], "provenance": "sampled_frames",
           "transcript_policy": transcript_policy(video), "fields": {"Overall": "Old unverified video"}}
    store.put(p, "parse", {**parsed, "rows": [row]}, parsed["id"])
    with pytest.raises(ValueError, match="依据|重新"):
        prepare(parsed_env)
