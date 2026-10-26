from __future__ import annotations

import re
import secrets
import time
from threading import RLock
from typing import Literal

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import Field, StrictBool

from .contracts import Contract
from .discovery import Discovery
from .store import NotFound, export_safe


def extension_origin(origin):
    return bool(re.fullmatch(r'chrome-extension://[a-p]{32}', origin or ''))


def discovery_extension_request(request):
    """Narrow local middleware exception. Caller MUST still enforce local Host."""
    return request.url.path in {'/extension/pair', '/extension/import'} and request.method in {'POST', 'OPTIONS'} and extension_origin(request.headers.get('origin'))


class Pairings:
    def __init__(self, clock=time.monotonic):
        self.clock, self.lock = clock, RLock()
        self.codes, self.tokens = {}, {}
        self.failures = []

    def create(self, project_id):
        with self.lock:
            current = self.clock()
            self.codes = {k: v for k, v in self.codes.items() if v['expires'] > current}
            self.tokens = {k: v for k, v in self.tokens.items() if v['expires'] > current}
            code = ''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(10))
            self.codes[code] = {'project': project_id, 'expires': current + 120}
            return {'code': code, 'expires_in': 120, 'project_id': project_id}

    def redeem(self, code, origin):
        if not extension_origin(origin):
            raise ValueError('仅允许扩展发起配对')
        with self.lock:
            current = self.clock()
            self.failures = [f for f in self.failures if current - f < 120]
            if len(self.failures) >= 5:
                raise ValueError('配对尝试过多，请稍后在应用重新生成配对码')
            record = self.codes.get(code.strip().upper())
            if not record or record['expires'] <= current:
                self.failures.append(current)
                raise ValueError('配对码无效或已过期')
            del self.codes[code.strip().upper()]
            token = secrets.token_urlsafe(32)
            self.tokens[token] = {**record, 'origin': origin, 'expires': current + 300}
            return {'import_token': token, 'project_id': record['project'], 'expires_in': 300}

    def consume(self, token, origin):
        with self.lock:
            record = self.tokens.get(token)
            if not record or record['expires'] <= self.clock() or record['origin'] != origin:
                raise ValueError('导入授权无效、来源不匹配或已过期，请重新配对')
            del self.tokens[token]
            return record['project']


class PlanInput(Contract):
    query: str = Field(min_length=1, max_length=200)
    platforms: list[Literal['douyin', 'kuaishou']] = Field(default=['douyin', 'kuaishou'], min_length=1, max_length=2)


class PairInput(Contract):
    code: str = Field(min_length=1, max_length=40)


class ImportInput(Contract):
    import_token: str = Field(min_length=1, max_length=100)
    captures: list[dict] = Field(min_length=1, max_length=50)


class LocalImportInput(Contract):
    format: Literal['text', 'csv', 'json']
    content: str = Field(min_length=1, max_length=1_000_000)
    ignore_unknown: bool = False


class AttachInput(Contract):
    asset_id: str = Field(min_length=1, max_length=100)


class ReferenceMediaInput(Contract):
    rights_confirmed: StrictBool
    idempotency_key: str = Field(min_length=1, max_length=120)


def register_discovery(app, store, media=None, *, dispatch=None, external_guard=None):
    discovery, pairings = Discovery(store), Pairings()
    app.state.discovery = discovery
    app.state.discovery_pairings = pairings
    if media is not None:
        from .media_fetch import ReferenceMedia
        app.state.discovery_media = ReferenceMedia(store, media, discovery, external_guard=external_guard)

    def call(fn, *args):
        try:
            return fn(*args)
        except NotFound as error:
            raise HTTPException(404, str(error)) from None
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(400, str(error)) from None

    @app.get('/api/projects/{p}/discovery/plans')
    def plans(p: str):
        return call(discovery.plans, p)

    @app.post('/api/projects/{p}/discovery/plans')
    def plan(p: str, body: PlanInput):
        return call(discovery.create_plan, p, body.query, body.platforms)

    @app.get('/api/projects/{p}/discovery/references')
    def references(p: str):
        return call(discovery.references, p)

    @app.post('/api/projects/{p}/discovery/references')
    def collect(p: str, body: list[dict]):
        return call(discovery.collect, p, body, "manual_link")

    @app.post('/api/projects/{p}/discovery/references/preview')
    def preview(p: str, body: LocalImportInput):
        return call(discovery.preview_import, p, body.format, body.content)

    @app.post('/api/projects/{p}/discovery/references/import')
    def import_local(p: str, body: LocalImportInput):
        return call(discovery.import_references, p, body.format, body.content, body.ignore_unknown)

    @app.post('/api/projects/{p}/discovery/references/{rid}/attach')
    def attach(p: str, rid: str, body: AttachInput):
        return call(discovery.attach, p, rid, body.asset_id)

    def media_service():
        service = getattr(app.state, 'discovery_media', None)
        if service is None:
            raise HTTPException(503, '本地取片服务未连接，请补充视频文件')
        return service

    @app.post('/api/projects/{p}/discovery/references/{rid}/media')
    def fetch_media(p: str, rid: str, body: ReferenceMediaInput):
        job = call(media_service().request, p, rid, body.rights_confirmed, body.idempotency_key)
        return export_safe(dispatch(p, job) if dispatch else job)

    @app.get('/api/projects/{p}/discovery/references/{rid}/media')
    def media_status(p: str, rid: str):
        return export_safe(call(media_service().status, p, rid))

    @app.post('/api/projects/{p}/discovery/pairing')
    def create_pairing(p: str):
        store.project(p)
        return pairings.create(p)

    def check(request):
        if getattr(app.state, 'security_profile', 'local') != 'local' or not discovery_extension_request(request):
            raise HTTPException(403, '只允许本地扩展主动收集')
        return request.headers['origin']

    @app.options('/extension/pair')
    @app.options('/extension/import')
    def preflight(request: Request):
        origin = check(request)
        return Response(status_code=204, headers={'Access-Control-Allow-Origin':origin, 'Access-Control-Allow-Methods':'POST,OPTIONS', 'Access-Control-Allow-Headers':'Content-Type', 'Vary':'Origin', 'Cache-Control':'no-store'})

    def result(origin, fn, *args):
        try:
            data, status = fn(*args), 200
        except (ValueError, KeyError, TypeError) as error:
            data, status = {'detail':str(error)}, 400
        return JSONResponse(data, status_code=status, headers={'Access-Control-Allow-Origin':origin, 'Vary':'Origin', 'Cache-Control':'no-store'})

    @app.post('/extension/pair')
    def pair(body: PairInput, request: Request):
        origin = check(request)
        return result(origin, pairings.redeem, body.code, origin)

    @app.post('/extension/import')
    def import_references(body: ImportInput, request: Request):
        origin = check(request)
        def collect_paired():
            project = pairings.consume(body.import_token, origin)
            before = {row['id'] for row in discovery.references(project)}
            references = discovery.collect(project, body.captures)
            created, updated = 0, 0
            for reference in references:
                if reference['id'] in before:
                    updated += 1
                else:
                    created += 1
                    before.add(reference['id'])
            return {'references': references, 'project_id': project,
                    'receipt': {'created': created, 'updated': updated, 'skipped': 0, 'failed': 0}}
        return result(origin, collect_paired)
