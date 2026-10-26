from typing import Literal

from fastapi import Request
from fastapi.responses import Response
from urllib.parse import quote
from pydantic import Field

from .contracts import Contract
from .scripts import ProductFacts, ScriptContent, Scripts
from .store import export_safe


class Revision(ScriptContent):
    expected_version: int = Field(ge=1)


class FromSession(Contract):
    session_id: str
    turn_index: int = Field(ge=0)
    script_index: int = Field(default=0,ge=0)


class Comment(Contract):
    version: int = Field(ge=1)
    text: str = Field(min_length=1,max_length=10000)
    row_index: int | None = Field(default=None,ge=0)
    verdict: Literal['comment','approved','changes_requested'] = 'comment'


class Production(Contract):
    version: int = Field(ge=1)
    assignee: str = Field(default='',max_length=200)
    idempotency_key: str = Field(min_length=1,max_length=180)


class TaskUpdate(Contract):
    expected_version: int = Field(ge=1)
    status: Literal['in_progress','in_review','approved','cancelled']
    finished_asset_id: str | None = None


class FactsRevision(ProductFacts):
    expected_version: int = Field(ge=0)


def register_scripts(app,store,*,access=None):
    service=Scripts(store)
    root='/api/projects/{p}'

    def check(p,value,kind=None):
        return access.check(p,value,kind) if access else value

    def visible(p,values,kind):
        return access.visible(p,values,kind) if access else values

    @app.get(root+'/scripts')
    def scripts(p:str): return export_safe(visible(p,store.list(p,'script'),'script'))

    @app.post(root+'/scripts')
    def create(p:str,body:ScriptContent):
        check(p,body.model_dump())
        return service.create(p,body.model_dump())

    @app.post(root+'/scripts/from-session')
    def from_session(p:str,body:FromSession):
        check(p,{'session_id':body.session_id})
        return service.from_session(p,body.session_id,body.turn_index,body.script_index)

    @app.get(root+'/scripts/{sid}')
    def get(p:str,sid:str): return export_safe(check(p,service.get(p,sid),'script'))

    @app.get(root+'/scripts/{sid}/export')
    def export_script(p:str,sid:str,format:str='docx',version:int|None=None):
        script=service.get(p,sid)
        check(p,script,'script')
        if access:access.check(p,script,'script',export=True)
        data=service.export(p,sid,format,version)
        types={'docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','pdf':'application/pdf','md':'text/markdown; charset=utf-8','json':'application/json'}
        return Response(data,media_type=types[format],headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(script['title']+'.'+format),'Cache-Control':'no-store'})

    @app.put(root+'/scripts/{sid}')
    def update(p:str,sid:str,body:Revision):
        check(p,service.get(p,sid),'script')
        check(p,body.model_dump())
        return service.revise(p,sid,body.expected_version,body.model_dump(exclude={'expected_version'}))

    @app.get(root+'/scripts/{sid}/comments')
    def comments(p:str,sid:str):
        check(p,service.get(p,sid),'script')
        return [c for c in store.list(p,'script_comment') if c['script_id']==sid]

    @app.post(root+'/scripts/{sid}/comments')
    def comment(p:str,sid:str,body:Comment,request:Request):
        check(p,service.get(p,sid),'script')
        return service.comment(p,sid,body.version,getattr(request.state,'actor_id','local'),body.text,row_index=body.row_index,verdict=body.verdict)

    @app.post(root+'/scripts/{sid}/production')
    def produce(p:str,sid:str,body:Production):
        check(p,service.get(p,sid),'script')
        return service.produce(p,sid,body.version,body.idempotency_key,body.assignee)

    @app.get(root+'/production')
    def tasks(p:str): return export_safe(visible(p,store.list(p,'production'),'production'))

    @app.put(root+'/production/{tid}')
    def task(p:str,tid:str,body:TaskUpdate):
        check(p,store.get(p,'production',tid),'production')
        check(p,body.model_dump())
        return service.update_task(p,tid,**body.model_dump())

    @app.get(root+'/product-facts')
    def facts(p:str): return service.facts(p)

    @app.put(root+'/product-facts')
    def save_facts(p:str,body:FactsRevision): return service.save_facts(p,body.expected_version,body.model_dump(exclude={'expected_version'}))
