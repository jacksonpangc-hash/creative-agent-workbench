from __future__ import annotations

import keyring


class SecretStore:
    """No plaintext fallback; injected backends are for an explicitly owned runtime/test."""
    def __init__(self, backend=None):
        self.injected = backend is not None
        self.backend = backend if self.injected else keyring.get_keyring()
        if not self.injected and type(self.backend).__module__.lower() == 'keyring.backends.chainer':
            candidates = [b for b in self.backend.backends if self._native(b) and b.priority > 0]
            if candidates:
                self.backend = max(candidates, key=lambda b: b.priority)

    @staticmethod
    def _native(backend):
        return type(backend).__module__.lower() in {
            'keyring.backends.macos', 'keyring.backends.windows',
            'keyring.backends.secretservice', 'keyring.backends.libsecret', 'keyring.backends.kwallet'}

    def _check(self):
        name = type(self.backend).__module__.lower()
        if self.backend.priority <= 0 or "plaintext" in name or ".fail" in name or (not self.injected and not self._native(self.backend)):
            raise ValueError("系统密钥库不可用，请先配置安全密钥库；不会回退到明文文件")

    def set(self, reference, value):
        self._check()
        if not value or len(value) > 8192:
            raise ValueError("密钥为空或超出长度限制")
        self.backend.set_password("creative-material-agent", reference, value)

    def get(self, reference):
        self._check()
        return self.backend.get_password("creative-material-agent", reference)

    def delete(self, reference):
        self._check()
        try:
            self.backend.delete_password("creative-material-agent", reference)
        except keyring.errors.PasswordDeleteError:
            pass

