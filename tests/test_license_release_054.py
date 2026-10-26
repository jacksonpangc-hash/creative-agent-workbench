"""Focused cases for existing admin feature editing and production configuration."""
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from creative_agent.commercial_license import LicenseError, verify_lease
from creative_agent.license_service import LicenseDatabase, LicenseService, create_license_service, hash_password
from creative_agent.license_deployment import validate_public_endpoint


def test_edit_features_preserves_term_revokes_refresh_and_requires_relogin(tmp_path):
    key=Ed25519PrivateKey.generate();service=LicenseService(LicenseDatabase(tmp_path/'licenses.sqlite'),key,now=lambda:1000)
    license=service.issue('客户','long-enough-password',30,features=['workspace.use','assets.import'])
    login=service.login(license['id'],'long-enough-password','device-A')
    before=service.list()[0]
    result=service.set_features(license['id'],['workspace.use','script.generate'])
    assert result['expires_at']==before['expires_at']
    assert result['generation']==before['generation']+1
    with pytest.raises(LicenseError):service.refresh(license['id'],login['refresh_token'],'device-A')
    login=service.login(license['id'],'long-enough-password','device-A')
    assert verify_lease(key.public_key(),login['lease'],'device-A',1000)['features']==['script.generate','workspace.use']
    assert service.audit(license['id'])[-2]['action']=='set_features'
    with pytest.raises(ValueError):service.set_features(license['id'],['unknown.feature'])


def test_admin_feature_edit_requires_session_and_forbids_extra_fields(tmp_path):
    service=LicenseService(LicenseDatabase(tmp_path/'licenses.sqlite'),Ed25519PrivateKey.generate())
    license=service.issue('客户','long-enough-password',3)
    client=TestClient(create_license_service(service,admin_password_hash=hash_password('admin-long-password')))
    path=f'/admin/licenses/{license["id"]}/features'
    assert client.post(path,json={'features':['workspace.use']}).status_code==401
    token=client.post('/admin/login',json={'password':'admin-long-password'}).json()['token']
    headers={'Authorization':'Bearer '+token}
    assert client.post(path,headers=headers,json={'features':['workspace.use']}).json()['features']==['workspace.use']
    assert client.post(path,headers=headers,json={'features':[],'disabled':0}).status_code==422


def certificate(tmp_path,host,*,expired=False):
    key=Ed25519PrivateKey.generate();now=datetime.now(timezone.utc)
    cert=(x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,host)]))
          .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Configuration Test CA')]))
          .public_key(key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now-timedelta(days=10)).not_valid_after(now+timedelta(days=-1 if expired else 10))
          .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]),critical=False).sign(key,None))
    path=tmp_path/'certificate.pem';path.write_bytes(cert.public_bytes(serialization.Encoding.PEM));return path


def test_production_endpoint_rejects_placeholders_missing_or_wrong_certificate(tmp_path):
    cert=certificate(tmp_path,'license.acme-commerce.com')
    for url in ['', 'http://license.acme-commerce.com','https://license.example','https://localhost','https://127.0.0.1','https://user:pass@license.acme-commerce.com','https://license.acme-commerce.com/path']:
        with pytest.raises(ValueError):validate_public_endpoint(url,cert)
    with pytest.raises(ValueError):validate_public_endpoint('https://other.acme-commerce.com',cert)
    with pytest.raises(ValueError):validate_public_endpoint('https://license.acme-commerce.com',tmp_path/'missing.pem')
    assert validate_public_endpoint('https://license.acme-commerce.com',cert)['host']=='license.acme-commerce.com'
    cert=certificate(tmp_path,'license.acme-commerce.com',expired=True)
    with pytest.raises(ValueError):validate_public_endpoint('https://license.acme-commerce.com',cert)
