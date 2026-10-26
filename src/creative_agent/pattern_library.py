"""Pinned, offline creative structure references; no commercial evidence or media.

The CC BY projection omits original dialogue and full prompts. The corpus must
remain separate from project facts and licensed commercial supplier responses.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from copy import deepcopy
from pathlib import Path

_DIRECTORY = Path(__file__).parent / 'data' / 'clipcat_patterns'
_COMMIT = '6afd5abb040768c7cb41a70b835bb0711971941e'
_UPSTREAM_SHA256 = '1ec9da8857fed927552b09fdf603a62262b145c4597508d1712a5620d5e68594'
_MANIFEST_SHA256 = 'b0f48f30af6930d6f9cecd0dfbdc43f42fd5840bc90310c6576e38dab0aa2924'
_REPOSITORY = 'https://github.com/Clipcat-ai/awesome-tiktok-shop-video-prompts'
_FIELDS = frozenset({
    'id', 'market', 'category', 'category_slug', 'video_type', 'duration_sec',
    'hook', 'hooks', 'presenter', 'promo', 'pain_point', 'visuals', 'source',
    'creator', 'published', 'clipcat_url',
})
_QUALITY = {
    'reference_kind': 'historical_creative_structure',
    'not_performance_evidence': True,
    'metrics_status': 'not_provided',
    'human_review_status': 'not_individually_reviewed',
    'source_video_verification': 'not_run',
}
_LABELS = {
    'br': '巴西 brazil', 'de': '德国 germany', 'es': '西班牙 spain',
    'fr': '法国 france', 'gb': '英国 uk united kingdom', 'id': '印度尼西亚 印尼 indonesia',
    'it': '意大利 italy', 'jp': '日本 japan', 'mx': '墨西哥 mexico',
    'my': '马来西亚 malaysia', 'ph': '菲律宾 philippines', 'sg': '新加坡 singapore',
    'th': '泰国 thailand', 'us': '美国 usa united states', 'vn': '越南 vietnam',
    'automotive-motorcycle': '汽车 摩托车 汽摩 汽车摩托车',
    'baby-maternity': '母婴 孕产 婴童',
    'beauty-personal-care': '美妆个护 美妆 护肤 美容 化妆品 美妆护肤',
    'books-magazines-audio': '图书 杂志 音像 书籍',
    'collectibles': '收藏品 收藏',
    'computers-office-equipment': '电脑 办公 办公设备',
    'fashion-accessories': '时尚配饰 配饰 饰品',
    'food-beverages': '食品 饮料 食饮',
    'furniture': '家具', 'health': '健康 保健',
    'home-improvement': '家装 家居装修 装修',
    'home-supplies': '家居用品 家居 日用 居家用品',
    'household-appliances': '家用电器 家电 小家电',
    'jewelry-accessories-derivatives': '珠宝 首饰 珠宝首饰',
    'kids-fashion': '童装 儿童服饰',
    'kitchenware': '厨房 厨具 餐具 厨房用品',
    'luggage-bags': '箱包 旅行箱 包袋',
    'menswear-underwear': '男装 男士内衣 男士服装',
    'pet-supplies': '宠物 宠物用品',
    'phones-electronics': '手机 数码 电子产品 消费电子',
    'shoes': '鞋 鞋靴 鞋子', 'sports-outdoor': '运动 户外 运动户外',
    'textiles-soft-furnishings': '家纺 软装 纺织品',
    'tools-hardware': '工具 五金 五金工具',
    'toys-hobbies': '玩具 兴趣 爱好',
    'womenswear-underwear': '女装 女士内衣 女士服装',
    'pre-owned': '二手 二手商品', 'muslim-fashion': '穆斯林服饰 穆斯林时尚',
    'bookings-vouchers': '预订 代金券 礼券', 'virtual-products': '虚拟商品 数字商品',
    'real-review': '真人测评 真实测评 测评 评测',
    'talking-head': '人物口播 讲解 出镜讲解',
    'handheld-demo': '手持演示 手持展示 演示',
    'ootd': '穿搭 穿搭展示', 'lifestyle-scene': '生活场景 生活方式 场景',
    'unboxing-pov': '开箱 第一视角开箱', 'promo-pitch': '促销推介 促销 推介',
    'story-skit': '情景短剧 短剧 剧情', 'product-closeup': '产品特写 特写',
    'asmr': '沉浸式 声音体验', 'brand-tvc': '品牌广告 品牌宣传',
    'product-compare': '产品对比 对比', 'outfit-transform': '换装 变装',
    'pain-point': '痛点 痛点开场', 'before-after': '前后对比',
    'result-first': '结果先行 结果前置 先展示结果',
    'contrarian': '反常识 反向观点', 'benefit-first': '利益先行 利益前置',
    'curiosity': '好奇 悬念', 'identity': '身份认同 身份',
    'pov-scenario': '第一视角 情境代入', 'authority': '社会证明 权威',
    'urgency-promo': '紧迫感 限时促销', 'sensory-asmr': '感官 沉浸开场',
    'skit-conflict': '冲突开场 冲突', 'weak-generic': '普通开场 弱开场',
    'real-person': '真人 单人出镜', 'multi-person': '多人 多人出镜',
    'hands-only': '仅手部 手部出镜 手部', 'no-person': '无人 无人出镜',
    'discount': '折扣', 'bundle': '组合 组合装', 'gwp': '赠品',
    'coupon': '优惠券', 'none': '无促销',
}
_CHINESE_ALIASES = {
    alias: code for code, text in _LABELS.items() for alias in text.split()
    if re.search(r'[\u3400-\u9fff]', alias)
}
_CHINESE_PATTERN = re.compile('|'.join(sorted(_CHINESE_ALIASES, key=len, reverse=True)))
_FILTER_ALIASES = {'uk': 'gb', 'usa': 'us', **_CHINESE_ALIASES}


def _text(value: str) -> str:
    return unicodedata.normalize('NFKC', value).casefold().strip()


def _read(directory: Path, name: str, maximum: int) -> bytes:
    with (directory / name).open('rb') as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError('参考库文件超出大小限制')
    return data


class PatternLibrary:
    """Load one immutable package and return defensive copies of local references."""

    def __init__(self, data_dir: Path | None = None):
        directory = Path(data_dir) if data_dir is not None else _DIRECTORY
        manifest_raw = _read(directory, 'manifest.json', 128_000)
        if hashlib.sha256(manifest_raw).hexdigest() != _MANIFEST_SHA256:
            raise ValueError('参考库来源清单校验失败')
        manifest = json.loads(manifest_raw)
        if (manifest.get('schema_version') != 1 or manifest.get('content_file') != 'patterns.json'
                or manifest.get('provider') != 'clipcat_public_patterns'
                or manifest.get('repository') != _REPOSITORY or manifest.get('commit') != _COMMIT
                or manifest.get('upstream_sha256') != _UPSTREAM_SHA256
                or manifest.get('license') != 'CC-BY-4.0'
                or set(manifest.get('projection_fields', [])) != _FIELDS
                or any(manifest.get(key) != value for key, value in _QUALITY.items())):
            raise ValueError('参考库来源、字段或许可不受支持')
        raw = _read(directory, 'patterns.json', 2_000_000)
        if hashlib.sha256(raw).hexdigest() != manifest.get('content_sha256'):
            raise ValueError('参考库内容校验失败')
        for name in ('LICENSE', 'NOTICE.md'):
            digest = hashlib.sha256(_read(directory, name, 128_000)).hexdigest()
            if digest != manifest.get('files', {}).get(name, {}).get('sha256'):
                raise ValueError('参考库许可文件校验失败')
        rows = json.loads(raw)
        if not isinstance(rows, list) or len(rows) != manifest.get('record_count') or len(rows) != 647:
            raise ValueError('参考库条目数校验失败')
        for row in rows:
            if (not isinstance(row, dict) or set(row) != _FIELDS
                    or not isinstance(row.get('id'), str)
                    or not isinstance(row.get('source'), str)
                    or not re.fullmatch(r'https://www\.tiktok\.com/share/video/\d+', row['source'])):
                raise ValueError('参考库条目包含不受支持的字段或来源')
        if len({row['id'] for row in rows}) != len(rows):
            raise ValueError('参考库条目标识重复')
        self._source = manifest
        self._rows = rows
        self._by_id = {row['id']: row for row in rows}
        self._index = [(row, self._search_text(row)) for row in rows]

    @staticmethod
    def _search_text(row: dict) -> str:
        values = [row['id'], row['market'], row['category'], row['category_slug'],
                  row['video_type'], row['hook'], row['presenter'], row['creator'],
                  row['pain_point'] or '', *row['hooks'], *row['promo'], *row['visuals']]
        labels = [_LABELS.get(value, '') for value in values]
        return _text(' '.join([*values, *labels]))

    def _item(self, row: dict) -> dict:
        return deepcopy({**row, **_QUALITY, 'library_source': self._source})

    @staticmethod
    def _matches(row: dict, text: str, terms: list[str]) -> bool:
        codes = {row['market'], row['category'], row['video_type'], row['hook'], row['presenter'],
                 *row['hooks'], *row['promo']}
        return all(term in codes if term in _LABELS else term in text for term in terms)

    def search(self, query: str = '', market: str = '', category: str = '',
               offset: int = 0, limit: int = 20) -> dict:
        """AND-match local text/Chinese structure aliases, with stable source ordering."""
        if (not all(isinstance(value, str) for value in (query, market, category))
                or type(offset) is not int or offset < 0
                or type(limit) is not int or not 1 <= limit <= 100):
            raise ValueError('检索词和筛选需为文本，起点需非负，每页需为1至100条')
        market = _text(market)
        category = _text(category)
        market = _FILTER_ALIASES.get(market, market)
        category = _FILTER_ALIASES.get(category, category)
        normalized = _CHINESE_PATTERN.sub(lambda match: f' {_CHINESE_ALIASES[match.group()]} ', _text(query))
        terms = [term for term in re.split(r'[\s,，;；]+', normalized) if term]
        rows = [row for row, text in self._index
                if (not market or row['market'] == market)
                and (not category or category in (row['category'], row['category_slug']))
                and self._matches(row, text, terms)]
        page = rows[offset:offset + limit]
        next_offset = offset + len(page)
        return {'items': [self._item(row) for row in page], 'total': len(rows),
                'next_offset': next_offset if next_offset < len(rows) else None,
                'source': deepcopy(self._source)}

    def get(self, pattern_id: str) -> dict | None:
        """Return one source-linked reference, or None for an unknown source id."""
        if not isinstance(pattern_id, str):
            raise TypeError('参考条目标识需为文本')
        row = self._by_id.get(pattern_id)
        return self._item(row) if row is not None else None
