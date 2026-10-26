"""Formal commercial gateway; never infer API permissions from membership."""

import httpx

from .providers import ProviderError


class CMMConnector:
    BASE = "https://ai-api.chanmama.com"

    def __init__(self, api_key, client=None):
        if not api_key:
            raise ValueError("需要蝉妈妈正式 API 凭证")
        self.key = api_key
        self.client = client or httpx.Client(timeout=60, trust_env=False, follow_redirects=False)

    def _request(self, method, path, **kwargs):
        try:
            response = self.client.request(
                method, self.BASE + path, headers={"Authorization": "Bearer " + self.key}, **kwargs
            )
            if response.status_code != 200:
                raise ProviderError(f"商业数据通道返回 HTTP {response.status_code}，请核对正式权限")
            data = response.json()
            if not isinstance(data, dict) or type(data.get("code")) is not int or data["code"] != 0:
                raise ProviderError("蝉妈妈业务响应未成功；请核对账户权限和供应商账单（错误内容已隐藏）")
            return data
        except (httpx.HTTPError, ValueError):
            raise ProviderError("商业数据请求失败，请核对权限和请求状态") from None

    def permissions(self, intent):
        if not intent.strip():
            raise ValueError("需要明确本次查询目的")
        return self._request("GET", "/v1/cmm/api/permission/list", params={"intent": intent})

    def execute(self, operation, query, *, permission_schema=None):
        # A permission response alone is not a parameter schema. Both are required.
        if (
            not permission_schema
            or operation != permission_schema.get("api")
            or not permission_schema.get("authorized")
        ):
            raise ValueError("缺少正式接口权限和已核对的参数映射")
        properties = permission_schema.get("properties", {})
        required = permission_schema.get("required", [])
        if not properties or set(query) - set(properties) or any(k not in query for k in required):
            raise ValueError("参数与正式权限接口文档不一致")
        return self._request("POST", "/v1/cmm/api/execute", json={"api": operation, "query": query})
