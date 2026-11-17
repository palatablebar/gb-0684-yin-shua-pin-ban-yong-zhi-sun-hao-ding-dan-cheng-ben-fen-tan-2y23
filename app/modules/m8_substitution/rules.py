from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP, ROUND_CEILING

from app.errors import DomainError
from app.modules.m3_versions.rules import layout
from app.modules.m4_segments.rules import losses
from app.modules.m5_calculate.rules import apportion

# 换纸裁决策略：换纸后存在多个可行做法时的选择口径。
# full   整单换纸（默认）：开机损耗只在换纸批发生一次，优先保住原纸批余量；
# split  先用原纸批全部余量再由换纸批补足：两段各自承担一次开机损耗，
#        与整单换纸得到不同的每张排数、库存与订单成本。
POLICIES = ('full', 'split')
CENT = Decimal('0.01')


def _D(value):
    return Decimal(str(value))


def build_catalog(state):
    """从已采信工单聚合纸批台账（裁决前不扣减、不预占）。"""
    groups = {}
    for r in state['rows']:
        groups.setdefault(r['lot'], []).append(r)
    catalog, conflicts = {}, []
    for lot, rows in groups.items():
        # 同纸批库存/克重/纸价/尺寸必须一致；Decimal 数值等值即视为同一材质。
        sig = {(_D(r['paper_w']), _D(r['paper_h']), _D(r['gsm']),
                _D(r['price_kg']), _D(r['stock']), r['grain'], r['dimension_unit']) for r in rows}
        if len(sig) != 1:
            conflicts.append(lot)
            continue
        first = rows[0]
        factor = {'mm': Decimal(1), 'cm': Decimal(10)}.get(first['dimension_unit'])
        if factor is None:
            raise DomainError('UNIT', '尺寸单位无效')
        catalog[lot] = {
            'lot': lot,
            'paper_w': _D(first['paper_w']) * factor,
            'paper_h': _D(first['paper_h']) * factor,
            'gsm': _D(first['gsm']),
            'price_kg': _D(first['price_kg']),
            'stock': int(_D(first['stock'])),
            'grain': first['grain'],
        }
    return catalog, conflicts


def _plan(order, lot_id, lot):
    """单一纸批承担整单时的拼版、损耗、重量与精确成本（金额未舍入）。"""
    choice = layout(lot['paper_w'], lot['paper_h'], order['piece_w'], order['piece_h'],
                    order['bleed'], order['gap'], grain=lot['grain'])
    detail = losses(order['quantity'], choice['copies'], order['fixed_loss'], order['loss_pct'])
    area = lot['paper_w'] * lot['paper_h'] / Decimal(1000000)
    weight = Decimal(detail['gross']) * area * lot['gsm'] / Decimal(1000)
    return {
        'lot': lot_id, 'kind': None,
        'copies': choice['copies'], 'rotated': choice['rotated'],
        'columns': choice['columns'], 'rows': choice['rows'],
        'net': detail['net'], 'fixed_loss': detail['fixed'],
        'run_loss': detail['variable'], 'gross': detail['gross'],
        'capacity': detail['capacity'], 'overprint': detail['overprint'],
        'weight_kg': weight, 'exact_cost': weight * lot['price_kg'],
        'gsm': lot['gsm'], 'price_kg': lot['price_kg'],
        'paper_w': lot['paper_w'], 'paper_h': lot['paper_h'], 'grain': lot['grain'],
    }


def _candidates(order, source_lot, catalog):
    """枚举原纸做法与每个可换纸批做法；尺寸/纹向不兼容的纸批不具备换纸资格。"""
    out = []
    for lot_id, lot in catalog.items():
        try:
            plan = _plan(order, lot_id, lot)
        except DomainError:
            continue
        plan['kind'] = 'ORIGINAL' if lot_id == source_lot else 'SUBSTITUTE'
        out.append(plan)
    return out


def _gross_for_net(net, copies, fixed, pct):
    variable = int((Decimal(net) * _D(pct) / 100).to_integral_value(rounding=ROUND_CEILING))
    return net + int(fixed) + variable, variable


def _max_net_within_sheets(copies, fixed, pct, sheets):
    """给定总张数上限，二分求可容纳的最大净张数（开机损耗与印量损耗分开计）。"""
    lo, hi = 0, max(0, sheets - int(fixed))
    while lo < hi:
        mid = (lo + hi + 1) // 2
        gross, _ = _gross_for_net(mid, copies, fixed, pct)
        if gross <= sheets:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _split_plan(order, original, sub, remaining):
    """先用原纸批全部余量、再由换纸批补足整单；两段各自计一次开机损耗。"""
    src_sheets = remaining[original['lot']]
    src_net = _max_net_within_sheets(original['copies'], order['fixed_loss'], order['loss_pct'], src_sheets)
    if src_net <= 0:
        return None
    made = src_net * original['copies']
    rest = order['quantity'] - made
    if rest <= 0:
        return None
    sub_net = losses(rest, sub['copies'], order['fixed_loss'], order['loss_pct'])['net']
    sub_gross, _ = _gross_for_net(sub_net, sub['copies'], order['fixed_loss'], order['loss_pct'])
    if sub_gross > remaining[sub['lot']]:
        return None
    seg_src = _segment(original, order, src_net, made, original['copies'])
    seg_sub = _segment(sub, order, sub_net, rest, sub['copies'])
    return {
        'kind': 'SPLIT', 'lot': original['lot'] + '+' + sub['lot'], 'replaced_lot': original['lot'],
        'segments': [seg_src, seg_sub],
        'gross': seg_src['gross'] + seg_sub['gross'],
        'weight_kg': seg_src['weight_kg'] + seg_sub['weight_kg'],
        'exact_cost': seg_src['exact_cost'] + seg_sub['exact_cost'],
    }


def _segment(plan, order, net, quantity, copies):
    gross, variable = _gross_for_net(net, copies, order['fixed_loss'], order['loss_pct'])
    area = plan['paper_w'] * plan['paper_h'] / Decimal(1000000)
    weight = Decimal(gross) * area * plan['gsm'] / Decimal(1000)
    return {
        'lot': plan['lot'], 'kind': plan['kind'], 'copies': copies,
        'rotated': plan['rotated'], 'net': net, 'fixed_loss': int(order['fixed_loss']),
        'run_loss': variable, 'gross': gross, 'quantity': quantity,
        'weight_kg': weight, 'exact_cost': weight * plan['price_kg'],
        'gsm': plan['gsm'], 'price_kg': plan['price_kg'],
    }


def _select_single(plan, replaced_lot=None):
    return {'kind': plan['kind'], 'lot': plan['lot'], 'replaced_lot': replaced_lot,
            'copies': plan['copies'], 'rotated': plan['rotated'],
            'columns': plan['columns'], 'rows': plan['rows'],
            'net': plan['net'], 'fixed_loss': plan['fixed_loss'], 'run_loss': plan['run_loss'],
            'gross': plan['gross'], 'capacity': plan['capacity'], 'overprint': plan['overprint'],
            'weight_kg': plan['weight_kg'], 'exact_cost': plan['exact_cost'],
            'gsm': plan['gsm'], 'price_kg': plan['price_kg'], 'segments': None}


def adjudicate(state, policy='full'):
    """近交期订单换纸裁决：候选做法、本批选择与纸批后续可用量。全程为预演，未选纸批不预占。"""
    if policy not in POLICIES:
        raise DomainError('POLICY', '换纸策略无效，可选 full 或 split')
    state = deepcopy(state)
    catalog, conflicts = build_catalog(state)
    remaining = {lot: meta['stock'] for lot, meta in catalog.items()}

    orders = []
    for item in state['layouts']:
        r = item['source']
        orders.append({
            'order_id': item['id'], 'line': item['line'], 'source_lot': r['lot'], 'due': r['due'],
            'quantity': int(_D(r['quantity'])),
            'piece_w': _D(item['dims']['piece_w']), 'piece_h': _D(item['dims']['piece_h']),
            'bleed': _D(item['dims']['bleed']), 'gap': _D(item['dims']['gap']),
            'fixed_loss': int(_D(r['fixed_loss'])), 'loss_pct': _D(r['loss_pct']),
        })
    orders.sort(key=lambda o: (o['due'], o['order_id']))

    decisions = []
    for order in orders:
        src = order['source_lot']
        if src not in catalog:
            decisions.append({'order_id': order['order_id'], 'line': order['line'], 'due': order['due'],
                              'source_lot': src, 'status': 'BLOCKED', 'reason': 'CONFLICT',
                              'candidates': [], 'selected': None, 'exact_fit': False,
                              'note': '原纸批材质定义冲突，无法裁决'})
            continue
        candidates = _candidates(order, src, catalog)
        for c in candidates:
            c['available'] = remaining[c['lot']]
            c['feasible'] = c['gross'] <= remaining[c['lot']]
        original = next((c for c in candidates if c['kind'] == 'ORIGINAL'), None)
        subs = [c for c in candidates if c['kind'] == 'SUBSTITUTE' and c['feasible']]
        decision = {'order_id': order['order_id'], 'line': order['line'], 'due': order['due'],
                    'source_lot': src, 'candidates': candidates, 'selected': None,
                    'status': None, 'reason': '', 'exact_fit': False}

        if original and original['feasible']:
            decision.update(status='RELEASED', reason='ORIGINAL',
                            selected=_select_single(original))
            remaining[src] -= original['gross']
        elif subs:
            chosen_sub = min(subs, key=lambda c: (c['exact_cost'], c['gross'], c['lot']))
            split = _split_plan(order, original, chosen_sub, remaining) if original and remaining[src] > 0 else None
            # 刚好够产：原纸不足以整单放行，而某可换纸批的剩余量恰好等于整单需求张数。
            decision['exact_fit'] = bool(original and not original['feasible'] and
                                         any(c['feasible'] and c['gross'] == remaining[c['lot']] for c in subs))
            if policy == 'split' and split is not None:
                decision.update(status='SWAPPED', reason='SPLIT', selected=split)
                remaining[src] = 0
                remaining[chosen_sub['lot']] -= split['segments'][1]['gross']
            else:
                decision.update(status='SWAPPED', reason='FULL',
                                selected=_select_single(chosen_sub, replaced_lot=src))
                remaining[chosen_sub['lot']] -= chosen_sub['gross']
        else:
            decision.update(status='SHORTAGE', reason='NO_SUBSTITUTE',
                            note='原纸不足且无尺寸/纹向兼容、库存充足的可换纸批')
        decisions.append(decision)

    projection = _projection(catalog, remaining, decisions)
    state['substitution'] = {
        'policy': policy, 'lot_conflicts': conflicts,
        'decisions': decisions, 'projection': projection,
        'business': _business(decisions, projection),
    }
    return state


def _projection(catalog, remaining, decisions):
    """纸批后续可用量：只汇总被选做法的扣减，未选中的候选纸批保持期初余量。"""
    consumed = {lot: 0 for lot in catalog}
    for d in decisions:
        sel = d.get('selected')
        if not sel:
            continue
        if sel.get('segments'):
            for seg in sel['segments']:
                consumed[seg['lot']] += seg['gross']
        else:
            consumed[sel['lot']] += sel['gross']
    return [{'lot': lot, 'opening': catalog[lot]['stock'], 'consumed': consumed[lot],
             'closing': remaining[lot], 'gsm': str(catalog[lot]['gsm']),
             'price_kg': str(catalog[lot]['price_kg']),
             'paper_w': str(catalog[lot]['paper_w']), 'paper_h': str(catalog[lot]['paper_h'])}
            for lot in sorted(catalog)]


def _business(decisions, projection):
    released = [d for d in decisions if d.get('selected')]
    costs = apportion([d['selected']['exact_cost'] for d in released]) if released else []
    total = sum(costs, Decimal(0)).quantize(CENT, rounding=ROUND_HALF_UP)
    for d, cost in zip(released, costs):
        d['selected']['cost'] = str(cost)
    return {
        'released_orders': len(released),
        'shortage_orders': sum(d['status'] == 'SHORTAGE' for d in decisions),
        'swapped_orders': sum(d['status'] == 'SWAPPED' for d in decisions),
        'sheets': sum(p['consumed'] for p in projection),
        'leftover': sum(p['closing'] for p in projection),
        'total_cost': str(total), 'unit': 'CNY',
    }
