"""Bundle the exact current host/architecture; do not cross-label a host binary."""
import argparse
import hashlib
import io
import json
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

root=Path(__file__).resolve().parents[1]
from package_config import commercial_inputs, media_inputs

parser=argparse.ArgumentParser()
parser.add_argument('--commercial',action='store_true')
parser.add_argument('--commercial-config',type=Path)
parser.add_argument('--single-file',action='store_true',help='Keep the legacy single-file format')
parser.add_argument('--directory',action='store_true',help='Mac directory runtime avoids unpacking large models on every launch')
parser.add_argument('--incremental',action='store_true',help='Reuse PyInstaller dependency cache after a targeted source fix')
args=parser.parse_args()
if args.directory and args.single_file:parser.error('请选择一种封装格式')
args.directory=args.directory or (sys.platform=='darwin' and not args.single_file)
if args.directory and sys.platform!='darwin':parser.error('目录封装当前只验收Mac；其他平台沿用单文件')
dist=root/('.state/worker-dir-dist' if args.directory else '.state/worker-dist')
if args.commercial_config and not args.commercial:parser.error('--commercial-config 需要 --commercial')
if args.commercial:
    sys.path.insert(0,str(root/'src'))
    from creative_agent.knowledge_cases import require_reviewed_cases
    try:require_reviewed_cases(root/'src/creative_agent/creative_cases')
    except ValueError as exc:parser.error(str(exc))
try:public= commercial_inputs(root,args.commercial_config) if args.commercial else None
except ValueError as exc:parser.error(str(exc))
try:media_runtime=media_inputs(root)
except ValueError as exc:parser.error(str(exc))
asr=root/'.state/asr'
required=['whisper-cli','ggml-small.bin','manifest.json','THIRD_PARTY_NOTICES.txt','whisper.cpp-LICENSE.txt','openai-whisper-LICENSE.txt','ggml-silero-v6.2.0.bin','silero-vad-LICENSE.txt']
if any(not (asr/name).is_file() for name in required):raise SystemExit('缺少已准备的本地口播资源；请先显式运行 scripts/prepare_asr.py，本脚本不自动下载模型')
manifest=json.loads((asr/'manifest.json').read_text())
for name,key in [('whisper-cli','binary_sha256'),('ggml-small.bin','model_sha256')]:
    with (asr/name).open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    if digest!=manifest[key]:raise SystemExit('内置口播资源完整性检查失败：'+name)
for name,key in [('ggml-silero-v6.2.0.bin','sha256'),('silero-vad-LICENSE.txt','license_sha256')]:
    if hashlib.sha256((asr/name).read_bytes()).hexdigest()!=manifest.get('vad',{}).get(key):raise SystemExit('VAD资源校验失败：'+name)
asr_archive=asr/'executable.zip'
with zipfile.ZipFile(asr_archive,'w',compression=zipfile.ZIP_DEFLATED) as archive:
    archive.write(asr/'whisper-cli','whisper-cli')
local_models=root/'.state/creative-loop-054/models/bge-small-zh-v1.5'
if not (local_models/'manifest.json').is_file():raise SystemExit('缺少已准备的中文离线模型；不能交付自动联网下载占位包')
local_args=['--add-data',str(root/'src/creative_agent/data/clipcat_patterns')+':creative_agent/data/clipcat_patterns','--add-data',str(local_models)+':models/bge-small-zh-v1.5',
    '--add-data',str(root/'src/creative_agent/browser-release-config.json')+':creative_agent',
    '--add-data',str(root/'src/creative_agent/creative_cases')+':creative_agent/creative_cases']
for dependency in ['fastembed','qdrant_client','onnxruntime','tokenizers']:
    local_args += ['--collect-all',dependency]
for distribution in ['fastembed','qdrant-client','onnxruntime','tokenizers']:
    local_args += ['--copy-metadata',distribution]
commercial_args=[] if public is None else ['--add-data',str(public/'commercial-license.json')+':creative_agent','--add-data',str(public/'commercial-release.json')+':creative_agent']
media_name='ffmpeg.exe' if sys.platform=='win32' else 'ffmpeg'
media_archive=media_runtime/'executable.zip'
with zipfile.ZipFile(media_archive,'w',compression=zipfile.ZIP_DEFLATED) as archive:
    archive.write(media_runtime/media_name,media_name)
media_args=['--add-data',str(media_archive)+':media-runtime',
            '--additional-hooks-dir',str(root/'scripts/pyinstaller-hooks')]
for name in ['manifest.json','corresponding-sources.zip','NOTICES.txt','ffmpeg-license.txt',
             'ffmpeg-version.txt','ffmpeg-buildconf.txt','FFmpeg-COPYING.GPLv2','x264-COPYING']:
    if (media_runtime/name).is_file():
        media_args += ['--add-data',str(media_runtime/name)+':media-runtime']
arch=platform.machine().lower()
if sys.platform=='darwin': triple={'x86_64':'x86_64-apple-darwin','arm64':'aarch64-apple-darwin'}[arch]
elif sys.platform=='win32' and arch in {'amd64','x86_64'}: triple='x86_64-pc-windows-msvc'
else: raise SystemExit('此打包脚本尚未验收当前系统/架构')
subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm',*([] if args.incremental else ['--clean']),('--onedir' if args.directory else '--onefile'),'--name','creative-worker',
    '--paths',str(root/'src'),*commercial_args,*media_args,*local_args,'--add-data',str(asr/'ggml-silero-v6.2.0.bin')+':asr','--add-data',str(asr/'silero-vad-LICENSE.txt')+':asr','--add-data',str(root/'src/creative_agent/fonts')+':creative_agent/fonts','--add-data',str(asr_archive)+':asr',
    '--add-data',str(asr/'THIRD_PARTY_NOTICES.txt')+':asr',
    '--add-data',str(asr/'ggml-small.bin')+':asr','--add-data',str(asr/'manifest.json')+':asr',
    '--add-data',str(asr/'whisper.cpp-LICENSE.txt')+':asr','--add-data',str(asr/'openai-whisper-LICENSE.txt')+':asr','--collect-submodules','imageio_ffmpeg','--collect-submodules','keyring.backends','--collect-data','keyring',
    '--collect-submodules','reportlab','--collect-submodules','yt_dlp','--collect-data','yt_dlp','--copy-metadata','yt-dlp','--distpath',str(dist),
    '--workpath',str(root/'.state/worker-build'),'--specpath',str(root/'.state'),str(root/'scripts/worker_entry.py')],check=True,cwd=root)
suffix='.exe' if sys.platform=='win32' else ''
from PyInstaller.archive.readers import CArchiveReader
frozen = None if args.directory else CArchiveReader(dist/f'creative-worker{suffix}')
for name, expected in [('whisper-cli',manifest['binary_sha256']),
                       ('ggml-silero-v6.2.0.bin',manifest['vad']['sha256']),
                       ('silero-vad-LICENSE.txt',manifest['vad']['license_sha256'])]:
    def payload_file(relative):
        return (dist/'creative-worker'/'_internal'/relative).read_bytes() if args.directory else frozen.extract(relative)
    if name=='whisper-cli':
        with zipfile.ZipFile(io.BytesIO(payload_file('asr/executable.zip'))) as archive:payload=archive.read('whisper-cli')
    else:payload=payload_file('asr/'+name)
    if hashlib.sha256(payload).hexdigest()!=expected:raise SystemExit('冻结程序改变了已核验的口播资源，拒绝交付：'+name)
destination=root/'src-tauri/binaries'/f'creative-worker-{triple}{suffix}'
destination.parent.mkdir(parents=True,exist_ok=True)
launcher=dist/'creative-worker'/f'creative-worker{suffix}' if args.directory else dist/f'creative-worker{suffix}'
shutil.copy2(launcher,destination)
(destination.parent/'worker-bundle.json').write_text(json.dumps({'format':'onedir' if args.directory else 'onefile','launcher_sha256':hashlib.sha256(destination.read_bytes()).hexdigest()},indent=2))
print('Worker packaged for',triple)
