import hashlib,importlib.util,json
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('macos_worker_bundle',Path(__file__).parents[1]/'scripts/macos_worker_bundle.py')

def load():
    assert spec is not None and spec.loader is not None
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def fixture(root):
    app=root/'src-tauri/target/release/bundle/macos/Creative Agent.app';(app/'Contents/MacOS').mkdir(parents=True)
    source=root/'.state/worker-dir-dist/creative-worker';(source/'_internal/asr').mkdir(parents=True);(source/'_internal/asr/model').write_bytes(b'model')
    launcher=root/'src-tauri/binaries/creative-worker-x86_64-apple-darwin';launcher.parent.mkdir(parents=True,exist_ok=True);launcher.write_bytes(b'launcher')
    (launcher.parent/'worker-bundle.json').write_text(json.dumps({'format':'onedir','launcher_sha256':hashlib.sha256(b'launcher').hexdigest()}))
    return app,source,launcher

def test_runtime_is_bundled_beside_launcher_and_old_files_are_replaced(tmp_path):
    app,source,_=fixture(tmp_path);old=app/'Contents/MacOS/_internal';old.mkdir();(old/'obsolete').write_text('old')
    result=load().finalize_bundle(tmp_path)
    assert result['format']=='onedir' and (app/'Contents/MacOS/creative-worker').read_bytes()==b'launcher'
    assert (old/'asr/model').read_bytes()==b'model' and not (old/'obsolete').exists()

def test_stale_launcher_or_escaping_symlink_cannot_be_packaged(tmp_path):
    app,source,launcher=fixture(tmp_path);launcher.write_bytes(b'stale')
    with pytest.raises(ValueError,match='启动'):load().finalize_bundle(tmp_path)
    launcher.write_bytes(b'launcher');(source/'_internal/outside').symlink_to('/etc/hosts')
    with pytest.raises(ValueError,match='外部'):load().finalize_bundle(tmp_path)
    assert not (app/'Contents/MacOS/_internal').exists()

def test_legacy_onefile_or_absent_manifest_keeps_existing_bundle(tmp_path):
    assert load().finalize_bundle(tmp_path)['format']=='onefile'

def test_runtime_data_lives_in_resources_with_an_internal_relative_link(tmp_path):
    app,_,_=fixture(tmp_path);load().finalize_bundle(tmp_path)
    internal=app/'Contents/MacOS/_internal'
    assert internal.is_symlink()
    assert internal.resolve().is_relative_to(app/'Contents/Resources')

def test_macos_bootloader_can_read_runtime_from_frameworks(tmp_path):
    app,_,_=fixture(tmp_path);load().finalize_bundle(tmp_path)
    assert (app/'Contents/Frameworks/asr/model').read_bytes()==b'model'
