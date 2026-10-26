"""Export an explicit source allowlist from a commit, without Git history/data.

This is a file-boundary and obvious-secret check, not legal/security certification.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = {'.github', 'src', 'ui', 'tests', 'schemas', 'scripts', 'skills',
               'src-tauri', 'browser-extension', 'deploy', 'resources'}
FILES = {'LICENSE', 'THIRD_PARTY_NOTICES.md', 'README.md', 'PRODUCT.md', 'DESIGN.md',
         'package.json', 'package-lock.json', 'pyproject.toml', 'uv.lock',
         'tsconfig.json', 'vite.config.ts', 'index.html', '.gitignore', 'docs/dependency-inventory.json'}
DOCS = {'architecture', 'browser-extension', 'creative-context', 'data-sources',
        'distribution', 'licensing', 'privacy-and-ownership', 'video-understanding',
        'commercial-license', 'portability', 'api', 'providers', 'local-transcription'}


def included(name):
    path = Path(name)
    if any(part in {'.state', '.git', '.agents', '.superpowers', '.playwright-cli',
                    'node_modules', '__pycache__', 'target', 'binaries'} for part in path.parts):
        return False
    if path.suffix.lower() in {'.log', '.sqlite', '.sqlite3', '.mp4', '.mov', '.pem', '.key'}:
        return False
    return (name in FILES or path.parts[0] in DIRECTORIES
            or (path.parent.as_posix() == 'docs' and path.suffix == '.md' and path.stem in DOCS)
            or name == 'docs/creative-loop-054/user-guide.md'
            or name in {'docs/releases/0.5.5.md', 'docs/releases/0.5.5-acceptance.json'})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--commit', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    commit = subprocess.check_output(['git', 'rev-parse', args.commit+'^{commit}'], cwd=ROOT, text=True).strip()
    paths = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', commit], cwd=ROOT, text=True).splitlines()
    entries = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Never overwrite a previously delivered archive.
    with zipfile.ZipFile(args.output, 'x', compression=zipfile.ZIP_DEFLATED) as output:
        for name in paths:
            if not included(name):
                continue
            data = subprocess.check_output(['git', 'show', commit+':'+name], cwd=ROOT)
            if not data.startswith(b'\x00'):
                if str(Path.home()).encode() in data:
                    raise ValueError('Source contains a personal absolute path: '+name)
                if re.search(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|sk-[A-Za-z0-9_-]{32,}', data):
                    raise ValueError('Source contains a possible secret: '+name)
            output.writestr('CreativeAgent-source/'+name, data)
            entries.append({'path': name, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)})
        manifest = {'source_commit': commit, 'license': 'AGPL-3.0-only',
            'git_history_included': False, 'runtime_assets_included': False,
            'allowlist_directories': sorted(DIRECTORIES), 'allowlist_files': sorted(FILES),
            'excluded': ['private development records', 'screenshots', 'customer media and transcripts',
                         'supplier responses and bills', 'credentials', 'local tools and caches', 'Git history'],
            'review_scope': 'Explicit file allowlist plus obvious secret and personal home path checks; not legal approval',
            'files': entries}
        output.writestr('CreativeAgent-source/PUBLIC_SOURCE_MANIFEST.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    print(json.dumps({'source_commit': commit, 'files': len(entries), 'output': str(args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
