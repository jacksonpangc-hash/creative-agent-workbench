"""Exercise the frozen worker with real media and authenticated HTTP, without external model calls."""
import argparse
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import imageio_ffmpeg


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--binary',type=Path)
    parser.add_argument('--speech-fixture',type=Path,help='Local licensed speech fixture; validates bundled ASR without paid calls')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    metadata=root/'src-tauri/binaries/worker-bundle.json'
    directory=metadata.exists() and json.loads(metadata.read_text()).get('format')=='onedir'
    binary=(args.binary or (root/'.state/worker-dir-dist/creative-worker/creative-worker' if directory else root/'.state/worker-dist/creative-worker')).resolve()
    if sys.platform=='win32' and binary.suffix!='.exe':binary=binary.with_suffix('.exe')
    root=Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='creative-worker-smoke-') as folder:
        data=Path(folder)
        command=[str(binary),'--data-dir',str(data/'workspace')]
        def cli(*parts):
            return json.loads(subprocess.check_output([*command,*parts],text=True,timeout=120))
        p=cli('project','create','--name','Packaged worker smoke','--budget','10')
        video=data/'smoke.mp4'
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-loglevel','error','-y','-f','lavfi','-i','color=c=red:s=160x120:r=10:d=1','-c:v','libx264',str(video)],check=True,timeout=30)
        a=cli('ingest','--project',p['id'],'--file',str(video))
        assert a['metadata']['frame_count']==10
        assert 'path' not in a
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        token=secrets.token_urlsafe(32)
        proc=subprocess.Popen([*command,'serve','--no-open','--token-stdin','--port',str(port),'--ui-dir',str(root/'dist')],
            stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,start_new_session=os.name!='nt')
        try:
            proc.stdin.write((token+'\n').encode());proc.stdin.close()
            with httpx.Client(base_url=f'http://127.0.0.1:{port}',trust_env=False,timeout=5) as client:
                for _ in range(600):
                    if proc.poll() is not None:
                        raise RuntimeError('Frozen worker exited: '+proc.stderr.read().decode(errors='replace')[-2000:])
                    try:
                        response=client.get('/api/projects',headers={'Authorization':'Bearer '+token})
                        if response.status_code==200:break
                    except httpx.HTTPError: pass
                    time.sleep(.2)
                else:raise RuntimeError('Frozen worker did not start in 120 seconds')
                assert response.json()[0]['id']==p['id']
                catalog=client.get('/api/catalog',headers={'Authorization':'Bearer '+token}).json()
                for provider in ['openai','anthropic']:
                    models=next(v for v in catalog['providers'] if v['id']==provider)['models']
                    assert any(m['availability'].get('video_analysis',{}).get('available') for m in models), provider+' built-in video workflow missing'
                assert client.get('/api/projects').status_code==401
                state=client.get(f'/api/projects/{p["id"]}/state',headers={'Authorization':'Bearer '+token}).json()
                assert len(state['assets'])==1
                offline=client.get(f'/api/projects/{p["id"]}/patterns?limit=1',headers={'Authorization':'Bearer '+token},timeout=10)
                assert offline.status_code==200 and offline.json()['total']==647
                assert offline.json()['items'][0]['metrics_status']=='not_provided'
                ticket=client.post(f'/api/projects/{p["id"]}/media-ticket/{a["id"]}',headers={'Authorization':'Bearer '+token},json={}).json()
                assert '/api/media-streams/' in ticket['path'] and token not in ticket['path']
                ranged=client.get(ticket['path'],headers={'Range':'bytes=0-7'})
                assert ranged.status_code==206 and len(ranged.content)==8
                draft=client.put(f'/api/projects/{p["id"]}/drafts/inspiration',headers={'Authorization':'Bearer '+token},json={'expected_version':0,'operation_id':'frozen-draft','content':{'question':'冻结程序草稿验收','parseContext':None,'revisionContext':None}})
                assert draft.status_code==200,draft.text

                assert state['briefs']==[]
                assert client.get(f'/api/projects/{p["id"]}/briefs',headers={'Authorization':'Bearer '+token}).json()==[]
                scoped=client.post(f'/api/projects/{p["id"]}/search',headers={'Authorization':'Bearer '+token},json={
                    'scope':{'project_id':p['id'],'discovery':True,'date_basis':'published','start_date':'2000-01-01','group_ids':[]}})
                assert scoped.status_code==200 and scoped.json()==[]  # No invented publication date.
                poster=client.get(f'/api/projects/{p["id"]}/assets/{a["id"]}/file?poster=true',headers={'Authorization':'Bearer '+token})
                assert poster.status_code==200 and poster.content.startswith(b'\xff\xd8')
                # New browser uploads must acknowledge durable bytes before media processing.
                receipt=client.post(f'/api/projects/{p["id"]}/assets?background=true&source_kind=raw',
                    headers={'Authorization':'Bearer '+token},files={'file':('background.mp4',video.read_bytes(),'video/mp4')}).json()
                assert receipt['inspection']=='pending' and 'processing_job' in receipt
                for _ in range(100):
                    refreshed=client.get(f'/api/projects/{p["id"]}/state',headers={'Authorization':'Bearer '+token}).json()
                    processed=next(item for item in refreshed['assets'] if item['id']==receipt['id'])
                    if processed['inspection']=='ready':break
                    time.sleep(.1)
                else:raise RuntimeError('Packaged background media processing did not finish')
                assert len(refreshed['assets'])==2
                # Exercise real frozen document exporters and bundled font data.
                headers={'Authorization':'Bearer '+token}
                script=client.post(f'/api/projects/{p["id"]}/scripts',headers=headers,json={
                    'title':'冻结程序导出验收','objective':'检查真实文档与中文字体',
                    'rows':[{'start_ms':0,'end_ms':3000,'dialogue':'把常用物品放在顺手的位置。','visual':'桌面整理近景','reshoot':'补拍整理动作'}]}).json()
                pdf=client.get(f'/api/projects/{p["id"]}/scripts/{script["id"]}/export?format=pdf&version=1',headers=headers,timeout=30)
                assert pdf.status_code==200 and pdf.content.startswith(b'%PDF') and b'/FontFile2' in pdf.content
                for fmt in ['docx','xlsx','md','json']:
                    exported=client.get(f'/api/projects/{p["id"]}/scripts/{script["id"]}/export?format={fmt}&version=1',headers=headers)
                    assert exported.status_code==200 and exported.content,fmt+' frozen export failed'
                if args.speech_fixture:
                    speech=data/'speech.mp4'
                    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-loglevel','error','-y','-f','lavfi','-i',
                        'color=c=black:s=160x120:r=10','-i',str(args.speech_fixture.resolve()),'-c:v','libx264',
                        '-c:a','aac','-shortest',str(speech)],check=True,timeout=30)
                    headers={'Authorization':'Bearer '+token}
                    receipt=client.post(f'/api/projects/{p["id"]}/assets?background=true',headers=headers,
                        files={'file':('speech.mp4',speech.read_bytes(),'video/mp4')},timeout=30).json()
                    for _ in range(300):
                        assets=client.get(f'/api/projects/{p["id"]}/state',headers=headers).json()['assets']
                        prepared=next(item for item in assets if item['id']==receipt['id'])
                        if prepared['inspection']=='ready':break
                        time.sleep(.2)
                    else:raise RuntimeError('Frozen speech media processing did not complete')
                    response=client.post(f'/api/projects/{p["id"]}/transcriptions',headers=headers,json={
                        'asset_ids':[receipt['id']],'idempotency_key':'frozen-asr-smoke'},timeout=30)
                    assert response.status_code==200,response.text
                    job_id=response.json()['id']
                    for _ in range(600):
                        state=client.get(f'/api/projects/{p["id"]}/state',headers=headers,timeout=10).json()
                        job=next(j for j in state['jobs'] if j['id']==job_id)
                        if job['state'] in {'succeeded','failed','needs_attention'}:break
                        time.sleep(1)
                    assert job['state']=='succeeded',job
                    spoken=next(a for a in state['assets'] if a['id']==receipt['id'])['metadata']
                    assert spoken['transcript'].strip() and spoken['subtitles']
                    assert spoken['transcription']['method']=='whisper.cpp-local'
                    assert spoken['transcription']['model_sha256']=='1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b'
                    assert all(0<=t['start_ms']<t['end_ms']<=spoken['duration_ms'] for t in spoken['subtitles'])
                    corrected=client.post(f'/api/projects/{p["id"]}/assets/{receipt["id"]}/subtitles',
                        headers=headers,json={'text':'1\n00:00:00,100 --> 00:00:00,900\nUser supplied correction\n'})
                    assert corrected.status_code==200,corrected.text
                    corrected_metadata=corrected.json()['metadata']
                    assert corrected_metadata['transcript_source']=='user_import'
                    assert not corrected_metadata.get('transcription')
                    assert corrected_metadata['transcript_history'][-1]['transcription']['method']=='whisper.cpp-local'
                    print('PASS: frozen bundled ASR speech, timestamps and model hash; zero paid calls')
                assert client.get('/').status_code==200
                assert client.get('/api/projects',headers={'Authorization':'Bearer '+token,'Origin':'https://example.org'}).status_code==403
        finally:
            if os.name=='nt':
                if proc.poll() is None:
                    subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
            else:
                try:os.killpg(proc.pid,signal.SIGTERM)
                except ProcessLookupError:pass
            proc.wait(timeout=15)
    print('PASS: frozen worker CLI, real FFmpeg frames/poster, background upload/processing, GPT/Claude video workflow catalog, UI resources, API authentication and origin guard. No external model calls.')


if __name__=='__main__':main()
