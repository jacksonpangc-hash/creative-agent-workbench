"""Only necessary authorized material enters generation; audit objects stay local."""
from copy import deepcopy
import re


def infer_intent(question, *, selected=False, scripts=False, revision=False):
 if re.search(r'换(?:一批|批|些).*参考|新的?方向|换个?方向|换(?:商品|产品|规格)',question):return 'new_direction'
 if revision:return 'revise_current'
 if scripts:return 'new_script'
 return 'analyze_selected' if selected else 'general_discussion'


def model_context(snapshot):
 return {k:deepcopy(snapshot.get(k)) for k in ('intent','query','product_id','sku_id','reference_patterns','own_footage','preferences','limitations','context_snapshot_id','methodology')}


def provenance_summary(snapshot, rows):
 return {'local_references':len({r.get('source_record_id') for r in snapshot.get('reference_patterns',[]) if r.get('source_record_id')}),
         'new_external_queries':0,
         'own_clips':sum(len(r.get('own_references',[])) for r in rows),
         'new_shots':sum(bool(r.get('reshoot','').strip()) for r in rows)}
