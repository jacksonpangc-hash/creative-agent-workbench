"""Collect installed dependency declarations; this is an inventory, not a license clearance."""
from importlib import metadata
import json
from pathlib import Path
import subprocess

root=Path(__file__).resolve().parents[1]
python=[]
for dist in metadata.distributions():
    m=dist.metadata
    if m['Name']=='creative-material-agent': continue
    license=m.get('License-Expression') or m.get('License') or 'UNDECLARED'
    python.append({'name':m['Name'],'version':dist.version,'license':license[:300],
                   'project_urls':m.get_all('Project-URL',[])})
lock=json.loads((root/'package-lock.json').read_text())
node=[]
for path,entry in lock['packages'].items():
    if not path: continue
    pkg=root/path/'package.json'
    info=json.loads(pkg.read_text()) if pkg.exists() else {}
    node.append({'name':info.get('name',path.split('node_modules/')[-1]),'version':entry['version'],
        'license':info.get('license',entry.get('license','UNDECLARED')),'repository':info.get('repository',{})})
media_root=root/'.state/media-runtime'
media_manifest=json.loads((media_root/'manifest.json').read_text()) if (media_root/'manifest.json').is_file() else {}
ffmpeg={key:media_manifest.get(key) for key in ('architecture','binary_sha256','source_archive_sha256','nonfree')}
ffmpeg.update(state='prepared_build_resource' if media_manifest else 'not_prepared',
    build=(media_root/'ffmpeg-version.txt').read_text().splitlines()[:4] if (media_root/'ffmpeg-version.txt').is_file() else [],
    license=(media_root/'ffmpeg-license.txt').read_text() if (media_root/'ffmpeg-license.txt').is_file() else 'UNDECLARED',
    wheel_executable_distributed=False)
result={'note':'Installed build inventory. Dev and transitive packages included. Not legal clearance.',
    'python':sorted(python,key=lambda x:x['name'].lower()),'npm':sorted(node,key=lambda x:x['name']),
    'ffmpeg':ffmpeg}
def redact_build_home(value):
    if isinstance(value,dict):return {k:redact_build_home(v) for k,v in value.items()}
    if isinstance(value,list):return [redact_build_home(v) for v in value]
    if isinstance(value,str):return value.replace(str(Path.home()),'[BUILD_HOME]')
    return value
result=redact_build_home(result)
result['path_redactions']='Personal build home paths replaced with [BUILD_HOME]; exact flags remain in private binary distribution materials.'
(root/'docs/dependency-inventory.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(len(python),'Python distributions;',len(node),'npm packages inventoried')
