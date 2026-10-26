"""Scoped own-footage retrieval from usable timed text and stored observations."""
import re


def tokens(text):
    chunks = re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]+', text.lower())
    return [token for c in chunks for token in ([c] if not re.search('[\u4e00-\u9fff]', c)
        else [c[i:i+2] for i in range(max(1, len(c)-1))])]


class FootageSearch:
    def __init__(self, store, media):
        self.store, self.media = store, media

    def search(self, p, query, selection, *, sku_id='', product_id='', limit=50):
        if selection.get('project_id', p) != p:
            raise ValueError('检索项目范围不一致')
        from .knowledge_index import KnowledgeIndex
        index = getattr(self.store, 'knowledge_index', None) or KnowledgeIndex(self.store, self.media, embeddings=None)
        ids = []
        for frozen in selection.get('own_assets', []):
            try:
                asset = self.store.get(p, 'asset', frozen['id'])
            except ValueError:
                continue
            if asset.get('sha256') == frozen.get('sha256'):
                ids.append(asset['id'])
        found = index.search(p, query, pool='own', selected_asset_ids=ids,
                             product_id=product_id, sku_id=sku_id, limit=min(limit, 40))
        return [{**row, 'score': row['rrf_score']} for row in found]
