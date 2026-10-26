"""Tests must never accidentally contact paid production providers."""
import pytest
import httpx


@pytest.fixture(autouse=True)
def prevent_live_supplier_http(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError('Real HTTP transport forbidden in tests; use an explicit MockTransport')
    monkeypatch.setattr(httpx.HTTPTransport,'handle_request',refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport,'handle_async_request',refuse)
