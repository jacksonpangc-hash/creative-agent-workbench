"""Read-only, licensed projections of canonical materials and saved outcomes.

Nothing here creates a second result store. Result identities point to a saved
session turn or parse row; projections cannot expand that object's source scope.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy

from .evidence_policy import transcript_policy
from .material_readiness import readiness_context, readiness_for_asset
from .store import NotFound, dumps, export_safe

_LOCAL_PATH = re.compile(r"file://[^\s<>\"']+|(?:/Users/|/home/|/private/|/tmp/|/var/|/Volumes/|/root/|/opt/|/mnt/|/etc/)[^\s<>\"']+|[A-Za-z]:\\[^\s<>\"']+")
_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]+=*", re.IGNORECASE)
_URL = re.compile(r"https?://[^\s<>\"']+")
_PRIVATE_KEYS = {"credentials", "headers", "key_ref", "vault_ref", "token", "apikey", "accesstoken",
                 "refreshtoken", "clientsecret", "secretref", "localpath"}
_ASSET_FIELDS = ("id", "project_id", "title", "source_kind", "source_url", "media_type", "inspection",
                 "sha256", "created_at", "updated_at", "description")
_META_FIELDS = ("duration_ms", "width", "height", "fps", "has_audio", "published_at", "platform_id",
                "sku_id", "product_id", "generic_footage")
_JOB_FIELDS = ("id", "project_id", "kind", "state", "quote", "actual_cost", "created_at", "updated_at",
               "content_restricted")


def _safe(value):
    """Keep export redaction, including old imported text carrying local paths."""
    value = export_safe(value)

    def clean(item):
        if isinstance(item, dict):
            return {key: clean(part) for key, part in item.items()
                    if key.lower() not in _PRIVATE_KEYS
                    and not key.lower().endswith(("_token", "_secret"))}
        if isinstance(item, list):
            return [clean(part) for part in item]
        if isinstance(item, str):
            text = _URL.sub(lambda match: export_safe(match.group(0)), item)
            return _BEARER.sub("[凭证已隐藏]", _LOCAL_PATH.sub("[本地路径已隐藏]", text))
        return item

    return clean(value)


def _pick(record, fields):
    return {key: deepcopy(record[key]) for key in fields if key in record}


def _asset_ids(value):
    """Explicit normalized ownership only; a provider's raw vocabulary is opaque."""
    if isinstance(value, list):
        return set().union(*(_asset_ids(item) for item in value)) if value else set()
    if not isinstance(value, dict):
        return set()
    ids = {value["asset_id"]} if value.get("asset_id") else set()
    ids.update(value.get("asset_ids") or [])
    for key, item in value.items():
        if key != "raw" and isinstance(item, (dict, list)):
            ids.update(_asset_ids(item))
    return ids


class MaterialViews:
    def __init__(self, store, licensing):
        self.store, self.licensing = store, licensing

    def _asset(self, p, aid):
        return self.licensing.check(p, self.store.get(p, "asset", aid), "asset")

    def _readiness_context(self, p):
        if hasattr(self.store, "path"):
            return readiness_context(self.store, p)
        # The shared Postgres Store schema has records/jobs, but no local FTS
        # or knowledge_chunk tables. Preserve known source readiness there.
        observations, speech_jobs = {}, {}
        for row in self.store.list(p, "observation"):
            observations.setdefault(row.get("asset_id"), []).append(row)
        for job in self.store.jobs(p):
            if job["kind"] == "transcription" and job["state"] in {"queued", "running"}:
                for aid in job["payload"].get("asset_ids", []):
                    speech_jobs.setdefault(aid, job["state"])
        return {"observations": observations, "speech_jobs": speech_jobs, "indexes": {},
                "manifests": {row["id"]: row for row in self.store.list(p, "media_input_manifest")}}

    def _asset_view(self, p, asset, context=None):
        context = self._readiness_context(p) if context is None else context
        return _safe({**asset, "transcript_policy": transcript_policy(asset),
                      "readiness": readiness_for_asset(self.store, p, asset, context)})

    def asset(self, p, aid):
        return self._asset_view(p, self._asset(p, aid))

    def assets(self, p, offset=0, limit=50, query="", source=""):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("素材分页需要非负起点和1–100条数量")
        if not isinstance(query, str) or len(query) > 500 or not isinstance(source, str) or len(source) > 100:
            raise ValueError("素材筛选条件无效")
        query = query.strip().casefold()
        source = source.strip()
        rows = self.licensing.visible(p, self.store.list(p, "asset"), "asset")
        rows = [row for row in rows if (not source or source == "all" or row.get("source_kind") == source)
                and (not query or query in "\n".join(str(row.get(key) or "")
                                                   for key in ("title", "description", "source_url")).casefold())]
        rows.sort(key=lambda row: (row.get("created_at", ""), row["id"]), reverse=True)
        page = rows[offset:offset + limit]
        context = self._readiness_context(p) if page else None
        items = []
        for row in page:
            summary = {**_pick(row, _ASSET_FIELDS), "metadata": _pick(row.get("metadata") or {}, _META_FIELDS),
                       "readiness": readiness_for_asset(self.store, p, row, context)}
            items.append(_safe(summary))
        return {"items": items, "total": len(rows),
                "next_offset": offset + limit if offset + limit < len(rows) else None}

    def _current(self, p, asset, item, manifests):
        """Fail closed when the source snapshot cannot establish current versions."""
        aid = asset["id"]
        if _asset_ids(item) - {aid}:
            return False
        try:
            self.licensing.check(p, item)
            details = item.get("observation_details") or {}
            expected_hash = item.get("asset_sha256") or details.get("asset_sha256")
            if expected_hash and expected_hash != asset.get("sha256"):
                return False
            mid = item.get("input_manifest_id") or details.get("input_manifest_id")
            manifest = item.get("input_manifest")
            if mid:
                if mid not in manifests:
                    manifests[mid] = self.store.get(p, "media_input_manifest", mid)
                manifest = manifests[mid]
            if manifest:
                self.licensing.check(p, manifest)
                return (manifest.get("asset_id") == aid and bool(asset.get("sha256"))
                        and manifest.get("asset_sha256") == asset["sha256"]
                        and manifest.get("execution_state") == "succeeded"
                        and (manifest.get("transcript") or {}).get("transcript_revision")
                        == transcript_policy(asset)["transcript_revision"])
            policy = item.get("transcript_policy") or details.get("transcript_policy") or {}
            current = transcript_policy(asset)
            if policy.get("transcript_revision"):
                return bool(asset.get("sha256") and policy["transcript_revision"] == current["transcript_revision"])
            # Legacy spoken evidence predates manifests. Its timed text remains
            # readable only while every saved segment still exists verbatim.
            subtitles = item.get("subtitles")
            if (expected_hash and item.get("provenance") in {"subtitle", "local_asr"}
                    and subtitles and current["allowed"]):
                live = {(s["start_ms"], s["end_ms"], s["text"]) for s in current["segments"]}
                old = [(s.get("start_ms"), s.get("end_ms"), s.get("text")) for s in subtitles]
                return (all(s in live for s in old)
                        and item.get("transcript", "") == "\n".join(s[2] for s in old))
            return bool(expected_hash and asset.get("media_type") == "image")
        except (ValueError, NotFound):
            return False

    @staticmethod
    def _scope(p, aid, original):
        return {"project_id": p, "asset_ids": [aid], "selected_segments": [deepcopy(segment)
                for segment in (original or {}).get("selected_segments", []) if segment.get("asset_id") == aid]}

    def _session_result(self, p, asset, session, turn, index, manifests):
        aid = asset["id"]
        original_scope = turn.get("scope_snapshot") or session.get("scope") or {}
        if original_scope.get("project_id", p) != p or aid not in original_scope.get("asset_ids", []):
            return None
        evidence = [deepcopy(item) for item in turn.get("evidence", [])
                    if (item.get("asset_id") == aid or (not item.get("asset_id") and item.get("id") == aid))
                    and self._current(p, asset, item, manifests)]
        if not evidence:
            return None
        ids = {item["id"] for item in evidence if item.get("id")}
        for item in evidence:
            ids.update(o["observation_id"] for o in item.get("observations", []) if o.get("observation_id"))
        original_answer = turn.get("answer") or {}
        whole_turn = (_asset_ids(original_scope) <= {aid}
                      and len(evidence) == len(turn.get("evidence", []))
                      and not original_scope.get("reference_ids"))
        findings = [deepcopy(finding) for finding in original_answer.get("findings", [])
                    if (set(finding.get("evidence_ids") or []) <= ids
                        and (whole_turn or finding.get("evidence_ids")))]
        if whole_turn:
            answer = deepcopy(original_answer)
            answer["findings"] = findings
        else:
            summaries = [finding.get("text", "") for finding in findings]
            if not summaries:
                summaries = [(item.get("observation") or {}).get("summary", "") for item in evidence]
            answer = {"summary": "\n".join(text for text in summaries if text), "findings": findings}
        title = session.get("title") if whole_turn else asset.get("title", "素材") + " · 分析"
        scoped_turn = {**_pick(turn, ("created_at",)), "evidence": evidence, "answer": answer}
        if whole_turn:
            scoped_turn["question"] = turn.get("question", "")
        scope = self._scope(p, aid, original_scope)
        scoped_turn["scope_snapshot"] = scope
        record = {**_pick(session, ("id", "project_id", "mode", "analysis_kind", "created_at", "updated_at")),
                  "title": title or "素材分析", "scope": scope, "turn_index": index, "turns": [scoped_turn]}
        return {"id": f"session:{session['id']}:turn:{index}", "kind": "session", "title": record["title"],
                "version": index + 1, "turn_index": index, "created_at": turn.get("created_at") or session.get("created_at"),
                "record": record}

    def analysis_results(self, p, aid):
        asset = self._asset(p, aid)
        results, manifests = [], {}
        for session in self.licensing.visible(p, self.store.list(p, "session"), "session"):
            for index, turn in enumerate(session.get("turns", [])):
                result = self._session_result(p, asset, session, turn, index, manifests)
                if result:
                    results.append(result)
        for parsed in self.licensing.visible(p, self.store.list(p, "parse"), "parse"):
            for index, row in enumerate(parsed.get("rows", [])):
                if row.get("asset_id") != aid or not self._current(p, asset, row, manifests):
                    continue
                record = {**_pick(parsed, ("id", "project_id", "created_at", "updated_at", "fields", "state",
                                            "template_snapshot", "version", "video_route")),
                          "asset_ids": [aid], "rows": [deepcopy(row)], "row_index": index}
                title = asset.get("title", "素材") + " · 拆解"
                results.append({"id": f"parse:{parsed['id']}:row:{index}", "kind": "parse", "title": title,
                                "version": parsed.get("version") or (parsed.get("template_snapshot") or {}).get("version") or 1,
                                "row_index": index, "created_at": parsed.get("created_at"), "record": record})
        results.sort(key=lambda item: (item.get("created_at") or "", item["id"]), reverse=True)
        return _safe({"asset": self._asset_view(p, asset), "results": results})

    def activity(self, p):
        """Fingerprint row identities and revisions without fetching record bodies.

        Include every identity, rather than MAX(updated_at): deletion of an old
        row and concurrent rows sharing timestamps must also invalidate a view.
        Store mutations always refresh updated_at on both supported backends.
        """
        project = self.store.project(p)
        self.licensing.check(p, project)
        with self.store.connection() as db:
            records = [dict(row) for row in db.execute(
                "SELECT id,kind,created_at,updated_at FROM records WHERE project_id=? ORDER BY id", (p,))]
            summaries = [dict(row) for row in db.execute(
                "SELECT id,project_id,kind,state,quote,actual_cost,created_at,updated_at "
                "FROM jobs WHERE project_id=? ORDER BY id", (p,))]
        # This is the same metadata-only projection allowed for restricted jobs.
        # Content authorization belongs to the detail endpoint: following result
        # lineage here would reload complete sessions on every progress poll.
        for job in summaries:
            job["quote"] = json.loads(job["quote"]) if job["quote"] else None
        # Archive-only jobs live in records, and receive the same metadata view.
        for job in self.store.list(p, "migration_job"):
            summaries.append(_pick(job, _JOB_FIELDS))
        summaries.sort(key=lambda row: (row.get("created_at", ""), row["id"]), reverse=True)
        revision = hashlib.sha256(dumps({"project": project, "records": records, "jobs": summaries}).encode()).hexdigest()
        return _safe({"revision": revision, "jobs": summaries})

    def outcomes(self, p):
        self.store.project(p)
        items = []
        for kind in ("session", "parse", "script", "note"):
            for row in self.licensing.visible(p, self.store.list(p, kind), kind):
                summary = {**_pick(row, ("id", "project_id", "created_at", "updated_at")), "kind": kind,
                           "title": row.get("title") or {"session": "素材分析", "parse": "素材拆解",
                                                          "script": "脚本", "note": "笔记"}[kind],
                           "version": row.get("version") or ((row.get("template_snapshot") or {}).get("version") if kind=="parse" else None) or (max(1, len(row.get("turns", []))) if kind == "session" else 1)}
                items.append(summary)
        items.sort(key=lambda item: (item.get("created_at") or "", item["id"]), reverse=True)
        return _safe({"items": items})
