"""Build-time only: provision the MIT licensed official whisper.cpp/small runtime."""
import hashlib
import json
import platform
import shutil
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = 'v1.9.5'
COMMIT = 'd1be6fde11ac6e0407606b4e42fe72d34add8037'
MODEL_URL = 'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin'
MODEL_SHA256 = '1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b'
MACOS_DEPLOYMENT_TARGET = '12.0'


def digest(path):
    with path.open('rb') as stream: return hashlib.file_digest(stream,'sha256').hexdigest()


def needs_native_build(build,fingerprint=None):
    cache=build/'CMakeCache.txt'
    try:
        built_for=json.loads((build/'creative-build-host.json').read_text())
    except (OSError,ValueError):
        built_for=None
    target_host = fingerprint or host_fingerprint()
    cache_text = cache.read_text() if cache.is_file() else ''
    incompatible_macos = target_host.get('system') == 'Darwin' and (
        f'CMAKE_OSX_DEPLOYMENT_TARGET:STRING={MACOS_DEPLOYMENT_TARGET}' not in cache_text
        or 'GGML_ACCELERATE:BOOL=OFF' not in cache_text)
    return (not (build/'bin/whisper-cli').is_file() or not cache.is_file()
            or 'GGML_NATIVE:BOOL=ON' not in cache_text or incompatible_macos
            or built_for!=target_host)

def host_cpu():
    if platform.system()=='Darwin':
        try:
            return subprocess.check_output(['sysctl','-n','machdep.cpu.brand_string','machdep.cpu.features','machdep.cpu.leaf7_features'],text=True,stderr=subprocess.DEVNULL).strip()
        except (OSError,subprocess.SubprocessError):
            try:
                model=subprocess.check_output(['sysctl','-n','hw.model'],text=True,stderr=subprocess.DEVNULL).strip()
            except (OSError,subprocess.SubprocessError):
                model=platform.processor() or platform.machine()
            return model+' (CPU feature sysctls unavailable; native build for this host)'
    return platform.processor()

def host_fingerprint():
    return {'architecture':platform.machine(),'system':platform.system(),'cpu_requirements':host_cpu()}

def prepare():
    output=ROOT/'.state/asr'; output.mkdir(parents=True,exist_ok=True)
    source=ROOT/'.state/asr-source/whisper.cpp'; build=source.parent/'build'
    if not source.exists(): subprocess.run(['git','clone','--depth','1','--branch',VERSION,'https://github.com/ggml-org/whisper.cpp.git',str(source)],check=True)
    if subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()!=COMMIT: raise SystemExit('whisper.cpp source revision mismatch')
    cmake=shutil.which('cmake') or str(Path(__file__).resolve().parents[1]/'.venv/bin/cmake')
    fingerprint=host_fingerprint()
    if needs_native_build(build,fingerprint):
        flags=[]
        if platform.system()=='Darwin':
            flags += [f'-DCMAKE_OSX_DEPLOYMENT_TARGET={MACOS_DEPLOYMENT_TARGET}', '-DGGML_ACCELERATE=OFF']
        sdk=Path('/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk/usr/include/c++/v1')
        if platform.system()=='Darwin' and sdk.is_dir(): flags+=['-DCMAKE_CXX_FLAGS=-I'+str(sdk)]
        subprocess.run([cmake,'-S',str(source),'-B',str(build),'-DBUILD_SHARED_LIBS=OFF','-DGGML_METAL=OFF','-DGGML_NATIVE=ON','-DWHISPER_BUILD_TESTS=OFF','-DCMAKE_BUILD_TYPE=Release',*flags],check=True)
        subprocess.run([cmake,'--build',str(build),'--clean-first','--config','Release','-j','4','--target','whisper-cli'],check=True)
        (build/'creative-build-host.json').write_text(json.dumps(fingerprint,sort_keys=True))
    staged=output/'whisper-cli.new'
    shutil.copy2(build/'bin/whisper-cli',staged)
    staged.replace(output/'whisper-cli')
    model=output/'ggml-small.bin'
    if not model.is_file():
        temporary=model.with_suffix('.part');urllib.request.urlretrieve(MODEL_URL,temporary);temporary.rename(model)
    if digest(model)!=MODEL_SHA256: raise SystemExit('Official small model SHA256 mismatch')
    shutil.copy2(source/'LICENSE',output/'whisper.cpp-LICENSE.txt')
    license_path=output/'openai-whisper-LICENSE.txt'
    if not license_path.is_file(): urllib.request.urlretrieve('https://raw.githubusercontent.com/openai/whisper/main/LICENSE',license_path)
    mit=(source/'LICENSE').read_text().split('Permission is hereby',1)[1]
    notices='nlohmann/json 3.11.2 — Copyright (c) 2013-2022 Niels Lohmann\nMIT License\nPermission is hereby'+mit
    for name,marker in [('stb_vorbis.c','This software is available under 2 licenses'),('miniaudio.h','This software is available as a choice')]:
        text=(source/'examples'/name).read_text()
        notices+='\n\n'+name+'\n'+text[text.rindex(marker):]
    (output/'THIRD_PARTY_NOTICES.txt').write_text(notices)
    manifest={'version' :VERSION,'commit':COMMIT,**fingerprint,'cpu_build':'native-current-host','model_url':MODEL_URL,'model_sha256':digest(model),'binary_sha256':digest(output/'whisper-cli'),'license':'MIT','license_sha256':{p.name:digest(p) for p in [*output.glob('*LICENSE.txt'),output/'THIRD_PARTY_NOTICES.txt']}}
    if platform.system()=='Darwin':
        manifest.update(macos_deployment_target=MACOS_DEPLOYMENT_TARGET, accelerate=False)
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    from prepare_vad import prepare as prepare_vad
    prepare_vad(output)
    manifest = json.loads((output/'manifest.json').read_text())
    print(json.dumps(manifest,indent=2))
    return output

if __name__=='__main__': prepare()
