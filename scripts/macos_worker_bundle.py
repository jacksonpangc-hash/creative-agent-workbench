"""Stage a directory worker in the generated Mac app; never touch customer data."""
import hashlib
import json
import os
import subprocess
import shutil
from pathlib import Path


def finalize_bundle(root):
    root=Path(root).resolve()
    manifest=root/'src-tauri/binaries/worker-bundle.json'
    if not manifest.exists() or json.loads(manifest.read_text()).get('format')!='onedir':
        return {'format':'onefile','runtime_staged':False}
    metadata=json.loads(manifest.read_text())
    app=root/'src-tauri/target/release/bundle/macos/Creative Agent.app'
    if not app.resolve().is_relative_to(root/'src-tauri/target/release/bundle/macos'):
        raise ValueError('拒绝修改生成目录以外的应用')
    launcher=root/'src-tauri/binaries/creative-worker-x86_64-apple-darwin'
    if hashlib.sha256(launcher.read_bytes()).hexdigest()!=metadata['launcher_sha256']:
        raise ValueError('工作进程启动文件已变化，请重新打包')
    source=root/'.state/worker-dir-dist/creative-worker/_internal'
    if not source.is_dir():raise ValueError('缺少目录运行资源，请重新打包')
    for path in source.rglob('*'):
        if path.is_symlink() and not path.resolve().is_relative_to(source.resolve()):
            raise ValueError('运行资源包含指向外部目录的链接')
    code_dir=app/'Contents/MacOS'
    resource_dir=app/'Contents/Resources'
    if not code_dir.resolve().is_relative_to(app.resolve()) or not resource_dir.resolve().is_relative_to(app.resolve()):
        raise ValueError('拒绝向应用以外的目录写入运行资源')
    runtime=resource_dir/'worker-runtime'
    if runtime.exists():shutil.rmtree(runtime)
    shutil.copytree(source,runtime,symlinks=True)
    target=code_dir/'_internal'
    if target.is_symlink():target.unlink()
    elif target.exists():shutil.rmtree(target)
    target.symlink_to('../Resources/worker-runtime',target_is_directory=True)
    frameworks=app/'Contents/Frameworks'
    if frameworks.is_symlink():frameworks.unlink()
    elif frameworks.exists():raise ValueError('已有其他框架目录，需单独适配目录运行资源')
    frameworks.symlink_to('Resources/worker-runtime',target_is_directory=True)
    shutil.copy2(launcher,code_dir/'creative-worker')
    ui=root/'dist'
    if (ui/'index.html').is_file():
        destination=resource_dir/'ui'
        if destination.exists():shutil.rmtree(destination)
        shutil.copytree(ui,destination)
    return {'format':'onedir','runtime_staged':True,'launcher_sha256':metadata['launcher_sha256']}


if __name__=='__main__':
    root=Path(__file__).resolve().parents[1]
    manifest=root/'src-tauri/binaries/worker-bundle.json'
    directory=manifest.exists() and json.loads(manifest.read_text()).get('format')=='onedir'
    if directory and os.environ.get('APPLE_SIGNING_IDENTITY'):
        raise SystemExit('正式签名需在目录封装后执行；当前目录封装脚本仅验收内部临时签名构建')
    result=finalize_bundle(root)
    if result['runtime_staged']:
        app=root/'src-tauri/target/release/bundle/macos/Creative Agent.app'
        subprocess.run(['codesign','--force','--deep','--sign','-',str(app)],check=True)
        subprocess.run(['codesign','--verify','--deep','--strict',str(app)],check=True)
        result['ad_hoc_integrity']='verified'
    print(json.dumps(result))
