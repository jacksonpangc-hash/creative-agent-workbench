"""RFC 6238 SHA-1 / six-digit administrator second factor."""
import base64
import binascii
import hashlib
import hmac
import struct


def secret_bytes(secret):
    try:
        normalized=secret.strip().upper()
        value=base64.b32decode(normalized+'='*((-len(normalized))%8))
    except (ValueError,binascii.Error,AttributeError) as exc:
        raise ValueError('管理员二次验证密钥必须是有效的 Base32') from exc
    if len(value)<20:raise ValueError('管理员二次验证密钥至少需要 160 位随机数据')
    return value


def totp_counter(secret,code,timestamp):
    key=secret_bytes(secret)
    if not isinstance(code,str) or len(code)!=6 or not code.isascii() or not code.isdigit():return None
    counter=int(timestamp)//30
    for candidate in (counter,counter-1,counter+1):
        if candidate<0:continue
        value=hmac.new(key,struct.pack('>Q',candidate),hashlib.sha1).digest()
        offset=value[-1]&15
        expected=f'{(struct.unpack(">I",value[offset:offset+4])[0]&0x7fffffff)%1000000:06d}'
        if hmac.compare_digest(expected,code):return candidate
    return None
