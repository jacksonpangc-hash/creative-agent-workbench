"""Catch wrong supplier wire formats, leaked secrets and silent reference-video downgrade."""
import json

import httpx
import pytest
from PIL import Image

from creative_agent.contracts import ModelBinding
from creative_agent.providers import ProviderClient, ProviderError, parse_json_answer
from creative_agent.connectors import CMMConnector


def binding(provider="deepseek", **kwargs):
    models={"deepseek":"deepseek-flash","anthropic":"claude-sonnet-5-5","google":"gemini-3.8-flash"}
    return ModelBinding(provider=provider, label="test", model_id=models[provider], role="strategy", **kwargs)


def test_openai_compatible_image_payload_and_usage_are_real_boundaries(tmp_path):
    path = tmp_path / "frame.png"
    Image.new("RGB", (8, 8)).save(path)
    def transport(request):
        payload = json.loads(request.content)
        assert request.url.path == "/chat/completions"
        assert payload["messages"][1]["content"][1]["type"] == "image_url"
        assert payload["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
        assert "temperature" not in payload
        return httpx.Response(200, json={"id": "req-a", "choices": [{"message": {"content": '{"ok":true}'}}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
    response = ProviderClient(binding(), "private-key", httpx.Client(transport=httpx.MockTransport(transport))).chat(
        "system", "question", images=[path])
    assert response["data"] == {"ok": True}
    assert response["usage"]["input_tokens"] == 10
    assert response["request_id"] == "req-a"


def test_anthropic_and_google_have_distinct_multimodal_contracts(tmp_path):
    path = tmp_path / "frame.png"
    Image.new("RGB", (8, 8)).save(path)
    def anthropic(request):
        data = json.loads(request.content)
        assert request.headers["x-api-key"] == "key"
        assert data["messages"][0]["content"][1]["source"]["type"] == "base64"
        return httpx.Response(200, json={"id": "a", "content": [{"type": "text", "text": '{"answer":"A"}'}],
                                         "usage": {"input_tokens": 3, "output_tokens": 4}})
    a = ProviderClient(binding("anthropic", protocol="anthropic"), "key", httpx.Client(transport=httpx.MockTransport(anthropic)))
    assert a.chat("system", "q", images=[path])["data"]["answer"] == "A"
    def google(request):
        data = json.loads(request.content)
        assert request.headers["x-goog-api-key"] == "key"
        assert data["contents"][0]["parts"][1]["inlineData"]["mimeType"] == "image/png"
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"answer":"G"}'}]}}],
                                         "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 6}})
    g = ProviderClient(binding("google", protocol="google"), "key", httpx.Client(transport=httpx.MockTransport(google)))
    assert g.chat("s", "q", images=[path])["data"]["answer"] == "G"


def test_unsupported_native_video_fails_before_any_network(tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"fixture")
    client = ProviderClient(binding(), "key", httpx.Client(transport=httpx.MockTransport(
        lambda request: pytest.fail("unsupported capability must not reach supplier"))))
    with pytest.raises(ValueError, match="不支持"):
        client.chat("s", "q", video=path)


def test_minimax_reference_video_generation_preserves_six_images_and_task_id(tmp_path):
    path = tmp_path / "ref.png"
    Image.new("RGB", (8, 8)).save(path)
    video = tmp_path / "ref.mp4"
    video.write_bytes(b"fixture")
    config = ModelBinding(provider="minimax", label="video", model_id="MiniMax-H3", role="video_generation", protocol="minimax_video")
    def transport(request):
        payload = json.loads(request.content)
        assert request.url.path == "/v2/video_generation"
        assert len([c for c in payload["content"] if c["type"] == "image_url"]) == 6
        assert next(c for c in payload["content"] if c["type"] == "video_url")["role"] == "reference_video"
        return httpx.Response(200, json={"task_id": "remote-1"})
    client = ProviderClient(config, "key", httpx.Client(transport=httpx.MockTransport(transport)))
    assert client.submit_video("replace", [path] * 6, video, duration=5, ratio="9:16", resolution="768P")["request_id"] == "remote-1"


def test_provider_error_does_not_echo_secret_or_body():
    def transport(request):
        return httpx.Response(401, text="key=private-key")
    client = ProviderClient(binding(), "private-key", httpx.Client(transport=httpx.MockTransport(transport)))
    with pytest.raises(ProviderError) as error:
        client.chat("s", "q")
    assert "private-key" not in str(error.value)
    assert "401" in str(error.value)


def test_malformed_or_partial_json_is_not_a_successful_answer():
    assert parse_json_answer('```json\n{"a":1}\n```') == {"a": 1}
    for text in ('{"a":', 'result follows', '[1,2]', '{"n":NaN}'):
        with pytest.raises(ProviderError):
            parse_json_answer(text)


def test_poll_and_cancel_do_not_create_a_new_generation():
    config = ModelBinding(provider="minimax", label="video", model_id="MiniMax-H3", role="video_generation", protocol="minimax_video")
    def transport(request):
        assert request.method in {"GET", "DELETE"}
        assert request.url.path.endswith("remote-1")
        if request.method == "DELETE":
            return httpx.Response(200, json={"task": {"status": "cancelled"}})
        return httpx.Response(200, json={"task": {"id": "remote-1", "status": "succeeded", "content": {"url": "https://example.org/out.mp4"}}})
    client = ProviderClient(config, "key", httpx.Client(transport=httpx.MockTransport(transport)))
    assert client.poll_video("remote-1")["state"] == "succeeded"
    assert client.cancel_video("remote-1")["state"] == "cancelled"


def test_cmm_uses_formal_gateway_and_does_not_guess_paid_operation_schema():
    def transport(request):
        assert request.url.path == "/v1/cmm/api/permission/list"
        assert request.url.params["intent"] == "research"
        return httpx.Response(200, json={"code":0, "data": {"apis": []}})
    cmm = CMMConnector("key", client=httpx.Client(transport=httpx.MockTransport(transport)))
    assert cmm.permissions("research")["data"]["apis"] == []
    with pytest.raises(ValueError, match="权限"):
        cmm.execute("guessed", {}, permission_schema=None)


def test_minimax_poll_uses_documented_query_route():
    config = ModelBinding(provider='minimax',label='H3',model_id='MiniMax-H3',role='video_generation',protocol='minimax_video')
    def handle(request):
        assert request.url.path=='/v2/query/video_generation/task-1'
        return httpx.Response(200,json={'task':{'status':'running'}})
    ProviderClient(config,'key',httpx.Client(transport=httpx.MockTransport(handle))).poll_video('task-1')

def test_embedding_returns_finite_vector_and_checks_role():
    b=ModelBinding(provider='openai',label='embed',model_id='text-embedding-3-small',role='embedding')
    def handle(request):
        assert request.url.path=='/v1/embeddings'
        assert json.loads(request.content)['input']=='商品开头'
        return httpx.Response(200,json={'data':[{'embedding':[0.1,0.2]}]})
    assert ProviderClient(b,'key',httpx.Client(transport=httpx.MockTransport(handle))).embed('商品开头')==[0.1,0.2]


@pytest.mark.parametrize('provider,model,path',[
    ('alibaba','text-embedding-v4','/compatible-mode/v1/embeddings'),
    ('zhipu','embedding-3','/api/paas/v4/embeddings'),
])
def test_chinese_embedding_services_use_their_documented_routes(provider,model,path):
    config=ModelBinding(provider=provider,model_id=model,label='检索',role='embedding')
    def route(request):
        assert request.url.path==path
        data=json.loads(request.content)
        assert data['model']==model and data['input']=='中文创意'
        return httpx.Response(200,json={'data':[{'embedding':[0.25,-0.5,0.75]}]})
    result=ProviderClient(config,'fixture',httpx.Client(transport=httpx.MockTransport(route))).embed('中文创意')
    assert result==[0.25,-0.5,0.75]


def test_permission_guard_rechecks_before_each_external_request():
    calls=[]
    permitted=[True]
    def guard():
        if not permitted[0]: raise PermissionError('revoked')
    def transport(request):
        calls.append(request)
        return httpx.Response(200,json={'ok':True})
    client=ProviderClient(binding(),'key',httpx.Client(transport=httpx.MockTransport(transport)),before_request=guard)
    assert client._request('GET','/first')=={'ok':True}
    permitted[0]=False
    with pytest.raises(PermissionError): client._request('POST','/second',json={})
    assert len(calls)==1


@pytest.mark.parametrize('provider,model_id,protocol,expected_thinking', [
    ('deepseek', 'deepseek-flash', 'openai', {'type': 'disabled'}),
    ('deepseek', 'deepseek-other-fixture', 'openai', None),
    ('openai', 'deepseek-flash', 'openai', None),
    ('openai', 'gpt-4.1', 'openai', None),
])
def test_only_deepseek_flash_disables_default_thinking_for_bounded_json(
        monkeypatch, provider, model_id, protocol, expected_thinking):
    # Controlled future profiles exercise the exact routing exclusion without
    # weakening the real capability validation or enabling them in the app.
    from creative_agent.model_catalog import PROFILES
    if not any(profile[0] == model_id for profile in PROFILES[provider]):
        monkeypatch.setitem(PROFILES, provider, [*PROFILES[provider],
            (model_id, '受控未来型号', ('image_input',), {})])
    config = ModelBinding(provider=provider, model_id=model_id, protocol=protocol,
                          label='受控请求', role='strategy', max_output_tokens=1024)
    def transport(request):
        payload = json.loads(request.content)
        if expected_thinking is None:
            assert 'thinking' not in payload
        else:
            assert payload['thinking'] == expected_thinking
        assert payload['max_tokens'] == 1024
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"ok":true}'}}]})
    result = ProviderClient(config, 'fixture', httpx.Client(transport=httpx.MockTransport(transport))).chat('s', 'q')
    assert result['data'] == {'ok': True}


@pytest.mark.parametrize('content', ['', '{"ok":true}'])
def test_output_limit_rejects_even_valid_json_without_retry_or_response_leak(content):
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={'id': 'billed-request', 'choices': [{
            'finish_reason': 'length', 'message': {'content': content,
                                                   'reasoning_content': 'private-reasoning'}}],
            'usage': {'prompt_tokens': 5, 'completion_tokens': 1024}})
    client = ProviderClient(binding(), 'private-key', httpx.Client(transport=httpx.MockTransport(transport)))
    with pytest.raises(ProviderError, match='输出上限') as error:
        client.chat('s', 'q')
    message = str(error.value)
    assert '可能已计费' in message and '不会自动重试' in message
    assert 'private-key' not in message and 'private-reasoning' not in message
    assert len(calls) == 1


@pytest.mark.parametrize('choice', [None, [], 'private-response'])
def test_malformed_chat_choice_keeps_sanitized_provider_error(choice):
    def transport(request):
        return httpx.Response(200, json={'choices': [choice]})
    client = ProviderClient(binding(), 'fixture', httpx.Client(transport=httpx.MockTransport(transport)))
    with pytest.raises(ProviderError, match='未返回回答') as error:
        client.chat('s', 'q')
    assert 'private-response' not in str(error.value)
