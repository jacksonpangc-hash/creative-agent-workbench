"""Bounded local file parsing, with no URL resolution, service access or persistence."""
from __future__ import annotations

import csv
import io
import json
import math
import re
from datetime import date, datetime

from .store import now, reject_secrets

METRICS = {'likes', 'comments', 'shares', 'favorites', 'views', 'followers'}
ALIASES = {'标题':'title', '作者':'author', '链接':'url', 'source_url':'url', '来源链接':'url',
           '发布时间':'published_at', '采集时间':'captured_at', '时长':'duration_seconds',
           '点赞':'likes', '评论':'comments', '分享':'shares', '收藏':'favorites', '播放':'views', '粉丝':'followers'}
FIELDS = METRICS | {'url', 'title', 'author', 'captured_at', 'published_at', 'duration_seconds', 'metrics'}
METRIC_FIELDS = {'name', 'raw', 'basis', 'as_of', 'value', 'lower', 'upper', 'unit', 'period', 'scope', 'nature'}


def timestamp(value, label, allow_date=False):
    try:
        if allow_date and isinstance(value, str) and len(value) == 10:
            date.fromisoformat(value)
        elif datetime.fromisoformat(value).tzinfo is None:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError(f'{label}格式无效，请使用 ISO 日期或含时区的时间' if allow_date else f'{label}须为含时区的 ISO 时间') from None
    return value


def parse_rows(format, content):
    if not isinstance(content, str) or len(content) > 1_000_000:
        raise ValueError('导入内容过大，请分成每批最多 200 条、1 MB 以内')
    content = content.lstrip('\ufeff')
    if format == 'text':
        rows = [{'url': line.strip(), 'title':re.split(r'https?://', line, maxsplit=1)[0].strip()}
                for line in content.splitlines() if line.strip()]
    elif format == 'json':
        try:
            rows = json.loads(content)
        except (ValueError, RecursionError):
            raise ValueError('JSON 无法读取，请使用对象数组，每条记录提供 url') from None
        if not isinstance(rows, list):
            raise ValueError('JSON 顶层必须是记录数组')
    elif format == 'csv':
        try:
            reader = csv.DictReader(io.StringIO(content), strict=True)
            headers = reader.fieldnames
            if not headers or len(headers) != len(set(headers)) or any(not h.strip() for h in headers):
                raise ValueError('CSV 需要非空且不重复的列名')
            rows = []
            for row in reader:
                if None in row or any(v is None for v in row.values()):
                    rows.append(None)  # A row error is shown in preview; never truncate silently.
                else:
                    rows.append(row)
                if len(rows) > 200:
                    break
        except csv.Error:
            raise ValueError('CSV 无法读取，请检查引号、分隔符和列数') from None
    else:
        raise ValueError('仅支持分享文案、CSV 或 JSON')
    if not 1 <= len(rows) <= 200:
        raise ValueError('每批请输入 1–200 条参考')
    return rows


def imported_observation(row):
    if not isinstance(row, dict) or any(not isinstance(k, str) for k in row):
        raise TypeError('记录必须是对象；CSV 每行列数须与表头一致')
    # Reject credential-shaped content rather than persisting it, including unsupported columns.
    reject_secrets(row)
    normalized, ignored, mapping = {}, [], {}
    for key, value in row.items():
        field = ALIASES.get(key.strip(), key.strip())
        if field not in FIELDS:
            ignored.append(key)
            continue
        if field in normalized:
            raise ValueError(f'{field} 有重复映射，请只保留一列')
        normalized[field] = value
        mapping[key] = field
    url = normalized.get('url')
    if not isinstance(url, str):
        raise TypeError('缺少链接，请添加 url 或「链接」列')
    # Multiline share text is one selected item per line; do not silently discard a second URL.
    if len(re.findall(r'https?://', url)) != 1:
        raise ValueError('每条记录只保留一个抖音或快手 HTTPS 链接')
    captured_value = normalized.get('captured_at')
    captured = timestamp(now() if captured_value in (None, '') else captured_value, '采集时间')
    published = normalized.get('published_at')
    if published in (None, ''):
        published = None
    else:
        timestamp(published, '发布时间', allow_date=True)
    title, author = normalized.get('title', ''), normalized.get('author', '')
    title = '' if title is None else title
    author = '' if author is None else author
    if not isinstance(title, str) or not isinstance(author, str) or len(title) > 2000 or len(author) > 300:
        raise ValueError('标题须为 2000 字以内，作者须为 300 字以内的文本')
    duration = normalized.get('duration_seconds')
    if duration in ('', None):
        duration = None
    else:
        try:
            if isinstance(duration, bool):
                raise TypeError()
            duration = float(duration)
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError('时长须为大于零的秒数；未知时留空') from None
    metrics = normalized.get('metrics')
    if metrics is None or metrics == '':
        metrics = []
    if not isinstance(metrics, list) or len(metrics) > 6:
        raise ValueError('metrics 须为最多 6 项的指标数组')
    metrics = list(metrics)
    for name in sorted(METRICS):
        value = normalized.get(name)
        if value not in (None, ''):
            metrics.append({'name':name, 'raw':value})
    output, names = [], set()
    for metric in metrics:
        if not isinstance(metric, dict) or metric.get('name') not in METRICS or 'raw' not in metric:
            raise ValueError('指标须提供 name 和 raw；仅支持点赞、评论、分享、收藏、播放、粉丝')
        name = metric['name']
        if name in names:
            raise ValueError(f'{name} 指标重复，请保留一项')
        names.add(name)
        ignored.extend(f'metrics.{name}.{key}' for key in metric if key not in METRIC_FIELDS)
        raw = metric['raw']
        if isinstance(raw, bool) or not isinstance(raw, (str, int, float)) or (isinstance(raw, (int, float)) and not math.isfinite(raw)):
            raise ValueError(f'{name} 须提供原始文本或有限数值；未知时留空')
        raw = str(raw).strip()
        if not raw:
            continue
        if len(raw) > 100 or (raw.startswith('-') and raw not in {'-', '--'}):
            raise ValueError(f'{name} 原文无效；指标不能是负数')
        state = 'unknown' if raw.lower() in {'unknown', 'null', 'n/a', '未知', '未提供', '--', '—', '-'} else 'range' if re.search(r'\d.*(?:[-–—~～至]|\+|以上|以下|[<>≤≥]).*', raw) or re.match(r'[<>≤≥]', raw) else 'reported'
        extra = {k:v for k,v in metric.items() if k in METRIC_FIELDS - {'name','raw','basis','as_of'}}
        for key in ('value', 'lower', 'upper'):
            if key in extra and (isinstance(extra[key], bool) or not isinstance(extra[key], (int, float)) or not math.isfinite(extra[key]) or extra[key] < 0):
                raise ValueError(f'{name} 的 {key} 须为非负有限数值')
        if 'lower' in extra and 'upper' in extra and extra['lower'] > extra['upper']:
            raise ValueError(f'{name} 区间下限不能大于上限')
        if 'lower' in extra or 'upper' in extra:
            state = 'range'
        if state in {'range', 'unknown'} and 'value' in extra:
            raise ValueError(f'{name} 为区间或未知值，不能同时填写精确 value')
        for key in ('unit', 'period', 'scope', 'nature'):
            if key in extra and (not isinstance(extra[key], str) or len(extra[key]) > 200):
                raise ValueError(f'{name} 的 {key} 须为 200 字以内文本')
        output.append({'name':name, 'raw':raw, 'basis':'user_import',
                       'as_of':timestamp(captured if metric.get('as_of') in (None, '') else metric['as_of'], '指标观察时间'), 'state':state, **extra})
    return url, {'title':title, 'author':author, 'captured_at':captured, 'published_at':published,
                 'duration_seconds':duration, 'metrics':output, 'links':[], 'source':'user_import',
                 'commerce_metrics':None,
                 'field_provenance':{field:'user_import' for field in normalized if field != 'metrics'},
                 'field_mapping':mapping}, ignored
