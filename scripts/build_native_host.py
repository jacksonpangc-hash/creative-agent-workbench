"""Bootstrap the real native host before Tauri checks its sidecar resource."""
import json,os,platform,shutil,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
env=dict(os.environ)
# The app config requires the host we are building. Temporarily omit only that
# sidecar from build-script resource checks; the final app uses the full config.
env['TAURI_CONFIG']=json.dumps({'bundle':{'externalBin':['binaries/creative-worker']}})
subprocess.run(['cargo','build','--manifest-path',str(root/'src-tauri/Cargo.toml'),'--release','--bin','creative-browser-host'],env=env,check=True)
triple={('darwin','x86_64'):'x86_64-apple-darwin',('darwin','arm64'):'aarch64-apple-darwin',('win32','AMD64'):'x86_64-pc-windows-msvc'}.get((sys.platform,platform.machine()))
if not triple:raise SystemExit('Target not validated by this build script')
suffix='.exe' if sys.platform=='win32' else ''
shutil.copy2(root/'src-tauri/target/release'/('creative-browser-host'+suffix),root/'src-tauri/binaries'/('creative-browser-host-'+triple+suffix))
print('Built actual native host:',triple)
