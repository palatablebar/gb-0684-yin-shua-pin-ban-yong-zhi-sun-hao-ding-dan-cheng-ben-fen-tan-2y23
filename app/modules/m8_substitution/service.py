from app.modules.m8_substitution.rules import adjudicate, POLICIES
from app.modules.m8_substitution.reconcile import reconcile


def execute(state):
    # 换纸策略可由运行参数指定，默认整单换纸（full）。
    policy = (state.get('parameters') or {}).get('substitution_policy', 'full')
    if policy not in POLICIES:
        policy = 'full'
    state = adjudicate(state, policy)
    return reconcile(state)
