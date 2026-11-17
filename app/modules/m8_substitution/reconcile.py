from decimal import Decimal

from app.errors import DomainError
CENT = Decimal('0.01')


def reconcile(state):
    """整批对账：行数守恒、纸批库存守恒、订单成本与用纸精确成本一致、未选纸批未被预占。"""
    sub = state['substitution']
    decisions = sub['decisions']
    projection = sub['projection']

    accepted = len(state['rows'])
    isolated = len(state['quarantine'])
    accounted = accepted + isolated + state['blank_count'] + state['header_count'] + state['duplicate_count']
    count_balanced = accounted == state['input_count']

    # 库存守恒：每个纸批 期初 = 耗用 + 期末，且期末不为负。
    stock_balanced = all(p['opening'] == p['consumed'] + p['closing'] and p['closing'] >= 0
                         for p in projection)

    # 成本守恒：被选订单分币成本之和 == 业务总成本；且等于各纸批精确成本按最大余额法分摊后的合计。
    order_costs = [Decimal(d['selected']['cost']) for d in decisions if d.get('selected')]
    cost_sum = sum(order_costs, Decimal(0)).quantize(CENT)
    exact_sum = sum((Decimal(d['selected']['exact_cost']) for d in decisions if d.get('selected')),
                    Decimal(0)).quantize(CENT)
    target = Decimal(sub['business']['total_cost'])
    cost_balanced = cost_sum == target == exact_sum

    # 未选纸批不被预占：候选中未被选中的纸批，其耗用量必须为 0（在 projection 上表现为只减选中段）。
    selected_lots = set()
    for d in decisions:
        sel = d.get('selected')
        if not sel:
            continue
        if sel.get('segments'):
            selected_lots.update(seg['lot'] for seg in sel['segments'])
        else:
            selected_lots.add(sel['lot'])
    candidate_lots = {c['lot'] for d in decisions for c in d['candidates']}
    untouched = candidate_lots - selected_lots
    no_preempt = all(p['consumed'] == 0 for p in projection if p['lot'] in untouched)

    # 释放订单的可用量声明必须与做法一致：候选纸批扣减前可用量 >= 该做法总张数。
    feasibility_ok = True
    for d in decisions:
        sel = d.get('selected')
        if not sel:
            continue
        if sel.get('segments'):
            for seg in sel['segments']:
                cand = next(c for c in d['candidates'] if c['lot'] == seg['lot'])
                if cand['available'] < seg['gross']:
                    feasibility_ok = False
        else:
            cand = next(c for c in d['candidates'] if c['lot'] == sel['lot'])
            if cand['available'] < sel['gross']:
                feasibility_ok = False

    balanced = all((count_balanced, stock_balanced, cost_balanced, no_preempt, feasibility_ok))
    state['substitution']['reconcile'] = {
        'balanced': balanced,
        'count_balanced': count_balanced,
        'stock_balanced': stock_balanced,
        'cost_balanced': cost_balanced,
        'no_preempt': no_preempt,
        'feasibility_ok': feasibility_ok,
        'input': state['input_count'], 'accepted': accepted, 'isolated': isolated,
        'blank': state['blank_count'], 'headers': state['header_count'],
        'duplicates': state['duplicate_count'], 'accounted': accounted,
        'order_cost_sum': str(cost_sum), 'exact_cost_sum': str(exact_sum),
        'untouched_lots': sorted(untouched),
    }
    return state


def assert_sealable(state):
    """封存前置条件：整批对账通过，否则拒绝封存并保留旧版。"""
    result = state.get('substitution', {}).get('reconcile')
    if not result:
        raise DomainError('RECONCILE', '尚未完成换纸对账')
    if not result['balanced']:
        raise DomainError('UNBALANCED', '整批对账未通过，已保留旧版，未封存')
    return result
