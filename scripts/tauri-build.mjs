// Build without Finder automation; application functionality does not need desktop-control permission.
import {spawnSync} from 'node:child_process';
import {existsSync} from 'node:fs';
import {resolve} from 'node:path';
const args=process.argv.slice(2);
if(process.platform==='darwin'&&!args.includes('--bundles'))args.push('--bundles','app');
const result=spawnSync(process.execPath,[resolve('node_modules/@tauri-apps/cli/tauri.js'),'build',...args],{stdio:'inherit',env:{...process.env,CI:'true'}});
if(result.status===0&&process.platform==='darwin'){const python=existsSync(resolve('.venv/bin/python'))?resolve('.venv/bin/python'):'python3';const staged=spawnSync(python,[resolve('scripts/macos_worker_bundle.py')],{stdio:'inherit'});if(staged.status!==0)process.exit(staged.status??1)}
process.exit(result.status??1);
