"""Synthetic foundation scenario, always disposable, no external operations."""
import json,tempfile
from pathlib import Path
from datetime import timedelta
from .store import Store,now
from .office import Office
from .seminars import JST
from .integrations.adapters import Principal


def demo():
    with tempfile.TemporaryDirectory() as directory:
        store=Store(Path(directory)/'synthetic.db'); office=Office(store); office.add_user('synthetic_owner','representative','SyntheticDemoPass!123'); p=Principal('representative','synthetic_owner'); office.portfolio.seed(p); f=office.business_foundation
        a=f.create(p,'synthetic-a','合成の追加事業A','合成の概要・実提供ではない','自由カテゴリA')
        b=f.create(p,'synthetic-b','合成の追加事業B','合成の概要・実提供ではない','自由カテゴリB')
        for business,agent in ((a,'AI17'),(b,'AI17'),(a,'AI28')): f.assign(p,business,agent,'既存の役職を維持','合成の事業内確認',['business_read','work'])
        task=office.portfolio.task(p,a,'synthetic-task','referral','合成の確認項目',agent='AI17'); office.portfolio.run(p,a,task)
        try: f.read_for_ai(p,b,'AI27')
        except PermissionError: cross_business_denied=True
        else: raise AssertionError('Unassigned employee must not access this business')
        day=now().astimezone(JST).date().isoformat(); end=(now().astimezone(JST).date()+timedelta(days=1)).isoformat()
        f.kpi(p,a,'sample_questions','合成の質問件数','件'); f.measure(p,a,'sample_questions',day,'5','mock','合成データ・実績ではない')
        row=f.get(p,b); f.update(p,b,row['overview'],row['category'],None,False,'preparing',False,row['revision'],row['business_revision'])
        result={'mode':'synthetic_temporary_database_not_real_business_results','registered_agents':len(store.agents),'old_businesses_preserved':store.db.execute("SELECT COUNT(*) FROM portfolio_businesses WHERE id IN ('BIZ-A','BIZ-B','BIZ-C','BIZ-D','BIZ-E','BIZ-F')").fetchone()[0],
            'new_businesses':[a,b],'one_employee_multiple_businesses':store.db.execute("SELECT COUNT(*) FROM bf_ai_assignments WHERE agent='AI17'").fetchone()[0],
            'unassigned_access_denied':cross_business_denied,'disabled_business_preserved':not bool(f.get(p,b)['enabled']),
            'own_ai_metadata':f.read_for_ai(p,a,'AI17'),'summary':f.summary(p,a,day,end),
            'actual_external_services':[],'external_send_attempts':store.db.execute('SELECT COUNT(*) FROM ix_attempts').fetchone()[0],
            'real_invoice_records':store.db.execute('SELECT COUNT(*) FROM scm_invoices').fetchone()[0],'paid_api_calls':0,'production_started':False,'foreign_key_violations':len(store.db.execute('PRAGMA foreign_key_check').fetchall())}
        assert result['registered_agents']==28 and result['old_businesses_preserved']==6 and result['one_employee_multiple_businesses']==2
        assert result['external_send_attempts']==result['real_invoice_records']==result['foreign_key_violations']==0
        store.close(); return result


if __name__=='__main__': print(json.dumps(demo(),ensure_ascii=False,indent=2))
