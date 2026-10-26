"""Pinned, offline-only Chinese FastEmbed runtime. No BYOK or network fallback."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
from threading import RLock


MODEL_ID = 'BAAI/bge-small-zh-v1.5'
MODEL_REVISION = '46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59'
RUNTIME_VERSIONS = {'fastembed': '0.7.3', 'qdrant-client': '1.15.1', 'onnxruntime': '1.23.2', 'tokenizers': '0.23.3'}


class EmbeddingUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class EmbeddingSpace:
    model_id: str = MODEL_ID
    weights_revision: str = MODEL_REVISION
    tokenizer_revision: str = MODEL_REVISION
    dimensions: int = 512
    normalized: bool = True
    query_prefix: str = ''
    chunk_rule_version: str = 'tokenizer-offset-448-overlap48-v1'
    delivery: str = 'local-offline'

    @property
    def id(self):
        return 'space-' + hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:24]


class LocalEmbeddings:
    def __init__(self, model_dir: Path | str):
        self.model_dir = Path(model_dir).resolve()
        self.space = EmbeddingSpace()
        self._tokenizer = None
        self._model = None
        self._lock = RLock()
        self._verified = False

    def _verify(self):
        if self._verified:
            return
        try:
            manifest = json.loads((self.model_dir / 'manifest.json').read_text())
            if manifest.get('model_id') != MODEL_ID or manifest.get('revision') != MODEL_REVISION:
                raise ValueError('离线模型版本不匹配')
            files = manifest['files']
            if not {'model_optimized.onnx', 'tokenizer.json', 'tokenizer_config.json', 'config.json', 'special_tokens_map.json'} <= set(files):
                raise ValueError('离线模型文件不完整')
            for name, digest in files.items():
                path = (self.model_dir / name).resolve()
                if path.parent != self.model_dir or not path.is_file():
                    raise ValueError('离线模型文件路径无效')
                with path.open('rb') as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                        raise ValueError('离线模型文件校验失败')
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise EmbeddingUnavailable(f'暂使用关键词查找：{exc}') from exc
        self._verified = True

    def _get_tokenizer(self):
        with self._lock:
            self._verify()
            if self._tokenizer is None:
                try:
                    from tokenizers import Tokenizer
                    self._tokenizer = Tokenizer.from_file(str(self.model_dir / 'tokenizer.json'))
                    self._tokenizer.no_truncation()
                    self._tokenizer.no_padding()
                except (ImportError, OSError, ValueError) as exc:
                    raise EmbeddingUnavailable(f'本地分词器不可用：{exc}') from exc
            return self._tokenizer

    def split_text(self, text: str, *, max_tokens=448, overlap=48) -> list[str]:
        """Use actual token offsets; preserve all input tokens, including the tail."""
        if not isinstance(text, str) or not text.strip():
            return []
        if not 0 <= overlap < max_tokens <= 448:
            raise ValueError('片段 token 上限无效')
        tokenizer = self._get_tokenizer()
        with self._lock:
            encoded = tokenizer.encode(text, add_special_tokens=False)
        offsets = [(a, b) for a, b in encoded.offsets if b > a]
        chunks = []
        for start in range(0, len(offsets), max_tokens - overlap):
            end = min(len(offsets), start + max_tokens)
            chunk = text[offsets[start][0]:offsets[end - 1][1]].strip()
            if chunk:
                chunks.append(chunk)
            if end == len(offsets):
                break
        return chunks

    def _get_model(self):
        with self._lock:
            self._verify()
            if self._model is None:
                try:
                    from importlib.metadata import version
                    for distribution, pinned in RUNTIME_VERSIONS.items():
                        if version(distribution) != pinned:
                            raise ValueError(f'{distribution} 需要固定版本 {pinned}')
                    from fastembed import TextEmbedding
                    self._model = TextEmbedding(model_name=MODEL_ID, specific_model_path=str(self.model_dir),
                        local_files_only=True, threads=2, providers=['CPUExecutionProvider'],
                        cache_dir=str(self.model_dir.parent / 'runtime-cache'))
                except Exception as exc:
                    raise EmbeddingUnavailable(f'本地语义模型不可用：{exc}') from exc
            return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        tokenizer = self._get_tokenizer()
        with self._lock:
            if any(len(tokenizer.encode(t, add_special_tokens=True).ids) > 512 for t in texts):
                raise ValueError('文本超过模型输入长度，请先按实际 tokenizer 切分')
            vectors = [row.tolist() for row in self._get_model().embed(texts, batch_size=16, parallel=None)]
        if any(len(row) != self.space.dimensions or not all(math.isfinite(x) for x in row)
               or abs(sum(x*x for x in row)-1) > 0.02 for row in vectors):
            raise EmbeddingUnavailable('本地模型返回无效或未归一化向量')
        return vectors

    def query(self, text: str) -> list[float]:
        parts = self.split_text(self.space.query_prefix + text)
        if not parts:
            raise ValueError('检索词不能为空')
        # Average all query chunks; long queries never silently lose their tail.
        vectors = self.embed(parts)
        vector = [sum(values)/len(vectors) for values in zip(*vectors)]
        norm = math.sqrt(sum(x*x for x in vector))
        return [x/norm for x in vector]

    def status(self):
        try:
            self._verify()
            return {'available': True, 'model_id': MODEL_ID, 'space_id': self.space.id,
                    'runtime_loaded': self._model is not None, 'delivery': 'local-offline'}
        except EmbeddingUnavailable as exc:
            return {'available': False, 'model_id': MODEL_ID, 'space_id': self.space.id,
                    'state': 'keyword_only', 'reason': str(exc)}
