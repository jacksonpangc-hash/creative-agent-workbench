"""Derive a reservation from an existing authorization; never report it as spend."""
from decimal import Decimal, ROUND_UP
import json

from .contracts import CostQuote, decimal


def plan_reservation(store, project_id, binding, payload, requests):
    budget = store.budget(project_id)
    available = decimal(budget['available']) if budget['available'] is not None else None
    currency = binding.get('currency', 'CNY')
    if available is not None and available <= 0:
        raise ValueError('可用额度不足；如有费用待确认，请先在用量与费用中核对。')
    if available is not None and currency != budget['currency']:
        raise ValueError('模型计价币种与项目费用上限不同，请在设置中核对。')
    incoming = payload.get('quote')
    if incoming not in (None, ''):
        amount = decimal(incoming)
        if amount <= 0:
            raise ValueError('费用占用金额需要大于零')
        return CostQuote(amount=amount, currency=currency, basis='沿用已确认任务金额；实际费用以服务商账单为准')
    inp, out = binding.get('price_input_million'), binding.get('price_output_million')
    native = (payload.get('video_route') or {}).get('route') == 'native_video'
    if inp is not None and out is not None and not native:
        # Planning estimate only: actual tokenization, caching and charges vary.
        # Use the frozen payload, actual text and the declared sampled-frame cap.
        text_bytes = len(json.dumps(payload, ensure_ascii=False, default=str).encode('utf-8'))
        ids = (payload.get('scope') or {}).get('asset_ids', payload.get('asset_ids', []))
        transcript_bytes = 0
        maximum_image_allowance = 0
        for aid in ids:
            asset = store.get(project_id, 'asset', aid)
            transcript_bytes += len(asset.get('metadata', {}).get('transcript', '').encode('utf-8'))
            maximum_image_allowance = max(maximum_image_allowance,36*4096 if asset.get('media_type') == 'video' else 4096)
        count=max(1,requests)
        rounds=max(1,int((payload.get('task_spec') or {}).get('max_generation_rounds',1)))
        kind=payload.get('kind','analysis')
        observations=(max(0,count-rounds) if kind=='analysis' else count if kind=='parse' else 0)
        # Frames are sent to observation requests, not to every strategy round.
        # Repeating the full media set per request made batch estimates quadratic.
        input_allowance=(text_bytes+16384)*count + transcript_bytes*(rounds+1) + maximum_image_allowance*observations
        amount = ((decimal(inp)*input_allowance + decimal(out)*int(binding.get('max_output_tokens',4096))*count)
                  * Decimal('1.25') / 1000000)
        amount = max(Decimal('0.000001'), amount.quantize(Decimal('0.000001'), rounding=ROUND_UP))
        if available is not None and amount > available:
            raise ValueError('本次处理超出当前费用上限，请减少素材或在设置中调整。')
        return CostQuote(amount=amount, currency=currency, basis='按已保存单价、材料和请求次数估算并留出余量；不是实际消费，实际以服务商账单为准')
    limit = binding.get('unpriced_task_limit')
    if limit is None:
        return CostQuote(amount=None, currency=currency, basis='费用未知；按本次任务的调用次数执行，实际费用以服务商账单为准')
    amount = decimal(limit) if available is None else min(decimal(limit), available)
    if amount <= 0: raise ValueError('单任务费用上限需要大于零')
    return CostQuote(amount=amount, currency=currency, basis='沿用该服务已确认的单任务限制；不是预计扣费，实际以服务商账单为准')
