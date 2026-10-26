"""Documented generation wire contracts; external HTTP is replaced, no live validation."""

import json

import httpx
import pytest
from PIL import Image

from creative_agent.contracts import ModelBinding, provider_catalog
from creative_agent.model_catalog import model_availability
from creative_agent.providers import ProviderClient, ProviderError

SEEDANCE = ["doubao-seedance-2-0-260128", "doubao-seedance-2-5-260628"]
URL = "https://customer.example/confirmed-selected-clip.mp4"


def unexpected_request(request):
    raise AssertionError("rejected input reached the external transport")


def config(provider, model):
    return ModelBinding(provider=provider, label=model, model_id=model, role="video_generation")


@pytest.fixture
def image(tmp_path):
    path = tmp_path / "product.png"
    Image.new("RGB", (400, 400), "#f00").save(path)
    return path


@pytest.mark.parametrize("model", SEEDANCE)
def test_exact_seedance_candidates_send_six_images_and_explicit_video_url(model, image):
    binding = config("volcengine", model)
    assert model_availability(binding.model_dump())["video_generation"]["available"]

    def transport(request):
        assert request.url == "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks"
        body = json.loads(request.content)
        assert body["model"] == model
        assert body["duration"] == 5 and body["resolution"] == "720p" and body["generate_audio"] is False
        assert len(body["content"]) == 8
        assert sum(item.get("role") == "reference_image" for item in body["content"]) == 6
        assert body["content"][-1] == {
            "type": "video_url",
            "video_url": {"url": URL},
            "role": "reference_video",
        }
        assert "data:video" not in request.content.decode()
        return httpx.Response(200, json={"id": "task-1"})

    client = ProviderClient(
        binding, "test-key", client=httpx.Client(transport=httpx.MockTransport(transport))
    )
    result = client.submit_video(
        "q", [image] * 6, reference_video_url=URL, duration=5, resolution="720p", audio=False
    )
    assert result == {"request_id": "task-1", "state": "submitted"}


def test_kling_exact_candidate_uses_omni_feature_reference_and_four_images(image):
    binding = config("kling", "kling-video-o1")
    assert model_availability(binding.model_dump())["video_generation"]["available"]

    def transport(request):
        assert request.url == "https://api-singapore.klingai.com/v1/videos/omni-video"
        assert request.headers["authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["model_name"] == "kling-video-o1"
        assert body["video_list"] == [
            {"video_url": URL, "refer_type": "feature", "keep_original_sound": "no"}
        ]
        assert len(body["image_list"]) == 4
        assert body["mode"] == "std" and body["sound"] == "off" and body["duration"] == "5"
        assert body["aspect_ratio"] == "9:16"
        assert "resolution" not in body
        return httpx.Response(200, json={"code": 0, "data": {"task_id": "o1-task"}})

    client = ProviderClient(
        binding, "test-key", client=httpx.Client(transport=httpx.MockTransport(transport))
    )
    assert (
        client.submit_video("q", [image] * 4, reference_video_url=URL, resolution="720p", audio=False)[
            "request_id"
        ]
        == "o1-task"
    )
    catalog = next(item for item in provider_catalog() if item["id"] == "kling")
    assert catalog["models"][0]["generation_constraints"]["max_reference_images_with_video"] == 4


@pytest.mark.parametrize("provider,model", [("volcengine", SEEDANCE[0]), ("kling", "kling-video-o1")])
def test_url_only_generation_rejects_local_video_without_customer_url(provider, model, tmp_path):
    video = tmp_path / "selected.mp4"
    video.write_bytes(b"not transmitted")
    client = ProviderClient(
        config(provider, model),
        "test-key",
        client=httpx.Client(transport=httpx.MockTransport(unexpected_request)),
    )
    with pytest.raises(ValueError, match="URL"):
        client.submit_video("q", video=video, resolution="720p", audio=False)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"images_count": 6},
        {"audio": True},
        {"duration": 15},
        {"resolution": "4k"},
        {"ratio": "21:9"},
    ],
)
def test_kling_does_not_drop_excess_images_or_downgrade_requested_output(kwargs, image):
    calls = []
    client = ProviderClient(
        config("kling", "kling-video-o1"),
        "test-key",
        client=httpx.Client(transport=httpx.MockTransport(lambda request: calls.append(request))),
    )
    options = {"resolution": "720p", "audio": False, "duration": 5, "ratio": "9:16"}
    options.update({key: value for key, value in kwargs.items() if key != "images_count"})
    with pytest.raises(ValueError):
        client.submit_video("q", [image] * kwargs.get("images_count", 0), reference_video_url=URL, **options)
    assert calls == []


@pytest.mark.parametrize(
    "url",
    [
        "data:video/mp4;base64,eA==",
        "file:///selected.mp4",
        "http://customer.example/x.mp4",
        "https://user:password@customer.example/x.mp4",
        "https://127.0.0.1/x.mp4",
    ],
)
def test_seedance_rejects_nonpublic_or_credential_bearing_reference_urls(url):
    client = ProviderClient(
        config("volcengine", SEEDANCE[0]),
        "test-key",
        client=httpx.Client(transport=httpx.MockTransport(unexpected_request)),
    )
    with pytest.raises(ValueError):
        client.submit_video("q", reference_video_url=url, resolution="720p")


def test_kling_poll_uses_documented_task_envelope_and_cancellation_is_not_invented():
    requests = []

    def transport(request):
        requests.append(request)
        assert request.url.path == "/v1/videos/omni-video/task-1"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "task_status": "succeed",
                    "task_result": {
                        "videos": [{"id": "video-1", "url": "https://output.example/result.mp4"}]
                    },
                },
            },
        )

    client = ProviderClient(
        config("kling", "kling-video-o1"),
        "test-key",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )
    result = client.poll_video("task-1")
    assert result["state"] == "succeeded" and result["url"] == "https://output.example/result.mp4"
    with pytest.raises(ValueError, match="取消"):
        client.cancel_video("task-1")
    assert len(requests) == 1


def test_kling_business_error_does_not_return_task_or_echo_provider_body(image):
    client = ProviderClient(
        config("kling", "kling-video-o1"),
        "test-key",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json={"code": 1001, "message": "secret test-key", "data": {"task_id": "invalid"}}
                )
            )
        ),
    )
    with pytest.raises(ProviderError) as error:
        client.submit_video("q", [image], reference_video_url=URL, resolution="720p", audio=False)
    assert "test-key" not in str(error.value)


def test_new_generation_route_preserves_external_request_guard(image):
    calls = []

    def deny():
        raise ValueError("project membership revoked")

    client = ProviderClient(
        config("volcengine", SEEDANCE[0]),
        "test-key",
        before_request=deny,
        client=httpx.Client(transport=httpx.MockTransport(lambda request: calls.append(request))),
    )
    with pytest.raises(ValueError, match="membership revoked"):
        client.submit_video("q", [image] * 6, reference_video_url=URL, resolution="720p")
    assert calls == []


def test_existing_h3_does_not_ignore_an_explicit_video_url():
    client = ProviderClient(
        config("minimax", "MiniMax-H3"),
        "test-key",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"task_id": "would-submit"})
            )
        ),
    )
    with pytest.raises(ValueError, match="URL"):
        client.submit_video("q", reference_video_url=URL, resolution="768P")


def test_url_validation_happens_in_preflight_before_any_budget_reservation():
    with pytest.raises(ValueError, match="URL"):
        ProviderClient.validate_video(
            config("volcengine", SEEDANCE[0]), 5, "720p", reference_video_url="https://127.0.0.1/source.mp4"
        )
