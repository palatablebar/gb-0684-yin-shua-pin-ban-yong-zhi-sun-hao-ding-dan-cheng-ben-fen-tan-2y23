from app.modules.m7_archive.export_out import checksum

def execute(state):
    sub=state.get('substitution') or {}
    extra=len(sub.get('decisions',[]))+len(sub.get('projection',[]))+(1 if sub.get('reconcile') else 0)
    state['archive']={'sha256':checksum(state),'evidence_count':len(state.get('calculations',[]))+len(state['quarantine'])+extra,
        'ready':state.get('reconcile',{}).get('balanced',False)}
    return state
