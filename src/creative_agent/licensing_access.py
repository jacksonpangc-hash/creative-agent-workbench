"""Resolve licensed source lineage before exposing or reusing cached project content."""

from .store import NotFound


class LicensedAccess:
    def __init__(self, store, industry, *, cmm=None):
        from .cmm import CMMService

        self.store, self.industry = store, industry
        self.cmm = cmm or CMMService(store, industry.vault)

    def check(self, p, value, kind=None, *, export=False, model=False, derivative=False, _seen=None):
        seen = set() if _seen is None else _seen
        if isinstance(value, list):
            for item in value:
                self.check(p, item, export=export, model=model, derivative=derivative, _seen=seen)
            return value
        if not isinstance(value, dict):
            return value
        if value.get("project_id") not in (None, p):
            raise ValueError("内容来源项目范围不一致")
        identity = (kind, value.get("id"))
        if kind and value.get("id"):
            if identity in seen:
                return value
            seen.add(identity)
        source_kind=value.get('source_kind')
        source_id=value.get('source_record_id')
        if source_id and source_kind in {'asset','public_reference','cmm_reference','script'}:
            source=self.store.get(p,source_kind,source_id)
            self.check(p,source,source_kind,export=export,model=model,derivative=derivative,_seen=seen)
        if kind == "asset":
            aid = value["id"]
            self.industry.validate_asset_access(p, aid)
            if model:
                self.industry.validate_model_use(p, asset_ids=[aid], derivative=derivative)
        if kind == "job" and value.get("kind") == "industry_search":
            self.industry._current(p, value, ["retain_metadata"] + (["export_metadata"] if export else []))
        cmm_actions = (
            ["retain_metadata"]
            + (["export_metadata"] if export else [])
            + (["model_process"] if model else [])
            + (["derivative_create"] if derivative else [])
        )
        if kind == "cmm_reference":
            self.cmm.validated_evidence(p, [value["id"]], actions=cmm_actions)
        if kind == "cmm_job":
            self.cmm.validate_job_access(p, value, actions=cmm_actions)
        pointers = {
            "parent_reference_id": "cmm_reference",
            "cmm_reference_id": "cmm_reference",
            "cmm_job_id": "cmm_job",
            "asset_id": "asset",
            "reference_asset_id": "asset",
            "finished_asset_id": "asset",
            "session_id": "session",
            "source_session_id": "session",
            "brief_id": "brief",
            "script_id": "script",
            "source_job_id": "job",
            "generation_job_id": "job",
            "job_id": "job",
            "industry_reference_id": "industry_reference",
            "reference_id": "industry_reference",
            "source_result_id": "industry_result",
            "parsed_record_id": "parse",
            "parse_id": "parse",
        }
        lists = {
            "asset_ids": "asset",
            "reference_image_ids": "asset",
            "group_ids": "group",
            "industry_reference_ids": "industry_reference",
            "reference_ids": "industry_reference",
        }
        if kind in {"industry_reference", "industry_result"}:
            job = self.store.job(p, value["job_id"])
            self.industry._current(p, job, ["retain_metadata"] + (["export_metadata"] if export else []))
            if model:
                self.industry._current(
                    p, job, ["model_process"] + (["derivative_create"] if derivative else [])
                )

        def related(target_kind, record_id):
            if not record_id:
                return
            # Shared lineage names can refer to current CMM or archived industry data.
            # Only missing identities fall back; revocation is never swallowed.
            if target_kind == "industry_reference":
                try:
                    record = self.store.get(p, "cmm_reference", record_id)
                    target_kind = "cmm_reference"
                except NotFound:
                    try:
                        record = self.store.get(p, "public_reference", record_id)
                        target_kind = "public_reference"
                    except NotFound:
                        record = self.store.get(p, "industry_reference", record_id)
            elif target_kind == "job":
                try:
                    record = self.store.get(p, "cmm_job", record_id)
                    target_kind = "cmm_job"
                except NotFound:
                    record = self.store.job(p, record_id)
            else:
                record = self.store.get(p, target_kind, record_id)
            self.check(p, record, target_kind, export=export, model=model, derivative=derivative, _seen=seen)

        for key, item in value.items():
            # Provider raw payloads have their own vocabulary; only our normalized lineage is authoritative.
            if key == "raw":
                continue
            if key == "request" and isinstance(item, dict):
                request = dict(item)
                for source in ("brief", "script"):
                    snapshot = value.get(source + "_snapshot")
                    if isinstance(snapshot, dict) and snapshot.get("id") == request.get(source + "_id"):
                        if source == "script" and snapshot.get("version") != request.get("script_version"):
                            raise ValueError("生成脚本快照与确认版本不一致")
                        request.pop(source + "_id", None)
                self.check(p, request, export=export, model=model, derivative=derivative, _seen=seen)
            elif (
                kind == "asset"
                and value.get("generation_job_id")
                and key in {"brief_id", "script_id", "source_session_id"}
            ):
                # The generation job holds the immutable source versions used for this finished media.
                continue
            elif (
                key == "script_id"
                and isinstance(value.get("script_snapshot"), dict)
                and value["script_snapshot"].get("id") == item
            ):
                if value.get("script_version") != value["script_snapshot"].get("version"):
                    raise ValueError("脚本快照版本不一致")
                continue
            elif key in pointers and item:
                related(pointers[key], item)
            elif key in lists:
                for record_id in item or []:
                    related(lists[key], record_id)
            elif isinstance(item, (dict, list)):
                self.check(p, item, export=export, model=model, derivative=derivative, _seen=seen)
        if kind == "job" and isinstance(value.get("result"), dict):
            result = value["result"]
            if result.get("record_id") and result.get("kind") in {"session", "parse", "search", "embedding"}:
                related(result["kind"], result["record_id"])
        return value

    def visible(self, p, values, kind=None):
        result = []
        for value in values:
            try:
                self.check(p, value, kind)
                result.append(value)
            except (ValueError, NotFound):
                continue
        return result

    def job_view(self, p, job):
        try:
            return self.check(p, job, "job")
        except ValueError:
            return {
                **{
                    k: job[k]
                    for k in (
                        "id",
                        "project_id",
                        "kind",
                        "state",
                        "quote",
                        "actual_cost",
                        "created_at",
                        "updated_at",
                    )
                },
                "content_restricted": True,
            }
