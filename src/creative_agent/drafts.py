"""Project-local drafts; conflicting windows keep separate recoverable copies."""
import hashlib
import json
import re
from typing import Literal
from pydantic import Field
from .contracts import Contract, EvidenceRef
from .store import Conflict, dumps, now, reject_secrets

class DraftParseContext(Contract):
    parse_id:str=Field(min_length=1)
    version:int=Field(ge=1)

class DraftRevisionContext(Contract):
    script_id:str=Field(min_length=1)
    version:int=Field(ge=1)

class DraftContent(Contract):
    analysisMode: Literal['directed','exploration','inspiration'] = 'inspiration'
    analysisKind: Literal['','video','spoken'] = ''
    discussionOnly: bool = False
    question: str = Field(default='',max_length=30000)
    selected: list[str] = Field(default_factory=list,max_length=200)
    selectedSegments: list[EvidenceRef] = Field(default_factory=list,max_length=200)
    ownAssetIds: list[str] = Field(default_factory=list,max_length=10000)
    useHistory: bool = False
    referenceIds: list[str] = Field(default_factory=list,max_length=200)
    activeGroups: list[str] = Field(default_factory=list,max_length=20)
    source: str = Field(default='',max_length=40)
    scopeSources: list[str] = Field(default_factory=list,max_length=10)
    dateBasis: Literal['published','imported','statistics'] = 'published'
    startDate: str = Field(default='',max_length=10)
    endDate: str = Field(default='',max_length=10)
    minSpend: str = Field(default='',max_length=40)
    minRoas: str = Field(default='',max_length=40)
    account: str = Field(default='',max_length=200)
    query: str = Field(default='',max_length=2000)
    discovery: bool = False
    pendingExample: dict | None = None
    parseContext: DraftParseContext | None = None
    revisionContext: DraftRevisionContext | None = None
    scriptDrafts: dict | None = None

class DraftWrite(Contract):
    expected_version: int = Field(ge=0)
    operation_id: str = Field(min_length=1,max_length=100)
    content: DraftContent

class Drafts:
    def __init__(self,store):self.store=store

    def list(self,p):
        rows=sorted(self.store.list(p,'task_draft'),key=lambda row:row['updated_at'],reverse=True)
        return {'drafts':[self.view(row) for row in rows], 'active_identity':next((r['identity'] for r in rows if r['content'].get('scriptDrafts') is None),None)}

    @staticmethod
    def view(row):return {k:v for k,v in row.items() if k!='recent_operations'}

    def save(self,p,identity,body):
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,79}',identity):raise ValueError('草稿标识无效')
        content=body.content.model_dump(mode='json');reject_secrets(content)
        if len(dumps(content).encode())>1500000:raise ValueError('草稿过长，请分开保存')
        for kind,ids in [('asset',content['selected']+content['ownAssetIds']+[r['asset_id'] for r in content['selectedSegments']]),('group',content['activeGroups']),('cmm_reference',content['referenceIds'])]:
            for rid in set(ids):self.store.get(p,kind,rid)
        if content.get('parseContext'):self.store.get(p,'parse',content['parseContext']['parse_id'])
        if content.get('revisionContext'):
            from .scripts import Scripts
            Scripts(self.store).version(p,content['revisionContext']['script_id'],content['revisionContext']['version'])
        cache=content.get('scriptDrafts')
        if cache is not None:
            if (set(cache)-{'active','drafts'} or not isinstance(cache.get('active'),str)
                    or not isinstance(cache.get('drafts'),dict) or len(cache['drafts'])>100):
                raise ValueError('脚本编辑草稿结构无效')
            def check_refs(value):
                if isinstance(value,dict):
                    if value.get('project_id',p)!=p:raise ValueError('脚本草稿不能引用其他项目')
                    if value.get('asset_id'):self.store.get(p,'asset',value['asset_id'])
                    if value.get('script_id'):self.store.get(p,'script',value['script_id'])
                    for item in value.values():check_refs(item)
                elif isinstance(value,list):
                    for item in value:check_refs(item)
            for draft in cache['drafts'].values():
                if not isinstance(draft,dict) or not isinstance(draft.get('rows'),list) or any(not isinstance(r,dict) for r in draft['rows']):
                    raise ValueError('脚本草稿缺少可编辑镜头')
                if draft.get('id'):self.store.get(p,'script',draft['id'])
                check_refs(draft)
        fingerprint=hashlib.sha256(dumps({'identity':identity,'content':content}).encode()).hexdigest()
        def identifier(key):return 'draft-'+hashlib.sha256((p+':'+key).encode()).hexdigest()
        with self.store.connection(True) as db:
            self.store._project(db,p)
            rows=[json.loads(r['data']) for r in db.execute("SELECT data FROM records WHERE project_id=? AND kind='task_draft'",(p,))]
            for row in rows:
                previous=row.get('recent_operations',{}).get(body.operation_id)
                if previous:
                    if previous!=fingerprint:raise Conflict('此草稿保存操作已用于其他内容')
                    return self.view(row)
            old=next((row for row in rows if row['identity']==identity),None)
            recovered=None
            if (old or {}).get('version',0)!=body.expected_version:
                recovered=identity
                identity='recovered-'+hashlib.sha256((p+identity+body.operation_id).encode()).hexdigest()[:24]
                old=None
            stamp=now()
            operations={**(old or {}).get('recent_operations',{}),body.operation_id:fingerprint}
            operations=dict(list(operations.items())[-16:])
            row={'id':old['id'] if old else identifier(identity),'project_id':p,'identity':identity,'content':content,'version':(old or {}).get('version',0)+1,'created_at':(old or {}).get('created_at',stamp),'updated_at':stamp,'recent_operations':operations}
            if recovered:row['recovered_from']=recovered
            elif old and old.get('recovered_from'):row['recovered_from']=old['recovered_from']
            if old:db.execute('UPDATE records SET data=?,updated_at=? WHERE id=? AND project_id=?',(dumps(row),stamp,row['id'],p))
            else:db.execute('INSERT INTO records VALUES(?,?,?,?,?,?)',(row['id'],p,'task_draft',dumps(row),stamp,stamp))
            return self.view(row)
