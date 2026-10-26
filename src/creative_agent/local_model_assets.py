"""Install bundled, checksummed local assets into the current data directory."""
import json
import shutil
import sys
import tempfile
from pathlib import Path


def install_bundled_embeddings(root):
 root=Path(root)
 target=root/'models'/'bge-small-zh-v1.5'
 if target.is_dir():return target
 resources=Path(sys._MEIPASS) if hasattr(sys,'_MEIPASS') else Path(__file__).resolve().parents[2]/'.state'/'creative-loop-054'
 source=resources/'models'/'bge-small-zh-v1.5'
 if not source.is_dir():return None
 from .local_embeddings import LocalEmbeddings
 runtime=LocalEmbeddings(source)
 runtime._verify()
 target.parent.mkdir(parents=True,exist_ok=True)
 work=Path(tempfile.mkdtemp(prefix='.local-model-',dir=target.parent))
 try:
  shutil.copytree(source,work/'model')
  if not target.exists():(work/'model').rename(target)
 finally:shutil.rmtree(work)
 return target
