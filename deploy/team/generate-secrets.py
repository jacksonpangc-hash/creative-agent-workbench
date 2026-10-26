"""Generate new deployment secrets only; refuses to replace any existing directory."""
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import quote

from cryptography.fernet import Fernet

path=Path(sys.argv[1] if len(sys.argv)>1 else 'private')
path.mkdir(mode=0o700,parents=False,exist_ok=False)
admin=secrets.token_urlsafe(48)
runtime=secrets.token_urlsafe(48)
values={'postgres_password':admin,'runtime_password':runtime,
        'admin_dsn':f'postgresql://postgres:{quote(admin,safe="")}@db:5432/creative',
        'runtime_dsn':f'postgresql://creative_runtime:{quote(runtime,safe="")}@db:5432/creative',
        'vault_master_key':Fernet.generate_key().decode()}
for name,value in values.items():
    descriptor=os.open(path/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(descriptor,'w') as stream: stream.write(value+'\n')
print('Created private deployment secret files. Keep the vault master key backup separate from database backups.')
