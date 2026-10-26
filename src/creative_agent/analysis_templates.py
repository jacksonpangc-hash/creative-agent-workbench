"""Versioned analysis fields, distinct from reusable chat instructions."""
from typing import Literal
from copy import deepcopy
import hashlib
from pydantic import Field
from .contracts import Contract
from .scripts import Scripts


class AnalysisField(Contract):
    field_id:str=Field(min_length=1,max_length=100)
    name:str=Field(min_length=1,max_length=100)
    type:Literal['text','enum','list','number','evidence']='text'
    level:Literal['overall','shot']='overall'
    basis:Literal['observed','inferred','recommended']='inferred'
    description:str=Field(default='',max_length=2000)
    unit:str=''
    options:list[str]=Field(default_factory=list,max_length=100)
    required:bool=False


class Templates:
    def __init__(self,store):self.store=store
    def save(self,p,body,record_id=None,expected_version=None):
        fields=body.get('fields')
        if not body.get('title','').strip() or not isinstance(fields,list) or not 1<=len(fields)<=40:raise ValueError('模板需要名称和1–40个字段')
        if any(not isinstance(f,str) or not f.strip() for f in fields) or len(set(fields))!=len(fields):raise ValueError('解析字段不能为空或重复')
        definitions=body.get('definitions')
        if definitions is None:
            definitions=[{'field_id':'field-'+hashlib.sha256(f.encode()).hexdigest()[:16],'name':f} for f in fields]
        definitions=[AnalysisField.model_validate(d).model_dump() for d in definitions]
        if [d['name'] for d in definitions]!=fields or len({d['field_id'] for d in definitions})!=len(definitions):raise ValueError('字段定义与顺序不一致')
        data={'title':body['title'],'fields':fields,'definitions':definitions,'template_type':'analysis','schema_version':'2.1'}
        if record_id:return Scripts(self.store)._write(p,'template',data,record_id,expected_version)
        identity='template-'+hashlib.sha256(__import__('json').dumps([p,data],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        return self.store.put_if_absent(p,'template',{**data,'version':1,'revisions':[]},identity)

    def cells(self,definitions,values):
        result={}
        for definition in definitions:
            value=values.get(definition['field_id'],values.get(definition['name']))
            state='insufficient' if value is None else 'available'
            basis=definition['basis']
            if isinstance(value,dict) and 'value' in value:
                basis=value.get('basis',basis);state=value.get('state',state);value=value['value']
            if basis not in {'observed','inferred','recommended'} or state not in {'available','not_observed','insufficient','not_applicable'}:raise ValueError('分析字段的依据或缺失状态无效')
            kind=definition['type']
            if value is not None:
                valid=(isinstance(value,str) if kind=='text' else isinstance(value,(int,float)) and not isinstance(value,bool) if kind=='number' else isinstance(value,list) if kind in {'list','evidence'} else value in definition['options'])
                if not valid:raise ValueError('分析字段类型不匹配：'+definition['name'])
            result[definition['field_id']]={'value':deepcopy(value),'basis':basis,'state':state,'unit':definition['unit']}
        return result
