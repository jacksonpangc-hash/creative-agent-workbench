"""User-assisted public references; network acquisition is an explicit separate service."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from threading import RLock
from urllib.parse import parse_qs, quote, urlsplit, urlunsplit

from .store import now, reject_secrets

_HOSTS = {'www.douyin.com':'douyin', 'douyin.com':'douyin', 'www.iesdouyin.com':'douyin',
          'iesdouyin.com':'douyin', 'v.douyin.com':'douyin', 'www.kuaishou.com':'kuaishou',
          'kuaishou.com':'kuaishou', 'v.kuaishou.com':'kuaishou'}
_METRICS = {'likes', 'comments', 'shares', 'favorites', 'views', 'followers'}


def identity(value):
    match = re.search(r'https://[^\s<>"\u3002\uff0c]+', value)
    if not match:
        raise ValueError('请提供抖音或快手的公开 HTTPS 链接')
    parsed = urlsplit(match.group(0).rstrip('.,;，。；）)'))
    if parsed.scheme != 'https' or parsed.hostname not in _HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('仅接受抖音或快手公开链接，不接受账号凭证或其他地址')
    host = parsed.hostname
    platform = _HOSTS[host]
    entity_type, platform_id = 'unknown', None
    if not host.startswith('v.'):
        patterns = [(r'/(?:share/)?video/(\d+)(?:/|$)', 'video'), (r'/user/([A-Za-z0-9_-]+)(?:/|$)', 'account')] if platform == 'douyin' else [(r'/short-video/([A-Za-z0-9_-]+)(?:/|$)', 'video'), (r'/profile/([A-Za-z0-9_-]+)(?:/|$)', 'account')]
        for pattern, kind in patterns:
            found = re.fullmatch(pattern, parsed.path)
            if found:
                platform_id, entity_type = found[1], kind
                break
        if platform == 'douyin' and not platform_id:
            modal = parse_qs(parsed.query).get('modal_id', [''])[0]
            if re.fullmatch(r'\d+', modal):
                platform_id, entity_type = modal, 'video'
    if platform_id:
        canonical = f'https://www.douyin.com/{"video" if entity_type == "video" else "user"}/{platform_id}' if platform == 'douyin' else f'https://www.kuaishou.com/{"short-video" if entity_type == "video" else "profile"}/{platform_id}'
    else:
        canonical = urlunsplit(('https', host, parsed.path.rstrip('/') or '/', '', ''))
    return {'platform': platform, 'entity_type': entity_type, 'platform_entity_id': platform_id,
            'identity_state': 'resolved' if platform_id else 'pending', 'url': canonical}


def observation(capture):
    reject_secrets(capture)
    if set(capture) - {'url', 'title', 'author', 'metrics', 'links', 'captured_at'}:
        raise ValueError('收集内容含不支持的字段')
    metrics = capture.get('metrics', [])
    if not isinstance(metrics, list) or len(metrics) > 6:
        raise ValueError('公开指标格式无效')
    for metric in metrics:
        if not isinstance(metric, dict) or set(metric) != {'name', 'raw', 'basis', 'as_of'} or metric['name'] not in _METRICS or metric['basis'] != 'visible_page' or not isinstance(metric['raw'], str) or len(metric['raw']) > 100:
            raise ValueError('仅接受页面可见互动指标原文，不推断销售额')
        try:
            timestamp = datetime.fromisoformat(metric['as_of'])
            if timestamp.tzinfo is None:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ValueError('公开指标必须注明含时区的观察时间') from None
    title, author = capture.get('title', ''), capture.get('author', '')
    if not isinstance(title, str) or not isinstance(author, str) or len(title) > 2000 or len(author) > 300:
        raise ValueError('页面标题或作者过长')
    links = capture.get('links', [])
    if not isinstance(links, list) or len(links) > 50:
        raise ValueError('公开链接数量无效')
    from .discovery_import import timestamp
    captured_at = timestamp(capture.get('captured_at', now()), '采集时间')
    return {'title': title, 'author': author, 'metrics': metrics,
            'links': list(dict.fromkeys(identity(link)['url'] for link in links)),
            'captured_at': captured_at, 'source': 'user_active_browser', 'commerce_metrics': None}


class Discovery:
    def __init__(self, store):
        self.store = store
        self.lock = RLock()

    def create_plan(self, project_id, query, platforms):
        query = query.strip()
        if not query or len(query) > 200 or not platforms or len(platforms) > 2 or any(p not in {'douyin', 'kuaishou'} for p in platforms):
            raise ValueError('请输入 1–200 字关键词并选择抖音或快手')
        links = [{'platform': p, 'url': f'https://www.douyin.com/search/{quote(query, safe="")}' if p == 'douyin' else f'https://www.kuaishou.com/search/video?searchKey={quote(query, safe="")}'} for p in dict.fromkeys(platforms)]
        return self.store.put(project_id, 'discovery_plan', {'query': query, 'platforms': list(dict.fromkeys(platforms)), 'links': links, 'mode': 'user_assisted', 'cost': 'free_public_discovery', 'status': 'awaiting_user_search'})

    def plans(self, project_id):
        self.store.project(project_id)
        return self.store.list(project_id, 'discovery_plan')

    def _view(self, project_id, reference):
        exists = False
        if reference.get('asset_id'):
            try:
                asset = self.store.get(project_id, 'asset', reference['asset_id'])
                exists = bool(asset.get('path')) and Path(asset['path']).is_file() and asset.get('inspection') not in {'pending', 'failed'}
            except ValueError:
                pass
        return {**reference, 'media_state': 'available' if exists else 'missing', 'can_visually_analyze': exists}

    def references(self, project_id):
        self.store.project(project_id)
        return [self._view(project_id, row) for row in self.store.list(project_id, 'public_reference') if not row.get('merged_into')]

    def reference(self, project_id, reference_id):
        """Resolve preserved short-link aliases within exactly this project."""
        seen = set()
        while reference_id not in seen:
            seen.add(reference_id)
            row = self.store.get(project_id, 'public_reference', reference_id)
            if not row.get('merged_into'):
                return self._view(project_id, row)
            reference_id = row['merged_into']
        raise ValueError('参考关联无效，请重新选择作品')

    def resolve_identity(self, project_id, reference_id, resolved):
        """Merge only an extractor/redirect-verified work ID, never a similar title."""
        checked = identity(resolved['url'])
        if (checked['entity_type'] != 'video' or checked['identity_state'] != 'resolved'
                or any(checked[k] != resolved.get(k) for k in ('platform', 'entity_type', 'platform_entity_id'))):
            raise ValueError('作品身份未通过核对')
        with self.lock:
            reference = self.reference(project_id, reference_id)
            if reference['platform'] != checked['platform'] or (reference.get('platform_entity_id')
                    and reference['platform_entity_id'] != checked['platform_entity_id']):
                raise ValueError('作品身份与所选参考不一致')
            target = next((row for row in self.references(project_id)
                           if row['id'] != reference['id'] and self._key(row) == self._key(checked)), reference)
            aliases = list(dict.fromkeys([*target.get('source_urls', []), target['url'],
                                          *reference.get('source_urls', []), reference['url']]))
            original = None
            if target['id'] != reference['id']:
                observations = [*target.get('observations', [])]
                for observed in reference.get('observations', []):
                    if observed not in observations:
                        observations.append(observed)
                target = {**target, 'observations': observations}
                # Preserve the original row for old links/jobs; no history deletion.
                original = self.store.get(project_id, 'public_reference', reference['id'])
            target = {**target, **checked, 'source_urls': aliases}
            target.pop('media_state', None)
            target.pop('can_visually_analyze', None)
            saved = self.store.put(project_id, 'public_reference', target, target['id'])
            # Save combined observations before hiding the alias. An interruption
            # can leave a visible duplicate, but cannot hide unsaved observations.
            if original is not None:
                self.store.put(project_id, 'public_reference', {**original, 'merged_into': target['id']}, original['id'])
            return self._view(project_id, saved)

    def collect(self, project_id, captures, channel="active_tab"):
        self.store.project(project_id)
        if channel not in {'active_tab', 'manual_link'}:
            raise ValueError('未知采集渠道')
        if not isinstance(captures, list) or not 1 <= len(captures) <= 50:
            raise ValueError('每次收集 1–50 条用户选中的公开参考')
        # Validate the complete batch before writing any record.
        validated = [(identity(c['url']), observation(c)) for c in captures]
        for _ident, obs in validated:
            obs['source'] = 'user_active_browser' if channel == 'active_tab' else 'user_manual_link'
        return self._save(project_id, validated, channel)

    @staticmethod
    def _key(ident):
        return ident['platform'], ident['entity_type'], ident.get('platform_entity_id') or ident['url']

    def _save(self, project_id, validated, channel):
        output = []
        with self.lock:
            rows = {self._key(r): r for r in self.references(project_id)}
            aliases = {url: row for row in rows.values() for url in row.get('source_urls', [])}
            for ident, obs in validated:
                key = self._key(ident)
                old = rows.get(key) or aliases.get(ident['url'])
                if old and old.get('identity_state') == 'resolved' and ident['identity_state'] == 'pending':
                    ident = {k: old[k] for k in ('platform', 'entity_type', 'platform_entity_id', 'identity_state', 'url')}
                    key = self._key(ident)
                data = {**(old or {}), **ident,
                        'supplier': (old or {}).get('supplier', 'user_import' if channel == 'user_import' else 'public_browser'),
                        'collection_channel': (old or {}).get('collection_channel', channel),
                        'source_kind': (old or {}).get('source_kind', 'inspiration'),
                        'usage': (old or {}).get('usage', 'reference_only'),
                        'title': obs['title'] or (old or {}).get('title', ''),
                        'author': obs['author'] or (old or {}).get('author', ''),
                        'observations': [*(old or {}).get('observations', []), obs]}
                for field in ('published_at', 'duration_seconds'):
                    if obs.get(field) is not None:
                        data[field] = obs[field]
                saved = self.store.put(project_id, 'public_reference', data, old['id'] if old else None)
                rows[key] = saved
                output.append(self._view(project_id, saved))
        return output

    def preview_import(self, project_id, format, content):
        from .discovery_import import imported_observation, parse_rows
        self.store.project(project_id)
        existing = {self._key(r): r for r in self.references(project_id)}
        seen, output, ignored, mappings = set(), [], [], {}
        for index, row in enumerate(parse_rows(format, content), 1):
            try:
                url, obs, unknown = imported_observation(row)
                ident = identity(url)
                # Keep the supplied platform URL without tracking/private query parameters.
                # Identity is canonical; the source path can differ (e.g. iesdouyin share pages).
                parsed = urlsplit(re.search(r'https://[^\s<>"\u3002\uff0c]+', url).group(0).rstrip('.,;，。；）)'))
                obs['source_url'] = urlunsplit(('https', parsed.hostname, parsed.path, '', ''))
                key = self._key(ident)
                old = existing.get(key)
                preview = {**(old or {}), **ident, 'title':obs['title'] or (old or {}).get('title', ''),
                           'author':obs['author'] or (old or {}).get('author', ''),
                           'published_at':obs.get('published_at') or (old or {}).get('published_at'),
                           'duration_seconds':obs.get('duration_seconds') or (old or {}).get('duration_seconds'),
                           'observations':[obs], 'media_state':(old or {}).get('media_state', 'missing'),
                           'can_visually_analyze':(old or {}).get('can_visually_analyze', False)}
                output.append({'row':index, 'reference':preview, 'errors':[],
                               'duplicate':'existing' if old else 'batch' if key in seen else None})
                seen.add(key)
                ignored.extend(unknown)
                mappings.update(obs['field_mapping'])
            except (ValueError, TypeError, KeyError) as error:
                output.append({'row':index, 'reference':None, 'errors':[str(error)], 'duplicate':None})
        return {'rows':output, 'ignored_columns':list(dict.fromkeys(ignored)), 'field_mapping':mappings,
                'can_confirm':bool(output) and all(not row['errors'] for row in output)}

    def import_references(self, project_id, format, content, ignore_unknown=False):
        # Re-validate original input on confirmation, never trust client-supplied normalized rows.
        preview = self.preview_import(project_id, format, content)
        if not preview['can_confirm']:
            raise ValueError('仍有无效记录，请按预览中的行号修正后重新预览；尚未导入任何参考')
        if preview['ignored_columns'] and not ignore_unknown:
            raise ValueError('请确认忽略预览列出的未支持字段，或修改表头后重新预览')
        validated = []
        for row in preview['rows']:
            ref = row['reference']
            ident = {k:ref[k] for k in ('platform', 'entity_type', 'platform_entity_id', 'identity_state', 'url')}
            validated.append((ident, ref['observations'][0]))
        return self._save(project_id, validated, 'user_import')

    def attach(self, project_id, reference_id, asset_id):
        reference = self.reference(project_id, reference_id)
        asset = self.store.get(project_id, 'asset', asset_id)
        if not asset.get('path') or not Path(asset['path']).is_file() or asset.get('inspection') in {'pending', 'failed'}:
            raise ValueError('请先导入有权使用的视频文件并完成本地处理；公开链接不能直接进行画面分析')
        reference['asset_id'] = asset_id
        return self._view(project_id, self.store.put(project_id, 'public_reference', reference, reference['id']))
