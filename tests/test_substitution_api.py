from app.config import MODULES


def test_m8_page_and_api(client):
    assert client.get('/m8_substitution').status_code == 200
    r = client.post('/api/runs', json={'sample': 'shortage.csv'})
    assert r.status_code == 200
    run_id = r.get_json()['run_id']
    page = client.get('/m8_substitution?sample=shortage.csv')
    assert page.status_code == 200 and '换纸裁决' in page.data.decode()
    api = client.get('/api/m8_substitution?sample=shortage.csv')
    assert api.status_code == 200 and api.get_json()['substitution']['reconcile']['balanced'] is True


def test_seal_flow_via_api(client):
    run_id = client.post('/api/runs', json={'sample': 'shortage.csv'}).get_json()['run_id']
    ok = client.post(f'/api/runs/{run_id}/seal', json={'version': 1})
    assert ok.status_code == 200 and ok.get_json()['sealed'] is True
    # 重跑同一排产决定：封存幂等，仍只有一条指纹，不新增用纸与订单成本。
    again = client.post(f'/api/runs/{run_id}/seal', json={'version': 1})
    assert len(again.get_json()['seals']) == 1
    # 陈旧版本被拒（409），旧版保留。
    stale = client.post(f'/api/runs/{run_id}/seal', json={'version': 99})
    assert stale.status_code == 409
    ledger = client.get('/api/m8_substitution/seals')
    assert ledger.status_code == 200 and any(r['lot'] == 'L2' for r in ledger.get_json()['sealed_lots'])


def test_seal_requires_version(client):
    run_id = client.post('/api/runs', json={'sample': 'shortage.csv'}).get_json()['run_id']
    r = client.post(f'/api/runs/{run_id}/seal', json={})
    assert r.status_code == 422 and r.get_json()['code'] == 'VERSION'


def test_split_policy_parameter_changes_decision(client):
    full = client.post('/api/runs', json={'sample': 'shortage.csv',
                                          'parameters': {'substitution_policy': 'full'}}).get_json()
    split = client.post('/api/runs', json={'sample': 'shortage.csv',
                                           'parameters': {'substitution_policy': 'split'}}).get_json()
    c_full = next(d for d in full['substitution']['decisions'] if d['order_id'] == 'PO-C303')
    c_split = next(d for d in split['substitution']['decisions'] if d['order_id'] == 'PO-C303')
    assert c_full['reason'] == 'FULL' and c_split['reason'] == 'SPLIT'
    assert full['substitution']['business']['total_cost'] != split['substitution']['business']['total_cost']


def test_shortage_sample_whitelisted(client):
    r = client.post('/api/runs', json={'sample': 'shortage.csv'})
    assert r.status_code == 200
    bad = client.post('/api/runs', json={'sample': '../etc/passwd'})
    assert bad.status_code == 422 and bad.get_json()['code'] == 'SAMPLE'


def test_module_count_extended():
    assert MODULES[-1][0] == 'm8_substitution' and len(MODULES) == 8


def test_export_contains_substitution_evidence(client):
    state = client.post('/api/runs', json={'sample': 'shortage.csv'}).get_json()
    csv_text = client.get(f"/api/archive/{state['run_id']}/export.csv").data.decode('utf-8-sig')
    assert '换纸决定' in csv_text and '纸批余量' in csv_text
