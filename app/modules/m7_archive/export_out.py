import io,csv,json,hashlib

def bundle(state):
    out=io.StringIO(newline='');writer=csv.writer(out)
    writer.writerow(['记录类型','批次','来源行','业务ID','原因码','内容'])
    rid=state.get('run_id','preview')
    writer.writerow(['运行清单',rid,'','','',json.dumps(state.get('manifest',{}),ensure_ascii=False,sort_keys=True)])
    writer.writerow(['对账',rid,'','','',json.dumps(state.get('reconcile',{}),ensure_ascii=False,sort_keys=True)])
    for row in state.get('calculations',[]):writer.writerow(['计算',rid,row.get('line',''),row.get('id',''),row.get('code',''),json.dumps(row,ensure_ascii=False,sort_keys=True)])
    for row in state['quarantine']:writer.writerow(['隔离',rid,row['line'],row.get('id',''),row['code'],json.dumps(row.get('raw',{}),ensure_ascii=False,sort_keys=True)])
    sub=state.get('substitution')
    if sub:
        writer.writerow(['换纸对账',rid,'','','',json.dumps(sub.get('reconcile',{}),ensure_ascii=False,sort_keys=True)])
        for d in sub.get('decisions',[]):
            writer.writerow(['换纸决定',rid,d.get('line',''),d.get('order_id',''),d.get('reason',''),json.dumps({'status':d.get('status'),'exact_fit':d.get('exact_fit'),'selected':d.get('selected'),'candidates':[{'lot':c['lot'],'kind':c['kind'],'gross':c['gross'],'available':c['available'],'feasible':c['feasible']} for c in d.get('candidates',[])]},ensure_ascii=False,sort_keys=True)])
        for p in sub.get('projection',[]):
            writer.writerow(['纸批余量',rid,'',p.get('lot',''),'',json.dumps(p,ensure_ascii=False,sort_keys=True)])
    return '\ufeff'+out.getvalue()

def checksum(state):
    return hashlib.sha256(bundle(state).encode('utf-8')).hexdigest()
