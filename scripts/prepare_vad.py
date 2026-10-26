"""Build-time provisioning of the official whisper.cpp v1.9.5 VAD asset.

No customer media, credentials or model calls. Does not rebuild ASR or switch engines.
"""
import hashlib
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VAD_NAME = 'ggml-silero-v6.2.0.bin'
VAD_REPOSITORY_REVISION = '9ffd54a1e1ee413ddf265af9913beaf518d1639b'
VAD_URL = f'https://huggingface.co/ggml-org/whisper-vad/resolve/{VAD_REPOSITORY_REVISION}/{VAD_NAME}'
VAD_SHA256 = '2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987'
LICENSE_REVISION = 'be95df9152c0d7618fa1edfeb296fc3dae32376f'
LICENSE_URL = f'https://raw.githubusercontent.com/snakers4/silero-vad/{LICENSE_REVISION}/LICENSE'
LICENSE_SHA256 = '2e63e9a38b6e8fc0c7bc37ce174caca1862870856c6daf5697cfb785e925520b'
WHISPER_COMMIT = 'd1be6fde11ac6e0407606b4e42fe72d34add8037'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verified_download(url, target, expected):
    if target.is_file() and digest(target) == expected:
        return
    temporary = target.with_suffix('.download')
    try:
        with urllib.request.urlopen(url, timeout=90) as response, temporary.open('wb') as output:
            while block := response.read(1024 * 1024):
                output.write(block)
        if digest(temporary) != expected:
            raise ValueError('Official VAD resource hash mismatch: ' + target.name)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def prepare(output=None):
    directory = Path(output) if output is not None else ROOT / '.state/asr'
    manifest_path = directory / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('version') != 'v1.9.5' or manifest.get('commit') != WHISPER_COMMIT:
        raise ValueError('VAD asset is pinned to the shipped whisper.cpp v1.9.5 revision')
    verified_download(VAD_URL, directory / VAD_NAME, VAD_SHA256)
    verified_download(LICENSE_URL, directory / 'silero-vad-LICENSE.txt', LICENSE_SHA256)
    manifest['vad'] = {'name': 'silero-v6.2.0', 'filename': VAD_NAME, 'sha256': VAD_SHA256,
        'repository_revision': VAD_REPOSITORY_REVISION, 'model_url': VAD_URL,
        'license': 'MIT', 'license_url': LICENSE_URL, 'license_revision': LICENSE_REVISION,
        'license_sha256': LICENSE_SHA256, 'compatible_whisper_commit': WHISPER_COMMIT}
    manifest.setdefault('license_sha256', {})['silero-vad-LICENSE.txt'] = LICENSE_SHA256
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest['vad']


if __name__ == '__main__':
    print(json.dumps(prepare(), indent=2))
