"""User-facing readiness derived from durable source facts, never frame totals."""
from pathlib import Path

from .evidence_policy import transcript_policy
from .store import NotFound


def get_readiness(store, project_id, asset_id):
    return readiness_for_asset(store, project_id, store.get(project_id, 'asset', asset_id))


def readiness_context(store, project_id):
    """Load project evidence once for a whole list, avoiding per-card SQL scans."""
    observations = {}
    for row in store.list(project_id,'observation'):
        observations.setdefault(row.get('asset_id'),[]).append(row)
    manifests = {row['id']:row for row in store.list(project_id,'media_input_manifest')}
    indexes = {}
    with store.connection() as db:
        for row in db.execute('SELECT source_record_id,index_state FROM knowledge_chunk WHERE project_id=? AND source_kind=? GROUP BY source_record_id,index_state',(project_id,'asset')):
            indexes.setdefault(row['source_record_id'],set()).add(row['index_state'])
    speech_jobs={}
    for job in store.jobs(project_id):
        if job['kind']=='transcription' and job['state'] in {'queued','running'}:
            for aid in job['payload'].get('asset_ids',[]):speech_jobs.setdefault(aid,job['state'])
    return {'observations':observations,'manifests':manifests,'indexes':indexes,'speech_jobs':speech_jobs}


def readiness_for_asset(store, project_id, asset, context=None):
    context = context if context is not None else readiness_context(store,project_id)
    meta = asset.get('metadata') or {}
    inspection = asset.get('inspection', 'pending')
    file_state = 'ready' if inspection == 'ready' else 'failed' if inspection == 'failed' else 'pending'
    if asset.get('path') and not Path(asset['path']).is_file(): file_state = 'missing'
    file_labels = {'ready':'原片可播放' if asset.get('media_type') == 'video' else '图片可查看',
                   'failed':'文件处理失败', 'missing':'原文件缺失', 'pending':'文件处理中'}
    policy = transcript_policy(asset)
    provenance = meta.get('transcription') or {}
    execution = policy['execution_state']
    active_speech=context.get('speech_jobs',{}).get(asset['id'])
    attempt = meta.get('transcription_attempt') or {}
    if not policy['allowed'] and attempt.get('execution_state') == 'failed': execution = 'failed'
    if asset.get('media_type') != 'video':
        transcript_state, transcript_label = 'not_applicable', '图片无需台词'
    elif not policy['allowed'] and active_speech in {'queued','running'}:
        transcript_state, transcript_label = active_speech, '台词等待识别' if active_speech=='queued' else '台词识别中'
    elif execution == 'failed':
        transcript_state, transcript_label = 'failed', '台词识别失败'
    elif execution == 'running':
        transcript_state, transcript_label = 'running', '台词识别中'
    elif execution == 'succeeded' and (provenance.get('no_speech') is True or provenance.get('speech_detected') is False) and not policy['segments']:
        transcript_state, transcript_label = 'no_speech', '未检测到口播'
    elif policy['allowed']:
        transcript_state, transcript_label = 'ready', '台词可使用' if policy['word_exact'] else '台词已识别，未校对'
    elif execution == 'succeeded':
        transcript_state, transcript_label = 'needs_review', '台词需复核'
    else:
        transcript_state, transcript_label = 'pending', '台词待识别'
    observations = []
    for row in context['observations'].get(asset['id'],[]):
        manifest = context['manifests'].get(row.get('input_manifest_id'))
        if not manifest: continue
        if manifest.get('asset_sha256') != asset.get('sha256'): continue
        if manifest.get('execution_state')!='succeeded':continue
        candidates=row.get('observations',[]) if not row.get('observation_id') else [row]
        observations.extend(o for o in candidates if o.get('interpretation') == 'observed'
                            and o.get('execution_state')=='succeeded'
                            and o.get('claim_kind') in {'visual_observation', 'screen_text'} and o.get('support'))
    observations=list({o['observation_id']:o for o in observations}.values())
    visual_state = 'partial' if observations else 'pending'
    visual_label = '已有画面观察' if observations else '画面尚未分析'
    states = context['indexes'].get(asset['id'],set())
    search = 'semantic' if 'ready' in states else 'keyword' if states else 'pending'
    return {'file':{'state':file_state,'label':file_labels[file_state]},
            'transcript':{'state':transcript_state,'label':transcript_label,'revision':policy['transcript_revision']},
            'visual':{'state':visual_state,'label':visual_label,'observations':len(observations)},
            'search':{'state':search,'label':{'semantic':'可按内容查找','keyword':'可按文字查找','pending':'检索准备中'}[search]},
            'summary':' · '.join([file_labels[file_state],transcript_label,visual_label])}
