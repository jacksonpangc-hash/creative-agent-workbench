"""Produce self-contained store submissions and a fixed-ID internal unpacked build.
Never copies .state, development private keys, tests or backend source.
"""
import argparse
import json
import shutil
import zipfile
from pathlib import Path


def package(kind, output):
    source = Path(__file__).resolve().parent
    manifest = json.loads((source/'manifest.json').read_text())
    config = json.loads((source/'release-config.json').read_text())
    if kind == 'development': manifest['key'] = config['development_public_key']
    else: manifest.pop('key',None)
    output = Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    destination = output/f'creative-collector-{kind}-{manifest["version"]}'
    destination.mkdir(exist_ok=True)
    names = ['popup.html','popup.js','connection.js','extract.js','native.js','service-worker.js']
    for name in names: shutil.copy2(source/name,destination/name)
    shutil.copytree(source/'icons',destination/'icons',dirs_exist_ok=True)
    (destination/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    archive = destination.with_suffix('.zip')
    # version dots are not a path extension to replace.
    archive = output/(destination.name+'.zip')
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as zip:
        for path in sorted(destination.rglob('*')):
            if path.is_file(): zip.write(path,path.relative_to(destination))
    return {'kind':kind,'version':manifest['version'],'directory':str(destination),'zip':str(archive),'store_id':config.get(kind+'_store_id'),'state':'internal_unpacked' if kind=='development' else 'ready_for_store_submission'}


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--kind',choices=['chrome','edge','development','all'],default='all');args=parser.parse_args()
    kinds=['chrome','edge','development'] if args.kind=='all' else [args.kind]
    print(json.dumps([package(kind,args.output) for kind in kinds],ensure_ascii=False,indent=2))
