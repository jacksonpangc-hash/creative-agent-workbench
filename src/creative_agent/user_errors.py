"""Stable user-facing errors; internal exceptions never authorize automatic retries."""
from .store import Conflict,NotFound,uid


def describe(error):
 text=str(error)
 code='not_found' if isinstance(error,NotFound) else 'version_conflict' if isinstance(error,Conflict) else 'needs_attention'
 action='重新打开当前资料后再试' if isinstance(error,Conflict) else '核对当前输入和资料'
 if '余额不足' in text:code='provider_balance';action='到模型服务商账户核对余额，已有资料和成果仍可使用'
 elif any(w in text for w in ('费用','额度','预算','积分','单任务')):code='usage_limit';action='查看用量与费用或模型设置'
 elif any(w in text for w in ('台词','字幕','转写')):code='transcript_needs_review';action='打开素材台词页核对或补充字幕'
 elif any(w in text for w in ('授权','许可','权限')):code='permission_required';action='核对账号和该资料的使用许可'
 return {'code':code,'user_message':text,'detail':text,'action':action,'retryable':False,'diagnostic_id':uid()}
