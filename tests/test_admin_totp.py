import pytest
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from creative_agent.license_service import LicenseDatabase, LicenseService, create_license_service, hash_password

SECRET='GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'


def test_standard_totp_vector_and_invalid_secret():
    from creative_agent.admin_mfa import totp_counter
    assert totp_counter(SECRET,'287082',59)==1
    assert totp_counter(SECRET,'000000',59) is None
    with pytest.raises(ValueError):totp_counter('weak','123456',59)


def test_admin_requires_second_factor_and_rejects_replay_after_restart(tmp_path):
    service=LicenseService(LicenseDatabase(tmp_path/'license.db'),Ed25519PrivateKey.generate(),now=lambda:59)
    def app():return create_license_service(service,admin_password_hash=hash_password('long-admin-password'),admin_totp_secret=SECRET)
    with TestClient(app()) as c:
        assert c.post('/admin/login',json={'password':'long-admin-password'}).status_code==401
        assert c.post('/admin/login',json={'password':'long-admin-password','otp':'000000'}).status_code==401
        assert c.post('/admin/login',json={'password':'wrong','otp':'287082'}).status_code==401
        result=c.post('/admin/login',json={'password':'long-admin-password','otp':'287082'})
        assert result.status_code==200,result.text
        assert SECRET not in result.text
    with TestClient(app()) as c:
        assert c.post('/admin/login',json={'password':'long-admin-password','otp':'287082'}).status_code==401


def test_rotating_second_factor_revokes_old_admin_sessions(tmp_path):
    service=LicenseService(LicenseDatabase(tmp_path/'license.db'),Ed25519PrivateKey.generate(),now=lambda:59)
    password=hash_password('long-admin-password')
    with TestClient(create_license_service(service,admin_password_hash=password,admin_totp_secret=SECRET)) as c:
        token=c.post('/admin/login',json={'password':'long-admin-password','otp':'287082'}).json()['token']
    changed=create_license_service(service,admin_password_hash=password,admin_totp_secret='JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP')
    with TestClient(changed) as c:
        assert c.get('/admin/licenses',headers={'Authorization':'Bearer '+token}).status_code==401
