"""Prepare cached parse evidence without widening its saved material/time scope."""

from __future__ import annotations

from copy import deepcopy

from .evidence_policy import validate_observations
from .material_views import MaterialViews, _safe


def _range(value):
    if (not isinstance(value, dict) or type(value.get("start_ms")) is not int
            or type(value.get("end_ms")) is not int or not 0 <= value["start_ms"] < value["end_ms"]):
        raise ValueError("片段时间无效，请重新选择已保存的镜头")
    return {"start_ms": value["start_ms"], "end_ms": value["end_ms"]}


def _contains(outer, inner):
    return outer["start_ms"] <= inner["start_ms"] < inner["end_ms"] <= outer["end_ms"]


def _support_inside(support, interval):
    if "time_ms" in support:
        return type(support["time_ms"]) is int and interval["start_ms"] <= support["time_ms"] < interval["end_ms"]
    try:
        return _contains(interval, _range(support))
    except ValueError:
        return False


def _verified(row, manifest, interval=None):
    observations = [*row.get("observations", [])]
    for scene in row.get("scenes", []):
        observations.extend(scene.get("observations", []))
    found = {}
    for observation in observations:
        if observation.get("execution_state", "succeeded") != "succeeded":
            continue
        try:
            checked = validate_observations(manifest, [observation])[0]
        except (ValueError, KeyError, TypeError):
            continue
        if checked["quality_state"] != "usable" or not checked["allowed_uses"] or not checked["support"]:
            continue
        # A statement supported partly outside the selection may describe that
        # omitted content. Do not retain its words by merely trimming supports.
        if interval and not all(_support_inside(support, interval) for support in checked["support"]):
            continue
        result = {**deepcopy(observation), "support": checked["support"]}
        identifier = observation.get("observation_id") or checked["observation_id"]
        result["observation_id"] = identifier
        found[identifier] = result
    return list(found.values())


def _clip_manifest(manifest, interval):
    result = deepcopy(manifest)
    result["frames"] = [frame for frame in manifest.get("frames", [])
                        if _support_inside(frame, interval)]
    transcript = deepcopy(manifest.get("transcript") or {})
    transcript["segments"] = [segment for segment in transcript.get("segments", [])
                              if _contains(interval, segment)]
    result["transcript"] = transcript
    result["requested_range"] = deepcopy(interval)
    result["projection"] = {"source_manifest_id": manifest["id"], "selected_range": deepcopy(interval)}
    if result.get("native_video"):
        result["native_video"] = {**result["native_video"], **interval}
    result["coverage"] = {"visual_basis": (manifest.get("coverage") or {}).get("visual_basis", "saved_parse_subset"),
                          "requested_range": deepcopy(interval)}
    return result


def _carrier(parsed, asset, row, row_index, manifest, interval):
    observations = _verified(row, manifest, interval)
    if not observations:
        raise ValueError("所选素材或片段没有当前可用的观察依据，请重新拆解或选择有依据的镜头")
    identity = f"parse:{parsed['id']}:asset:{asset['id']}:row:{row_index}"
    if interval:
        identity += f":{interval['start_ms']}-{interval['end_ms']}"
        carrier = {"requested_range": deepcopy(interval), "start_ms": interval["start_ms"],
                   "end_ms": interval["end_ms"], "input_manifest": _clip_manifest(manifest, interval),
                   "observed_timestamps_ms": [frame["time_ms"] for frame in manifest.get("frames", [])
                                              if _support_inside(frame, interval)]}
    else:
        carrier = deepcopy(row)
        carrier["input_manifest"] = deepcopy(manifest)
    carrier.update(id=identity, asset_id=asset["id"], asset_sha256=asset["sha256"], title=asset.get("title", "素材"),
                   provenance=row.get("provenance", "saved_parse"), observations=observations,
                   input_manifest_id=manifest["id"], parse_id=parsed["id"], cached_parse=True)
    return carrier


def _prior_image_row(parsed, asset, row, row_index):
    """Old image output is context for a fresh image call, never input proof."""
    cells = {field: {key: deepcopy(cell[key]) for key in ("value", "type", "unit", "state") if key in cell}
             for field, cell in (row.get("cells") or {}).items() if isinstance(cell, dict)}
    return {"id": f"parse:{parsed['id']}:asset:{asset['id']}:row:{row_index}",
            "parse_id": parsed["id"], "row_index": row_index, "asset_id": asset["id"],
            "title": row.get("title") or asset.get("title", "图片"),
            "provenance": "legacy_unverified_provider_analysis",
            "source_provenance": row.get("provenance", "image"),
            "fields": deepcopy(row.get("fields") or {}), "cells": cells}


def prepare_parse_continuation(store, licensing, p, parse_id, version, asset_ids, selected_segments):
    """Return licensed current evidence for exactly the saved user selection.

    Carriers retain canonical parse/asset/manifest identities. Granular evidence
    is also flattened so the normal answer validator can cite observation IDs.
    Manifest-free image rows only declare a fresh image observation requirement;
    their old provider analysis is separated from verified cached evidence.
    No media, model, index or provider operation is performed here.
    """
    parsed = store.get(p, "parse", parse_id)
    current_version = parsed.get("version") or (parsed.get("template_snapshot") or {}).get("version") or 1
    if type(version) is not int or version != current_version:
        raise ValueError("拆解版本已变化，请重新打开结果再继续")
    if (not isinstance(asset_ids, list) or not 1 <= len(asset_ids) <= 100
            or any(not isinstance(aid, str) or not aid for aid in asset_ids)
            or len(set(asset_ids)) != len(asset_ids)):
        raise ValueError("请选择1–100条已完成拆解的不同素材")
    if not isinstance(selected_segments, list) or len(selected_segments) > 200:
        raise ValueError("片段选择无效，请重新选择镜头")
    licensing.check(p, parsed, "parse", model=True)
    original_scope = (parsed.get("source_context") or {}).get("scope") or {}
    original_ids = set(original_scope.get("asset_ids", parsed.get("asset_ids", [])))
    if original_scope.get("project_id", p) != p or set(asset_ids) - original_ids:
        raise ValueError("所选素材超出原拆解来源范围")
    selected = {aid: [] for aid in asset_ids}
    for raw in selected_segments:
        if not isinstance(raw, dict) or raw.get("asset_id") not in selected:
            raise ValueError("所选片段素材超出本次素材范围")
        interval = _range(raw)
        if interval not in selected[raw["asset_id"]]:
            selected[raw["asset_id"]].append(interval)
    original_segments = original_scope.get("selected_segments") or []
    views = MaterialViews(store, licensing)
    evidence, effective_segments, reobserve_asset_ids, prior_image_rows = [], [], [], []
    for aid in asset_ids:
        view = views.analysis_results(p, aid)
        asset = view["asset"]
        current_rows = [(item["row_index"], item["record"]["rows"][0]) for item in view["results"]
                        if item["kind"] == "parse" and item["record"]["id"] == parse_id]
        current_rows = [(index, row) for index, row in current_rows
                        if row.get("state", "succeeded") == "succeeded"
                        and row.get("execution_state", "succeeded") == "succeeded"]
        if not current_rows:
            raise ValueError("拆解依据已变化或未成功完成，请重新拆解所选素材后继续")
        source_ranges = [_range(raw) for raw in original_segments if raw.get("asset_id") == aid]
        if asset.get("media_type") == "image":
            if selected[aid] or source_ranges or any(row.get("requested_range") for _index, row in current_rows):
                raise ValueError("图片没有视频时间片段，请选择整张图片后继续")
            legacy_rows = [(index, row) for index, row in current_rows
                           if not row.get("input_manifest_id") and not row.get("input_manifest")
                           and not (row.get("observation_details") or {}).get("input_manifest_id")]
            if not legacy_rows:
                raise ValueError("图片拆解来源格式需要更新，请重新打开或拆解图片后继续")
            reobserve_asset_ids.append(aid)
            prior_image_rows.extend(_prior_image_row(parsed, asset, row, index) for index, row in legacy_rows)
            continue
        candidates = []
        for index, row in current_rows:
            mid = row.get("input_manifest_id")
            if not mid:
                continue
            manifest = store.get(p, "media_input_manifest", mid)
            bounds = _range(row.get("requested_range") or manifest.get("requested_range")
                            or {"start_ms": 0, "end_ms": asset.get("metadata", {}).get("duration_ms")})
            candidates.append((index, row, manifest, bounds))
        if selected[aid]:
            for interval in selected[aid]:
                if source_ranges and not any(_contains(source, interval) for source in source_ranges):
                    raise ValueError("所选片段超出原分析时间范围，请重新选择")
                match = next(((index, row, manifest) for index, row, manifest, bounds in candidates
                              if _contains(bounds, interval)
                              and any(_range(scene) == interval for scene in row.get("scenes", []))), None)
                if match is None:
                    raise ValueError("片段必须与已保存的镜头范围完全一致，请重新选择镜头")
                index, row, manifest = match
                evidence.append(_carrier(parsed, asset, row, index, manifest, interval))
                effective_segments.append({"asset_id": aid, **interval})
        else:
            for index, row, manifest, bounds in candidates:
                limits = source_ranges or [bounds]
                for source in limits:
                    interval = {"start_ms": max(source["start_ms"], bounds["start_ms"]),
                                "end_ms": min(source["end_ms"], bounds["end_ms"])}
                    if interval["start_ms"] >= interval["end_ms"]:
                        continue
                    full = (not source_ranges and not row.get("requested_range")
                            and bounds == {"start_ms": 0, "end_ms": asset.get("metadata", {}).get("duration_ms")})
                    evidence.append(_carrier(parsed, asset, row, index, manifest, None if full else interval))
                    if not full:
                        effective_segments.append({"asset_id": aid, **interval})
        if not any(item.get("asset_id") == aid for item in evidence):
            raise ValueError("所选素材没有当前可用的观察依据，请重新拆解后继续")
    flattened = {}
    for carrier in evidence:
        for observation in carrier["observations"]:
            item = {**deepcopy(observation), "id": observation["observation_id"], "title": carrier["title"],
                    "provenance": carrier["provenance"]}
            starts = [support.get("time_ms", support.get("start_ms")) for support in item["support"]]
            ends = [support["time_ms"] + 1 if "time_ms" in support else support.get("end_ms") for support in item["support"]]
            item.update(start_ms=min(starts), end_ms=max(ends))
            flattened[item["id"]] = item
    filters = ("source_kinds", "account_ids", "date_basis", "start_date", "end_date", "min_spend", "min_roas")
    scope = {**{key: deepcopy(original_scope[key]) for key in filters if key in original_scope},
             "project_id": p, "asset_ids": asset_ids, "selected_segments": [], "discovery": False}
    for segment in effective_segments:
        if segment not in scope["selected_segments"]:
            scope["selected_segments"].append(segment)
    result = {"id": parse_id, "version": current_version, "evidence": [*evidence, *flattened.values()], "scope": scope}
    if reobserve_asset_ids:
        result.update(reobserve_asset_ids=reobserve_asset_ids, prior_image_rows=prior_image_rows)
    return _safe(result)
