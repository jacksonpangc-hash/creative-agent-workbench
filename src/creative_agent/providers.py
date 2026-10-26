"""BYOK wire adapters. Transport tests do not establish model/account availability."""
from __future__ import annotations

import base64
import ipaddress
import json
import mimetypes
import re
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from PIL import Image

from .contracts import ModelBinding, provider_catalog, require_capability
from .model_catalog import require_model


class ProviderError(ValueError):
    pass


class ProviderAccountError(ProviderError):
    """Safe actionable account state; supplier response bodies are never echoed."""


class OutputLimitError(ProviderError):
    """A completed, potentially billable request without a complete answer."""


def parse_json_answer(text):
    raw = text.strip()
    if raw.startswith('```'):
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
    try:
        data = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (ValueError, TypeError) as exc:
        raise ProviderError('模型未返回完整 JSON 对象；原始回答未作为成功结果保存') from exc


def check_media_file(path, limit=50 * 1024 * 1024):
    path = Path(path)
    if not path.is_file() or not 0 < path.stat().st_size <= limit:
        raise ValueError('模型输入文件不存在或超过通道大小限制')
    return path.stat().st_size


def media_data(path, limit=50 * 1024 * 1024):
    check_media_file(path, limit)
    path = Path(path)
    mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
    return mime, base64.b64encode(path.read_bytes()).decode()


def data_url(path):
    mime, data = media_data(path)
    return f'data:{mime};base64,{data}'


def validate_reference_video_url(value):
    """Validate an explicitly supplied customer URL; never publish or fetch local content here."""
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment or parsed.port not in {None, 443}):
        raise ValueError('参考视频需要客户提供无凭证的公网 HTTPS URL；本地视频不会自动上传')
    hostname = parsed.hostname.lower().rstrip('.')
    if hostname == 'localhost' or hostname.endswith(('.localhost', '.local', '.internal')) or '.' not in hostname:
        raise ValueError('参考视频 URL 不能使用本机或内网地址')
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return value
    if not address.is_global:
        raise ValueError('参考视频 URL 不能使用本机或内网地址')
    return value


def generation_image(path, *, kling=False):
    limit = (10 if kling else 30) * 1024 * 1024
    check_media_file(path, limit)
    with Image.open(path) as image:
        if image.format not in ({'JPEG', 'PNG'} if kling else {'JPEG', 'PNG', 'WEBP', 'BMP', 'TIFF', 'GIF'}):
            raise ValueError('参考图格式不支持，不能静默转换或丢弃')
        w, h = image.size
        if min(w, h) < 300 or not 0.4 <= w / h <= 2.5 or (not kling and max(w, h) > 6000):
            raise ValueError('参考图尺寸或宽高比不符合当前通道要求')
    mime, encoded = media_data(path, limit)
    return encoded if kling else f'data:{mime};base64,{encoded}'


class ProviderClient:
    def __init__(self, binding: ModelBinding, api_key: str, client=None, before_request=None, on_receipt=None):
        if not api_key:
            raise ValueError('请先设置此模型的 API 密钥')
        self.before_request = before_request
        self.on_receipt = on_receipt
        self.successful_calls = []
        self.binding = binding
        self.key = api_key
        default = next(p['base_url'] for p in provider_catalog() if p['id'] == binding.provider)
        if binding.protocol == 'minimax_video':
            default = 'https://api.minimax.io'
        self.base = (binding.base_url or default).rstrip('/')
        parsed = urlsplit(self.base)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('模型地址必须是无凭证、无查询参数的 HTTPS 地址')
        self.client = client or httpx.Client(timeout=180, trust_env=False, follow_redirects=False)

    def _request(self, method, path, *, headers=None, **kwargs):
        if self.before_request is not None: self.before_request()
        auth = {'Authorization': f'Bearer {self.key}'}
        if self.binding.protocol == 'anthropic':
            auth = {'x-api-key': self.key, 'anthropic-version': '2023-06-01'}
        elif self.binding.protocol == 'google':
            auth = {'x-goog-api-key': self.key}
        try:
            response = self.client.request(method, self.base + path, headers={**auth, **(headers or {})}, **kwargs)
            if response.status_code == 402:
                raise ProviderAccountError('模型服务余额不足，请到服务商账户核对余额后再发起。已完成的内容保留，费用以服务商账单为准；不会自动重试。')
            if not 200 <= response.status_code < 300:
                raise ProviderError(f'供应商请求失败（HTTP {response.status_code}）；请核对权限、地区与模型')
            result = response.json()
            if self.on_receipt and isinstance(result,dict):
                usage=result.get('usage') or result.get('usageMetadata') or {}
                self.on_receipt({'state':'response_received','http_status':response.status_code,'request_id':str(result.get('id') or result.get('responseId') or '')[:180],
                    'usage':{k:v for k,v in usage.items() if k in {'input_tokens','output_tokens','prompt_tokens','completion_tokens','promptTokenCount','candidatesTokenCount','total_tokens'} and (v is None or type(v) in (int,float))},
                    'finish_reason':str((result.get('choices') or [{}])[0].get('finish_reason',''))[:40]})
            if not isinstance(result, dict):
                raise ProviderError('供应商响应格式无效')
            if result.get('error') or result.get('base_resp', {}).get('status_code', 0):
                raise ProviderError('供应商返回业务错误；请在供应商控制台核对请求')
            if self.binding.protocol == 'kling_omni' and result.get('code') != 0:
                raise ProviderError('可灵返回业务错误或未核验响应；请在供应商控制台核对请求')
            return result
        except (httpx.HTTPError, ValueError) as exc:
            if isinstance(exc, ProviderError):
                raise
            raise ProviderError('供应商连接或响应解析失败；提交状态可能不确定，请勿直接重发') from None

    @staticmethod
    def validate_chat(binding, *, has_images=False, has_video=False):
        b = binding
        if b.role not in {'strategy','image_input','video_input'}: raise ValueError('所选模型用途不支持分析')
        config = b.model_dump()
        require_model(config, 'strategy')
        if has_images: require_model(config, 'image_input')
        if has_video: require_model(config, 'video_input')

    @staticmethod
    def validate_chat_files(binding, *, images=(), video=None):
        image_limit = (10 if binding.protocol == 'anthropic' else 14 if binding.protocol == 'google' else 50) * 1024 * 1024
        sizes = [check_media_file(path, image_limit) for path in images]
        if video: sizes.append(check_media_file(video))
        if binding.provider == 'minimax' and sum((n + 2) // 3 * 4 for n in sizes) > 62 * 1024 * 1024:
            raise ValueError('模型输入编码后超过请求大小限制')

    def chat(self, system, question, *, images=(), video=None):
        b = self.binding
        self.validate_chat(b, has_images=bool(images), has_video=bool(video))
        self.validate_chat_files(b, images=images, video=video)
        if images:
            require_capability(b.provider, 'image_input')
        if video:
            require_capability(b.provider, 'video_input')
        if b.role not in {'strategy', 'image_input', 'video_input'}:
            raise ValueError('所选模型用途不支持分析')
        if b.provider == 'minimax' and b.model_id.lower().startswith(('minimax-m2', 'm2')) and (images or video):
            raise ValueError('M2 系列不支持此多模态输入，请选择已验证的 M3 模型')
        if b.protocol == 'anthropic':
            content = [{'type': 'text', 'text': question}]
            for path in images:
                mime, data = media_data(path, 10 * 1024 * 1024)
                content.append({'type': 'image', 'source': {'type': 'base64', 'media_type': mime, 'data': data}})
            if video:
                if b.provider != 'minimax':
                    raise ValueError('此接口不支持原生视频；请选择帧图像观察')
                mime, data = media_data(video)
                content.append({'type': 'video', 'source': {'type': 'base64', 'media_type': mime, 'data': data}})
            raw = self._request('POST', '/messages', json={'model': b.model_id, 'system': system,
                'max_tokens': b.max_output_tokens, 'messages': [{'role': 'user', 'content': content}]})
            answer = ''.join(c.get('text', '') for c in raw.get('content', []) if c.get('type') == 'text')
            usage = raw.get('usage', {})
            request_id = raw.get('id')
        elif b.protocol == 'google':
            parts = [{'text': question}]
            for path in [*images, *([video] if video else [])]:
                mime, data = media_data(path)
                parts.append({'inlineData': {'mimeType': mime, 'data': data}})
            raw = self._request('POST', f'/models/{quote(b.model_id, safe="")}:generateContent', json={
                'systemInstruction': {'parts': [{'text': system}]}, 'contents': [{'role': 'user', 'parts': parts}],
                'generationConfig': {'responseMimeType': 'application/json', 'maxOutputTokens': b.max_output_tokens}})
            answer = ''.join(p.get('text', '') for c in raw.get('candidates', []) for p in c.get('content', {}).get('parts', []))
            u = raw.get('usageMetadata', {})
            usage = dict(input_tokens=u.get('promptTokenCount'), output_tokens=u.get('candidatesTokenCount'))
            request_id = raw.get('responseId')
        else:
            content = [{'type': 'text', 'text': question}]
            content.extend({'type': 'image_url', 'image_url': {'url': data_url(p)}} for p in images)
            if video:
                if b.provider not in {'kimi', 'volcengine'}:
                    raise ValueError('该接口未核验原生视频格式，请选择帧图像观察或专用接口')
                content.append({'type': 'video_url', 'video_url': {'url': data_url(video)}})
            base_override = b.protocol == 'dashscope'
            path = '/compatible-mode/v1/chat/completions' if base_override else '/chat/completions'
            body = {'model': b.model_id, 'messages': [
                {'role': 'system', 'content': system}, {'role': 'user', 'content': content}],
                'max_tokens': b.max_output_tokens}
            if b.protocol == 'openai' and b.provider == 'deepseek' and b.model_id == 'deepseek-flash':
                body['thinking'] = {'type': 'disabled'}
            raw = self._request('POST', path, json=body)
            try:
                choice = raw['choices'][0]
                if isinstance(choice, dict) and choice.get('finish_reason') == 'length':
                    raise OutputLimitError('模型回答达到本次输出上限，未返回完整结果；本次调用可能已计费，不会自动重试。请缩小本次创作目标，核对费用与任务记录后再手动发起。')
                answer = choice['message']['content']
            except (KeyError, IndexError, TypeError):
                raise ProviderError('供应商未返回回答') from None
            u = raw.get('usage', {})
            usage = dict(input_tokens=u.get('prompt_tokens'), output_tokens=u.get('completion_tokens'))
            request_id = raw.get('id')
        parsed = parse_json_answer(answer)
        self.successful_calls.append({'input_type':'video' if video else 'images' if images else 'text','request_id':request_id,'usage':usage})
        return dict(data=parsed, usage=usage, request_id=request_id)

    @staticmethod
    def validate_video(binding, duration, resolution, *, has_reference_video=True, audio=True,
                       reference_video_url=None, image_count=0, ratio=None):
        if binding.role != 'video_generation': raise ValueError('此配置不是视频生成用途')
        require_model(binding.model_dump(), 'video_generation')
        if reference_video_url:
            if binding.protocol not in {'ark', 'kling_omni'}:
                raise ValueError('当前通道尚未实现显式视频 URL 输入，不能忽略参考视频')
            validate_reference_video_url(reference_video_url)
        if binding.protocol == 'minimax_video':
            if binding.provider != 'minimax' or binding.model_id != 'MiniMax-H3' or not 4 <= duration <= 15 or resolution not in {'768P', '2K'}:
                raise ValueError('此通道仅核验 MiniMax-H3、4–15秒、768P/2K')
            if not audio: raise ValueError('H3 当前已核验接口不能保证关闭音频，请开启音频或更换通道')
        elif binding.protocol == 'ark':
            if binding.provider != 'volcengine': raise ValueError('Ark 协议需要火山引擎配置')
            if binding.model_id not in {'doubao-seedance-2-0-260128', 'doubao-seedance-2-5-260628'}:
                raise ValueError('此 Seedance 具体型号的生成协议尚未核验')
            if not 4 <= duration <= 15 or resolution not in {'480p', '720p', '1080p'}:
                raise ValueError('此 Seedance 适配范围为4–15秒、480p/720p/1080p')
            if has_reference_video and not reference_video_url:
                raise ValueError('Seedance 生成需要客户提供已选片段的公网 HTTPS URL，不支持本地视频 Base64')
        elif binding.protocol == 'kling_omni':
            if binding.provider != 'kling' or binding.model_id != 'kling-video-o1':
                raise ValueError('此通道仅核验 Kling O1')
            if not 3 <= duration <= 10 or resolution not in {'720p', '1080p'} or audio:
                raise ValueError('Kling O1 视频参考仅支持3–10秒、720p/1080p且关闭音频')
            if has_reference_video and not reference_video_url:
                raise ValueError('Kling O1 需要客户提供已选片段的公网 HTTPS URL，不能直接提交本地视频')
            if image_count > (4 if has_reference_video else 6):
                raise ValueError('Kling O1 有参考视频时最多4张参考图，不能静默删减六图')
            if ratio is not None and ratio not in {'16:9', '9:16', '1:1'}:
                raise ValueError('Kling O1 不支持所选宽高比')
        elif binding.protocol == 'dashscope':
            if has_reference_video: raise ValueError('Wan3 本地参考视频上传尚未核验，无法提交此计费任务')
        else:
            raise ValueError('此模型的原生参考视频生成接口尚未核验，不能提交计费任务')

    def submit_video(self, prompt, images=(), video=None, *, duration=5, ratio='9:16', resolution='768P', audio=True,
                     reference_video_url=None):
        b = self.binding
        require_capability(b.provider, 'video_generation')
        self.validate_video(b, duration, resolution, has_reference_video=bool(video or reference_video_url), audio=audio,
                            reference_video_url=reference_video_url, image_count=len(images), ratio=ratio)
        if not 1 <= duration <= 15 or len(images) > 6:
            raise ValueError('生成时长或参考图片数超限')
        if b.protocol == 'minimax_video':
            if b.model_id != 'MiniMax-H3' or not 4 <= duration <= 15 or resolution not in {'768P', '2K'}:
                raise ValueError('此通道仅核验 H3，时长4–15秒、分辨率768P或2K')
            content = [{'type': 'text', 'text': prompt}]
            content += [{'type': 'image_url', 'image_url': {'url': data_url(p)}, 'role': 'reference_image'} for p in images]
            if video:
                content.append({'type': 'video_url', 'video_url': {'url': data_url(video)}, 'role': 'reference_video'})
            raw = self._request('POST', '/v2/video_generation', json={'model': b.model_id, 'content': content,
                'duration': duration, 'ratio': ratio, 'resolution': resolution})
            task_id = raw.get('task_id')
        elif b.protocol == 'ark':
            content = [{'type': 'text', 'text': prompt}]
            content += [{'type': 'image_url', 'image_url': {'url': generation_image(p)}, 'role': 'reference_image'} for p in images]
            if reference_video_url:
                content.append({'type': 'video_url', 'video_url': {'url': validate_reference_video_url(reference_video_url)}, 'role': 'reference_video'})
            raw = self._request('POST', '/contents/generations/tasks', json={'model': b.model_id, 'content': content,
                'duration': duration, 'ratio': ratio, 'resolution': resolution, 'generate_audio': audio})
            task_id = raw.get('id')
        elif b.protocol == 'kling_omni':
            body = {'model_name': b.model_id, 'prompt': prompt,
                'image_list': [{'image_url': generation_image(p, kling=True)} for p in images],
                'mode': {'720p': 'std', '1080p': 'pro'}[resolution], 'sound': 'off',
                'aspect_ratio': ratio, 'duration': str(duration)}
            if reference_video_url:
                body['video_list'] = [{'video_url': validate_reference_video_url(reference_video_url),
                    'refer_type': 'feature', 'keep_original_sound': 'no'}]
            raw = self._request('POST', '/v1/videos/omni-video', json=body)
            task_id = raw.get('data', {}).get('task_id')
        elif b.protocol == 'dashscope':
            if video:
                raise ValueError('Wan3 参考视频需要供应商临时上传或公开 URL；未授权外传前不能提交本地视频')
            if b.model_id != 'wan3.0-video' or '.maas.aliyuncs.com' not in self.base:
                raise ValueError('Wan3 需要同地区工作空间地址与已核验模型 wan3.0-video')
            raw = self._request('POST', '/api/v1/services/aigc/video-generation/video-synthesis',
                headers={'X-DashScope-Async': 'enable'}, json={'model': b.model_id,
                'input': {'prompt': prompt, 'media': [{'type': 'reference_image', 'url': data_url(p)} for p in images]},
                'parameters': {'duration': duration, 'size': resolution}})
            task_id = raw.get('output', {}).get('task_id')
        else:
            raise ValueError('此模型的视频生成接口尚未核验，不能提交计费任务')
        if not task_id:
            raise ProviderError('供应商未返回任务编号；状态不确定，请在控制台核对')
        return {'request_id': str(task_id), 'state': 'submitted'}

    def poll_video(self, request_id):
        rid = quote(request_id, safe='')
        if self.binding.protocol == 'minimax_video':
            raw = self._request('GET', '/v2/query/video_generation/' + rid)
            task = raw.get('task', {})
            return dict(state=task.get('status', 'unknown'), url=task.get('content', {}).get('url'), usage=task.get('usage', {}))
        if self.binding.protocol == 'ark':
            task = self._request('GET', '/contents/generations/tasks/' + rid)
            return dict(state=task.get('status', 'unknown'), url=task.get('content', {}).get('video_url'), usage=task.get('usage', {}))
        if self.binding.protocol == 'kling_omni':
            task = self._request('GET', '/v1/videos/omni-video/' + rid).get('data', {})
            states = {'submitted': 'submitted', 'processing': 'submitted', 'succeed': 'succeeded', 'failed': 'failed'}
            videos = task.get('task_result', {}).get('videos', [])
            return {'state': states.get(task.get('task_status'), 'unknown'),
                'url': videos[0].get('url') if videos else None, 'usage': {}}
        if self.binding.protocol == 'dashscope':
            task = self._request('GET', '/api/v1/tasks/' + rid).get('output', {})
            states = {'PENDING': 'submitted', 'RUNNING': 'submitted', 'SUCCEEDED': 'succeeded', 'FAILED': 'failed'}
            return dict(state=states.get(task.get('task_status'), 'unknown'), url=task.get('video_url'), usage={})
        raise ValueError('尚未核验此通道的任务查询接口')

    def cancel_video(self, request_id):
        rid = quote(request_id, safe='')
        routes = {'minimax_video': '/v2/video_generation/', 'ark': '/contents/generations/tasks/'}
        if self.binding.protocol not in routes:
            raise ValueError('此通道未提供已核验的取消接口，请到供应商控制台处理')
        raw = self._request('DELETE', routes[self.binding.protocol] + rid)
        return {'state': raw.get('status') or raw.get('task', {}).get('status', 'cancelling')}

    def embed(self, text):
        import math
        if self.binding.role != 'embedding':
            raise ValueError('需要检索向量用途的模型')
        require_model(self.binding.model_dump(), 'embedding')
        if self.binding.protocol == 'google':
            raw = self._request('POST', f'/models/{quote(self.binding.model_id, safe="")}:embedContent',
                json={'model': 'models/' + self.binding.model_id, 'content': {'parts': [{'text': text}]}})
            vector = raw.get('embedding', {}).get('values')
        elif self.binding.protocol in {'openai', 'dashscope'}:
            path = '/compatible-mode/v1/embeddings' if self.binding.protocol == 'dashscope' else '/embeddings'
            raw = self._request('POST', path, json={'model': self.binding.model_id, 'input': text})
            vector = raw.get('data', [{}])[0].get('embedding')
        else:
            raise ValueError('此向量接口尚未核验')
        if not isinstance(vector, list) or not vector or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in vector) or not any(vector):
            raise ProviderError('供应商返回了无效向量')
        return vector
