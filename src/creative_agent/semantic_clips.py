"""Scene semantics from timed observed descriptions/transcripts, never pixel similarity."""
import hashlib
import json
import math
from copy import deepcopy

from .store import NotFound


class SemanticClips:
    def __init__(self,store): self.store=store

    def evidence(self,p,scene):
        asset=self.store.get(p,'asset',scene['asset_id'])
        start,end=scene['start_ms'],scene['end_ms']
        from .evidence_policy import transcript_policy
        from .knowledge_index import KnowledgeIndex
        policy = transcript_policy(asset, purpose='retrieve')
        parts = []
        if policy['allowed']:
            parts.extend({'type': 'transcript', **deepcopy(row), 'source': policy['source'],
                          'review_state': policy['review_state'], 'quality_state': policy['quality_state']}
                         for row in policy['segments'] if row['start_ms'] < end and row['end_ms'] > start)
        index = getattr(self.store, 'knowledge_index', None) or KnowledgeIndex(self.store, embeddings=None)
        try:
            observed = index._observed_parts(index._state(p, 'asset', asset['id']))
        except (ValueError, OSError):
            observed = []
        parts.extend(row for row in observed if row.get('start_ms') is not None
                     and row['start_ms'] < end and row['end_ms'] > start)
        text = '\n'.join(row['text'] for row in parts)
        identity = json.dumps(parts, ensure_ascii=False, sort_keys=True)
        return parts, text, hashlib.sha256(identity.encode()).hexdigest()

    def index(self,p,asset_ids,client=None):
        if client is None:
            from .knowledge_index import KnowledgeIndex
            index = getattr(self.store, 'knowledge_index', None) or KnowledgeIndex(self.store)
            for identifier in dict.fromkeys(asset_ids):
                self.store.get(p, "asset", identifier)
                index.enqueue(p, "asset", identifier)
            return {"asset_ids": list(dict.fromkeys(asset_ids)), "state": "queued", "local": True, "cost": "0"}
        ids=set(asset_ids)
        for aid in ids: self.store.get(p,'asset',aid)
        scenes=[s for s in self.store.list(p,'scene') if s['asset_id'] in ids]
        prepared=[(s,*self.evidence(p,s)) for s in scenes]
        if not prepared or any(not parts for _,parts,_,_ in prepared):
            raise ValueError('每个镜头需要带时间范围的字幕或画面解析，请先补齐证据')
        identity={'provider':client.binding.provider,'model':client.binding.model_id,'endpoint':client.binding.base_url or 'default','representation':'timed-text-v1'}
        count=0
        for scene,parts,text,digest in prepared:
            prior=scene.get('semantic',{})
            if prior.get('space')==identity and prior.get('content_hash')==digest: continue
            vector=client.embed(text)
            if not vector or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in vector) or sum(x*x for x in vector)<=0:
                raise ValueError('供应商返回无效镜头向量')
            fresh=self.store.get(p,'scene',scene['id'])
            fresh['semantic']={'space':identity,'content_hash':digest,'vector':vector,'evidence':parts}
            self.store.put(p,'scene',fresh,scene['id']);count+=1
        return self.store.put(p,'scene_embedding',{'asset_ids':list(ids),'indexed':count,'cached':len(prepared)-count,'space':identity})

    def valid(self,p,scene):
        semantic=scene.get('semantic',{})
        if not semantic or semantic.get('content_hash')!=self.evidence(p,scene)[2]: return None
        return semantic
