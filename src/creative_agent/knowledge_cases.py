"""Versioned methodology, explicitly separate from product and performance facts."""
import hashlib
import json
from pathlib import Path

DEFAULT_PACKAGE=Path(__file__).with_name('creative_cases')


def load_cases(directory=DEFAULT_PACKAGE, *, include_drafts=False):
    root=Path(directory).resolve()
    if not (root/'manifest.json').is_file():return []
    manifest=json.loads((root/'manifest.json').read_text())
    if not isinstance(manifest,dict):raise ValueError('案例说明格式无效')
    if manifest.get('license') not in {'AGPL-3.0-only','CC0-1.0','CC-BY-4.0'}:
        raise ValueError('内置案例许可不允许商业分发或尚未审核')
    if manifest['license']=='CC-BY-4.0' and not manifest.get('attribution'):
        raise ValueError('案例许可需要完整署名信息')
    if manifest.get('not_performance_evidence') is not True:raise ValueError('案例必须说明不是经营效果证明')
    filename=manifest.get('content_file')
    if filename!='cases.json':raise ValueError('案例文件位置无效')
    path=root/filename
    if not path.resolve().is_relative_to(root) or path.is_symlink():raise ValueError('案例文件超出包目录')
    raw=path.read_bytes()
    if len(raw)>1024*1024 or hashlib.sha256(raw).hexdigest()!=manifest.get('content_sha256'):
        raise ValueError('案例内容校验失败')
    rows=json.loads(raw)
    if not isinstance(rows,list) or len(rows)>1000:raise ValueError('案例内容格式无效')
    result=[];seen=set()
    for row in rows:
        if not isinstance(row,dict) or not row.get('id') or row['id'] in seen:raise ValueError('案例身份无效或重复')
        seen.add(row['id'])
        if row.get('type') not in {'method','example'} or not isinstance(row.get('content'),str) or not row['content'].strip():
            raise ValueError('案例缺少具体内容')
        if row['type']=='example' and row.get('synthetic') is not True:raise ValueError('当前案例包只收录已标注的创作演练')
        if any(k in row for k in ('sales','roi','metrics','product_facts')):raise ValueError('方法案例不能提供商品或经营事实')
        if row.get('review_state')=='approved' and (not row.get('reviewed_by') or not row.get('reviewed_at')):
            raise ValueError('案例缺少业务审核记录')
        if include_drafts or row.get('review_state')=='approved':result.append(row)
    return result


def require_reviewed_cases(directory=DEFAULT_PACKAGE):
    all_rows=load_cases(directory,include_drafts=True)
    reviewed=load_cases(directory)
    if len(reviewed)!=len(all_rows) or sum(r['type']=='method' for r in reviewed)<30 or sum(r['type']=='example' for r in reviewed)<12:
        raise ValueError('内置方法与案例尚未完成业务审核，不能制作商业发行包')
    return {'methods':sum(r['type']=='method' for r in reviewed),'examples':sum(r['type']=='example' for r in reviewed)}


def methodology_context(directory=DEFAULT_PACKAGE, query=''):
    try:
        rows=load_cases(directory)
        manifest_path=Path(directory)/'manifest.json'
        manifest=json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    except (OSError,ValueError):
        return {'methods':[],'status':'unavailable','not_product_facts':True,
                'not_performance_evidence':True,'purpose':'内置案例暂不可用，继续使用当前素材'}
    ranked=sorted(rows,key=lambda row:sum(len(tag) for tag in row.get('tags',[]) if tag in query),reverse=True)
    return {'methods':[{k:row[k] for k in ('id','type','title','content','synthetic') if k in row} for row in ranked[:4]],
            'status':'ready' if rows else 'awaiting_review','package_version':manifest.get('version'),
            'content_sha256':manifest.get('content_sha256'),
            'not_product_facts':True,'not_performance_evidence':True,'purpose':'仅供创作方法参考，不能据此补写商品事实或宣称效果'}
