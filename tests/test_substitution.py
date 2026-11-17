from decimal import Decimal

import pytest

from app import store
from app.errors import DomainError
from app.workflow import load, run
from app.modules.m8_substitution.rules import adjudicate, _max_net_within_sheets
from app.modules.m8_substitution.reconcile import reconcile, assert_sealable


def _shortage(policy='full'):
    return run(load('shortage.csv'), {'substitution_policy': policy}, 8)


def test_all_orders_adjudicated():
    s = _shortage()
    ids = [d['order_id'] for d in s['substitution']['decisions']]
    assert ids == ['PO-A101', 'PO-B202', 'PO-C303', 'PO-D404']  # 按 due、order_id 排序


def test_original_paper_held_when_sufficient():
    s = _shortage()
    b = next(d for d in s['substitution']['decisions'] if d['order_id'] == 'PO-B202')
    assert b['status'] == 'RELEASED' and b['reason'] == 'ORIGINAL' and b['selected']['lot'] == 'L2'


def test_shortage_triggers_full_substitution():
    s = _shortage()
    a = next(d for d in s['substitution']['decisions'] if d['order_id'] == 'PO-A101')
    assert a['status'] == 'SWAPPED' and a['reason'] == 'FULL'
    assert a['selected']['lot'] == 'L2' and a['selected']['replaced_lot'] == 'L1'
    assert a['selected']['gross'] == 122 and a['selected']['copies'] == 10  # 换纸后排数变化可见


def test_exact_fit_flagged_and_two_policies_differ():
    full = _shortage('full')
    split = _shortage('split')
    c_full = next(d for d in full['substitution']['decisions'] if d['order_id'] == 'PO-C303')
    c_split = next(d for d in split['substitution']['decisions'] if d['order_id'] == 'PO-C303')
    # 换纸批 L2 在轮到 C 单时剩余恰好 122 张，等于整单需求。
    assert c_full['exact_fit'] is True
    assert c_full['selected']['lot'] == 'L2' and c_full['selected']['gross'] == 122
    # 分段补足：原纸 L3 122 张全用尽，再由 L2 补 31 张，两段各一次开机损耗。
    assert c_split['reason'] == 'SPLIT'
    seg_lots = [x['lot'] for x in c_split['selected']['segments']]
    assert seg_lots == ['L3', 'L2']
    assert c_split['selected']['gross'] == 153 and c_split['selected']['gross'] != c_full['selected']['gross']
    # 两种做法的库存与成本均不同。
    assert full['substitution']['business'] != split['substitution']['business']


def test_no_substitute_order_blocks_without_occupying():
    s = _shortage()
    d = next(x for x in s['substitution']['decisions'] if x['order_id'] == 'PO-D404')
    assert d['status'] == 'SHORTAGE' and d['selected'] is None
    lx = next(p for p in s['substitution']['projection'] if p['lot'] == 'LX')
    assert lx['consumed'] == 0 and lx['closing'] == lx['opening'] == 10


def test_unselected_lots_are_not_preempted():
    s = _shortage()
    # L1 是 A 单原纸但未被整单换纸选用，整批不得预占；L3、LX 同理。
    for lot in ('L1', 'L3', 'LX'):
        p = next(x for x in s['substitution']['projection'] if x['lot'] == lot)
        assert p['consumed'] == 0 and p['closing'] == p['opening']
    assert s['substitution']['reconcile']['no_preempt'] is True


def test_stock_and_cost_conservation():
    s = _shortage()
    rec = s['substitution']['reconcile']
    assert rec['balanced'] is True and rec['stock_balanced'] is True and rec['cost_balanced'] is True
    for p in s['substitution']['projection']:
        assert p['opening'] == p['consumed'] + p['closing']
    order_sum = sum((Decimal(d['selected']['cost']) for d in s['substitution']['decisions']
                     if d['selected']), Decimal(0))
    assert order_sum == Decimal(s['substitution']['business']['total_cost'])


def test_available_quantity_after_batch_visible():
    s = _shortage()
    proj = {p['lot']: p for p in s['substitution']['projection']}
    # L2 期初 366，A、B、C 各 122 张，期末 0；其余纸批保持期初。
    assert proj['L2']['consumed'] == 366 and proj['L2']['closing'] == 0
    assert proj['L1']['closing'] == 60 and proj['L3']['closing'] == 122


def test_split_max_net_bsearch():
    # 60 张上限、开机 20、损耗 2%：最大净张数 39（39+20+1=60），40 需 61 张。
    assert _max_net_within_sheets(10, 20, 2, 60) == 39


def test_policy_parameter_validation():
    s = run(load('shortage.csv'), {}, 4)
    with pytest.raises(DomainError) as e:
        adjudicate(s, policy='bogus')
    assert e.value.code == 'POLICY'


def test_reconcile_detects_broken_stock():
    s = _shortage()
    s['substitution']['projection'][0]['closing'] += 1  # 制造库存不平
    out = reconcile(s)['substitution']['reconcile']
    assert out['balanced'] is False and out['stock_balanced'] is False
    with pytest.raises(DomainError) as e:
        assert_sealable(s)
    assert e.value.code == 'UNBALANCED'


def test_seal_idempotent_and_keeps_old_on_failure(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'm8.db'))
    s = _shortage()
    store.save(s['run_id'], s)
    first = store.seal(s['run_id'], 1, s)
    # 同一排产决定重跑：不新增用纸和订单成本（封存记录仍只有一条指纹）。
    second = store.seal(s['run_id'], 1, s)
    assert first['seals'] == second['seals'] and len(second['seals']) == 1
    # 复核版本陈旧 -> 409，旧版保留。
    with pytest.raises(DomainError) as e:
        store.seal(s['run_id'], 99, s)
    assert e.value.status == 409
    # 对账失败 -> 拒绝封存，不写入新记录。
    broken = _shortage()
    broken['substitution']['reconcile']['balanced'] = False
    with pytest.raises(DomainError):
        store.seal(broken['run_id'], 1, broken)
    assert len(store.get_seal(s['run_id'])['seals']) == 1


def test_different_decisions_create_distinct_seals(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'm8b.db'))
    full = _shortage('full')
    split = _shortage('split')
    # 不同输入参数产生不同 run_id；分别封存后指纹不同。
    store.save(full['run_id'], full)
    store.save(split['run_id'], split)
    a = store.seal(full['run_id'], 1, full)['seals'][0]['decision_fingerprint']
    b = store.seal(split['run_id'], 1, split)['seals'][0]['decision_fingerprint']
    assert a != b


def test_sealed_lots_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'm8c.db'))
    s = _shortage()
    store.save(s['run_id'], s)
    store.seal(s['run_id'], 1, s)
    ledger = {row['lot']: row for row in store.sealed_lots()}
    assert ledger['L2']['consumed'] == 366 and ledger['L2']['closing'] == 0
    assert ledger['L1']['consumed'] == 0


def test_sealed_evidence_in_export():
    s = _shortage()
    # 完整跑到 m7 之后再补 m8，导出应同时含换纸决定与纸批余量行。
    full = run(load('shortage.csv'), {'substitution_policy': 'full'})
    text = full  # run 默认 stage=7，不含 substitution
    bundle_state = run(load('shortage.csv'), {'substitution_policy': 'full'}, 8)
    from app.modules.m7_archive.export_out import bundle
    csv_text = bundle(bundle_state)
    assert '换纸决定' in csv_text and '纸批余量' in csv_text and '换纸对账' in csv_text
    assert full['run_id'] == bundle_state['run_id']  # 同输入同参数同规则 => 同 run_id
