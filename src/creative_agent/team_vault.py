"""Encrypted team vault. Master key is mounted separately from the database."""

from __future__ import annotations

import json
from contextvars import ContextVar
from pathlib import Path

shared_credential_consent: ContextVar[bool] = ContextVar("team_shared_credential_consent", default=False)


def encrypt_secret(key, organization_id, project_id, reference, value):
    from cryptography.fernet import Fernet

    if not value or len(value) > 8192:
        raise ValueError("密钥为空或超出长度限制")
    return Fernet(key).encrypt(
        json.dumps([organization_id, project_id, reference, value], ensure_ascii=False).encode()
    )


def decrypt_secret(key, organization_id, project_id, reference, ciphertext):
    from cryptography.fernet import Fernet, InvalidToken

    try:
        envelope = json.loads(Fernet(key).decrypt(bytes(ciphertext)))
        if envelope[:3] != [organization_id, project_id, reference]:
            raise ValueError("密钥范围不一致")
        return envelope[3]
    except (InvalidToken, ValueError, TypeError) as exc:
        raise ValueError("团队密钥无法解密或范围不一致") from exc


class TeamVault:
    def __init__(self, store, master_key_file):
        from cryptography.fernet import Fernet

        self.store = store
        path = Path(master_key_file)
        if not path.is_file():
            raise ValueError("必须挂载独立团队主密钥文件")
        from .team_admin import secret_file

        self.key = secret_file(path).encode()
        Fernet(self.key)  # fail startup, no plaintext fallback

    def _scope(self, reference, permission):
        actor = self.store.actor()
        project_id = reference.split(":", 1)[0]
        self.store.require(project_id, permission)
        return actor, project_id

    def set(self, reference, value):
        if not shared_credential_consent.get():
            raise PermissionError("团队当前仅支持明确确认的项目共享凭证；个人密钥不能自动作为共享密钥保存")
        actor, p = self._scope(reference, "secrets")
        cipher = encrypt_secret(self.key, actor.organization_id, p, reference, value)
        with self.store.connection(True) as db:
            db.execute(
                "INSERT INTO team_secrets(project_id,reference,ciphertext) VALUES(?,?,?) ON CONFLICT(project_id,reference) DO UPDATE SET ciphertext=excluded.ciphertext",
                (p, reference, cipher),
            )

    def get(self, reference):
        # Worker receives read access only while actively executing a leased job.
        actor = self.store.actor()
        permission = "external_model" if actor.worker else "secrets"
        actor, p = self._scope(reference, permission)
        with self.store.connection() as db:
            row = db.execute(
                "SELECT ciphertext FROM team_secrets WHERE project_id=? AND reference=?", (p, reference)
            ).fetchone()
        return (
            decrypt_secret(self.key, actor.organization_id, p, reference, row["ciphertext"]) if row else None
        )

    def delete(self, reference):
        _, p = self._scope(reference, "secrets")
        with self.store.connection(True) as db:
            db.execute("DELETE FROM team_secrets WHERE project_id=? AND reference=?", (p, reference))
