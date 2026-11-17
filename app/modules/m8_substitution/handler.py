from flask import Blueprint, jsonify, request

from app.errors import DomainError
from app.web import api, page
from app import store

bp = Blueprint('m8_substitution', __name__)


@bp.get('/m8_substitution')
def screen():
    return page(8, 'm8_substitution.html')


@bp.route('/api/m8_substitution', methods=['GET', 'POST'])
def preview():
    return api(8)


@bp.get('/api/m8_substitution/seals')
def seals_overview():
    return jsonify({'sealed_lots': store.sealed_lots(), 'runs': store.history()})


@bp.post('/api/runs/<run_id>/seal')
def seal_run(run_id):
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get('version'), int):
        raise DomainError('VERSION', '请提交复核版本')
    state = store.get(run_id)
    # 以当前保存的批次状态重算到 m8，确保封存依据与页面所见一致且为最新规则结果。
    from app.workflow import run
    import base64
    content = base64.b64decode(state['source_b64'])
    params = state.get('manifest', {}).get('parameters', {})
    fresh = run(content, params, 8)
    return jsonify(store.seal(run_id, data['version'], fresh))
