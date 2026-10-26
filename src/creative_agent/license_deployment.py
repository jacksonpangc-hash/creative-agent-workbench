"""Fail-closed structural preflight for the existing licensing deployment.

This validates supplied configuration locally; it does not prove DNS ownership,
public CA trust, live TLS, availability or production readiness.
"""
from __future__ import annotations

from datetime import datetime, timezone
import ipaddress
from pathlib import Path
import re
from urllib.parse import urlsplit

from cryptography import x509


def validate_public_endpoint(service_url, certificate_path, *, now=None):
    try:
        parsed=urlsplit(service_url)
        host=parsed.hostname or ''
        if parsed.scheme!='https' or not host or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/') or parsed.port not in (None,443):
            raise ValueError()
        lowered=host.lower()
        if '.' not in lowered or any(word in lowered for word in ('replace','placeholder','your-domain')) or lowered.endswith(('.example','.test','.invalid','.localhost','.local','.example.com','.example.net','.example.org')) or lowered in {'example.com','example.net','example.org'}:
            raise ValueError()
        try: ipaddress.ip_address(lowered)
        except ValueError: pass
        else: raise ValueError()
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?',lowered) or any(not label or label.startswith('-') or label.endswith('-') for label in lowered.split('.')):
            raise ValueError()
    except (ValueError,TypeError):
        raise ValueError('正式授权服务必须配置真实域名的根 HTTPS 地址，不能使用占位域名、本机地址或URL凭证') from None
    try:
        certificate=x509.load_pem_x509_certificate(Path(certificate_path).read_bytes())
        now=now or datetime.now(timezone.utc)
        if not certificate.not_valid_before_utc <= now < certificate.not_valid_after_utc:
            raise ValueError('证书尚未生效或已过期')
        if certificate.issuer==certificate.subject:
            raise ValueError('正式授权服务不接受自签名叶证书')
        names=certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
        def matches(name):
            name=name.lower()
            return name==lowered or (name.startswith('*.') and '*' not in name[2:] and lowered.count('.')==name.count('.') and lowered.endswith(name[1:]))
        if not any(matches(name) for name in names): raise ValueError('证书域名与正式服务地址不一致')
    except (OSError,ValueError,TypeError,x509.ExtensionNotFound) as error:
        raise ValueError('正式授权服务缺少匹配且有效的TLS叶证书：'+str(error)) from None
    return {'host':lowered,'service_url':service_url.rstrip('/'),'certificate_expires_at':certificate.not_valid_after_utc.isoformat(),'state':'configuration_validated_not_deployed'}
