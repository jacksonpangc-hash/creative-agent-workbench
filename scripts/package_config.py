"""Validate only operator public configuration for commercial releases."""
import base64
import hashlib
import json
import platform
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def commercial_inputs(root,config_path):
    if config_path is None:raise ValueError('商业包必须提供运营方公钥和HTTPS授权服务配置')
    config=json.loads(Path(config_path).read_text())
    if set(config)!={'required','public_key','service_url'} or config['required'] is not True:raise ValueError('商业包配置只能包含强制授权、公钥和服务地址，不能携带私钥或密码')
    parsed=urlsplit(config['service_url'])
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:raise ValueError('授权服务必须使用HTTPS')
    try:Ed25519PublicKey.from_public_bytes(base64.b64decode(config['public_key']+'='*(-len(config['public_key'])%4),altchars=b'-_',validate=True))
    except (ValueError,TypeError) as exc:raise ValueError('授权公钥无效') from exc
    directory=Path(root)/'.state/license-public';directory.mkdir(parents=True,exist_ok=True)
    (directory/'commercial-license.json').write_text(json.dumps(config,sort_keys=True))
    (directory/'commercial-release.json').write_text('{"require_license":true}')
    return directory


def media_inputs(root):
    directory = Path(root) / '.state/media-runtime'
    binary = directory / ('ffmpeg.exe' if platform.system() == 'Windows' else 'ffmpeg')
    manifest = directory / 'manifest.json'
    if not binary.is_file() or not manifest.is_file():
        raise ValueError('缺少已核验的媒体程序，不能打包未经核验的替代文件')
    metadata = json.loads(manifest.read_text())
    license_path = directory / 'ffmpeg-license.txt'
    if (metadata.get('nonfree') is not False or not license_path.is_file()
            or 'not legally redistributable' in license_path.read_text().lower()):
        raise ValueError('媒体程序未通过再分发检查')
    if metadata.get('architecture') != platform.machine():
        raise ValueError('媒体程序架构与当前构建目标不一致')
    with binary.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != metadata.get('binary_sha256'):
            raise ValueError('媒体程序完整性检查失败')
    archive = directory / 'corresponding-sources.zip'
    if not archive.is_file() or not (directory / 'NOTICES.txt').is_file():
        raise ValueError('媒体程序的对应源码及分发材料不完整')
    with archive.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != metadata.get('source_archive_sha256'):
            raise ValueError('媒体程序对应源码完整性检查失败')
    return directory
