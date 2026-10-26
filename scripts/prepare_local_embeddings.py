"""Build-time pinned offline asset provisioning. Customers never run downloads."""
from pathlib import Path
import hashlib,json,shutil,urllib.request
ROOT=Path(__file__).resolve().parents[1]
CONFIG=ROOT/'resources/local-embeddings'

def prepare(destination=None):
 target=Path(destination) if destination else ROOT/'.state/creative-loop-054/models/bge-small-zh-v1.5'
 target.mkdir(parents=True,exist_ok=True)
 manifest=json.loads((CONFIG/'manifest.json').read_text())
 revision=manifest['revision'];base=f'https://huggingface.co/Qdrant/bge-small-zh-v1.5/resolve/{revision}/'
 for name,expected in manifest['files'].items():
  if Path(name).name!=name:raise ValueError('Invalid fixed asset name')
  path=target/name
  if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==expected:continue
  if name in {'LICENSE','source_revision.json'}:data=(CONFIG/name).read_bytes()
  else:
   with urllib.request.urlopen(base+name,timeout=180) as response:data=response.read()
  if hashlib.sha256(data).hexdigest()!=expected:raise ValueError('Model resource integrity check failed: '+name)
  part=target/(name+'.partial');part.write_bytes(data);part.replace(path)
 shutil.copy2(CONFIG/'manifest.json',target/'manifest.json')
 print('Pinned Chinese model files verified; no model inference or customer data sent.')
 return target
if __name__=='__main__':prepare()
