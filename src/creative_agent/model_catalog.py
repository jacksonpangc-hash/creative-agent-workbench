"""Specific model capabilities and implemented routes, never vendor-wide inference.

Document checks establish input formats only, not live account/region availability.
Keep unknown models closed for media; add a documented profile to enable them.
"""
from urllib.parse import urlsplit

PURPOSES = ('image_input', 'video_input', 'video_generation', 'embedding', 'strategy')
LABELS = dict(strategy='口播分析', image_input='图片分析', video_input='视频分析', video_generation='视频生成', embedding='语义检索')
TASK_PURPOSES = (*PURPOSES, 'video_analysis')
CONNECTIONS = {
    'alibaba': ('dashscope', 'https://dashscope.aliyuncs.com'),
    'volcengine': ('ark', 'https://ark.cn-beijing.volces.com/api/v3'),
    'zhipu': ('openai', 'https://open.bigmodel.cn/api/paas/v4'),
    'minimax': ('anthropic', 'https://api.minimax.io/anthropic'),
    'kimi': ('openai', 'https://api.moonshot.cn/v1'),
    'deepseek': ('openai', 'https://api.deepseek.com'),
    'openai': ('openai', 'https://api.openai.com/v1'),
    'google': ('google', 'https://generativelanguage.googleapis.com/v1beta'),
    'anthropic': ('anthropic', 'https://api.anthropic.com/v1'),
    'kling': ('kling_omni', 'https://api-singapore.klingai.com'),
}
SOURCES = {
    'minimax': 'https://platform.minimax.io/docs/api-reference/text-anthropic-api',
    'kimi': 'https://platform.moonshot.cn/docs/guide/use-kimi-vision-model',
    'deepseek': 'https://api-docs.deepseek.com/guides/vision',
    'google': 'https://ai.google.dev/gemini-api/docs/video-understanding',
    'openai': 'https://developers.openai.com/api/docs/models/gpt-4.1',
    'anthropic': 'https://platform.claude.com/docs/en/about-claude/models/overview',
    'alibaba': 'https://help.aliyun.com/zh/model-studio/vision',
    'zhipu': 'https://docs.bigmodel.cn/cn/guide/models/vlm/glm-4.6v',
    'volcengine': 'https://docs.volcengine.com/docs/ark/create-video-generation-task-api?lang=zh',
    'kling': 'https://kling.ai/document-api/api/video/o1/video-omni/legacy',
}
# (exact API ID, label, documented input capabilities, adapter gaps by purpose)
PROFILES = {
    'minimax': [
        ('MiniMax-M3', 'MiniMax M3', ('image_input', 'video_input'), {}),
        ('MiniMax-M3.1-Flash-Preview', 'MiniMax M3.1 Flash Preview', ('image_input', 'video_input'), {}),
        ('MiniMax-M2.7', 'MiniMax M2.7', (), {}),
        ('MiniMax-H3', 'MiniMax H3', ('video_generation',), {}),
    ],
    'kimi': [('kimi-k2.6', 'Kimi K2.6', ('image_input', 'video_input'), {})],
    'deepseek': [('deepseek-flash', 'DeepSeek Flash', ('image_input',), {})],
    'openai': [
        ('gpt-4.1', 'GPT-4.1', ('image_input',), {}),
        ('text-embedding-3-small', 'Text Embedding 3 Small', ('embedding',), {}),
        ('text-embedding-3-large', 'Text Embedding 3 Large', ('embedding',), {}),
    ],
    'anthropic': [('claude-sonnet-5-5', 'Claude Sonnet 5.5', ('image_input',), {})],
    'alibaba': [('text-embedding-v4', 'Text Embedding v4', ('embedding',), {}), ('qwen3-vl-plus', 'Qwen3 VL Plus', ('image_input', 'video_input'),
                 {'video_input': '此型号的视频输入格式尚未接通'})],
    'zhipu': [('embedding-3', 'Embedding 3', ('embedding',), {}), ('glm-4.6v', 'GLM 4.6V', ('image_input', 'video_input'),
               {'video_input': '此型号的视频输入格式尚未接通'})],
    'google': [('gemini-3.8-flash', 'Gemini 3.8 Flash', ('image_input', 'video_input'),
                {'video_input': '当前文档的视频接口为 Interactions，本应用尚未接通此格式'})],
    'kling': [('kling-video-o1', 'Kling O1（视频URL，最多4图，无声）', ('video_generation',), {})],
    'volcengine': [('doubao-seedance-2-0-260128', 'Seedance 2.0（视频URL）', ('video_generation',), {}),
                  ('doubao-seedance-2-5-260628', 'Seedance 2.5（视频URL）', ('video_generation',), {}),
                  ('account-endpoint', '账户专属接入点', (),
                   {p: '接入点与具体型号的对应关系尚未核验' for p in PURPOSES})],
}

GENERATION_CONSTRAINTS = {
    'doubao-seedance-2-0-260128': {'reference_video_input': 'customer_https_url', 'max_reference_images_with_video': 6,
        'output_duration_seconds': [4, 15], 'resolutions': ['480p', '720p', '1080p'], 'requires_customer_media_url': True},
    'doubao-seedance-2-5-260628': {'reference_video_input': 'customer_https_url', 'max_reference_images_with_video': 6,
        'output_duration_seconds': [4, 15], 'resolutions': ['480p', '720p', '1080p'], 'requires_customer_media_url': True},
    'kling-video-o1': {'reference_video_input': 'customer_https_url', 'max_reference_images_with_video': 4,
        'output_duration_seconds': [3, 10], 'resolutions': ['720p', '1080p'], 'audio': False,
        'requires_customer_media_url': True, 'gap': '官方视频参考最多4图，六图同时参考未满足；不支持本地视频直接提交'},
}


def connection(provider, role):
    if provider not in CONNECTIONS:
        raise ValueError('未知模型厂商')
    if provider == 'minimax' and role == 'video_generation':
        return 'minimax_video', 'https://api.minimax.io'
    return CONNECTIONS[provider]


def check_connection(provider, role, protocol='', base_url=''):
    expected, default = connection(provider, role)
    if protocol and protocol != expected:
        raise ValueError('接口协议与厂商、用途不一致，请重新选择型号')
    if base_url:
        parsed = urlsplit(base_url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('模型地址必须是无凭证、无查询参数的 HTTPS 地址')
        # An old analysis URL must never be reused for MiniMax video generation.
        if provider == 'minimax' and parsed.hostname == 'api.minimax.io' and base_url.rstrip('/') != default:
            raise ValueError('接口地址与当前用途不一致，请恢复默认连接地址')
    return expected, base_url.rstrip('/') if base_url else default


def model_availability(binding):
    reason = ''
    try:
        check_connection(binding['provider'], binding['role'], binding.get('protocol', ''), binding.get('base_url', ''))
    except (ValueError, KeyError) as exc:
        reason = str(exc)
    if binding.get('enabled') is False:
        reason = '此配置已停用'
    profiles = PROFILES.get(binding.get('provider'), [])
    profile = next((m for m in profiles if m[0] == binding.get('model_id')), None)
    result = {}
    for purpose in PURPOSES:
        why = reason
        if not why and not profile:
            why = '此具体型号的能力尚未核验，请选择已列出的型号'
        if not why:
            _, _, caps, gaps = profile
            if purpose == 'strategy' and (set(caps) & {'image_input', 'video_input'} or profile[0] == 'MiniMax-M2.7'):
                caps = (*caps, 'strategy')
            why = gaps.get(purpose, '')
            if not why and purpose not in caps:
                why = f'此型号不支持{LABELS[purpose]}'
        role = binding.get('role')
        if not why and ((purpose in {'strategy', 'image_input', 'video_input'} and role not in {'strategy', 'image_input', 'video_input'})
                        or (purpose in {'video_generation', 'embedding'} and role != purpose)):
            why = '此配置的用途与当前任务不一致'
        result[purpose] = {'available': not bool(why), 'reason': why}
    # Task eligibility includes our explicit local preprocessing workflow. Keep
    # video_input reserved for implemented native-video transport capabilities.
    native, frames = result['video_input'], result['image_input']
    route = 'native_video' if native['available'] else 'sampled_frames' if frames['available'] else None
    result['video_analysis'] = {
        'available': route is not None,
        'reason': '' if route else frames['reason'] or native['reason'],
        'route': route,
        'label': '原生视频输入' if route == 'native_video' else '内置抽帧与时间戳文字稿' if route else '',
    }
    return result


def require_model(binding, purpose):
    status = model_availability(binding)
    item = status.get(purpose, {'available': False, 'reason': '此用途的工作流尚未接通'})
    if not item['available']:
        raise ValueError(item['reason'])


def model_profiles(provider):
    result = []
    for model_id, name, caps, gaps in PROFILES.get(provider, []):
        availability = {}
        for purpose in TASK_PURPOSES:
            b = {'provider': provider, 'role': 'strategy' if purpose == 'video_analysis' else purpose, 'model_id': model_id}
            availability[purpose] = model_availability(b)[purpose]
        result.append(dict(id=model_id, name=name, availability=availability,
                           source=SOURCES[provider], verified_at='2026-10-19', live_verified=False,
                           **({'generation_constraints': GENERATION_CONSTRAINTS[model_id]} if model_id in GENERATION_CONSTRAINTS else {})))
    return result
