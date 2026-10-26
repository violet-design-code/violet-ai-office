"""Synthetic sales workflow in a disposable database, never external execution."""
import json
import tempfile
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
from .store import Store,now
from .office import Office
from .seminars import JST
from .integrations.adapters import Principal


def demo():
    with tempfile.TemporaryDirectory() as directory:
        store=Store(Path(directory)/'synthetic.db'); office=Office(store)
        office.add_user('synthetic_owner','representative','SyntheticDemoPass!123')
        owner=Principal('representative','synthetic_owner'); office.portfolio.seed(owner)
        s=office.sales; business='BIZ-B'; today=now().astimezone(JST).date()
        contact=s.customer(owner,business,'合成シナリオの顧客','synthetic-reference')
        for purpose in ('contact','schedule','followup'):
            office.portfolio.consent(owner,business,contact,purpose,True,'合成の本人同意。実際の同意ではない。')
        deal=s.deal(owner,business,'synthetic-deal',contact,'合成の清掃相談','合成の検証問い合わせ','mock')
        lines=[{'label':'合成サービス','quantity':'1','unit_yen':'6000'}]
        expiry=(today+timedelta(days=7)).isoformat()
        quote=s.quote(owner,business,deal,lines,'合成の条件',expiry)
        try:
            s.decide_quote(owner,business,quote,'approve',True)
        except ValueError:
            unknown_tax_blocked=True
        else:
            raise AssertionError('Unknown tax must block approval')
        # Explicit test assumption, not the representative's actual tax settings.
        s.tax_policy(owner,business,'excluded','10','floor',True)
        quote=s.quote(owner,business,deal,lines,'合成の税設定。実際の料金ではない。',expiry,quote)
        s.decide_quote(owner,business,quote,'approve',True)
        for stage in ('prospect','proposal','quote'):
            s.transition(owner,business,deal,stage,revision=s.row(owner,business,'scm_deals',deal)['revision'])
        start=now()+timedelta(days=2)
        booking=s.booking(owner,business,deal,'service',start.isoformat(),(start+timedelta(hours=1)).isoformat())
        s.transition(owner,business,deal,'candidate',revision=s.row(owner,business,'scm_deals',deal)['revision'])
        def mock(kind,body=None):
            action=s.action(owner,business,deal,uuid4().hex,kind,quote,booking if kind=='booking_confirm' else None,body)
            s.decide_action(owner,business,action,'approve',True)
            assert s.execute_mock(owner,business,action)=='mock_succeeded'
        for kind in ('contract','booking_confirm','provided','invoice'): mock(kind)
        invoice=store.db.execute('SELECT * FROM scm_invoices').fetchone()
        mock('payment',{'invoice_id':invoice['id'],'amount_yen':6600})
        assignments=[]
        for activity in ('classify','reply','analysis','quality'):
            task=s.work(owner,business,deal,activity,activity); s.run_work(owner,business,task)
            assignments.append({'activity':activity,'agent':store.db.execute('SELECT agent FROM tasks WHERE id=?',(task,)).fetchone()[0]})
        stats=s.dashboard(owner,business,today.isoformat(),(today+timedelta(days=1)).isoformat())
        result={'mode':'synthetic_temporary_database_not_real_business_results',
            'unknown_tax_blocks_approval':unknown_tax_blocked,'synthetic_tax_assumption':'excluded / 10% / floor',
            'mock_invoice':{'net_yen':invoice['net_yen'],'tax_yen':invoice['tax_yen'],'gross_yen':invoice['gross_yen']},
            'mock_stage':s.row(owner,business,'scm_deals',deal)['stage'],'actual_dashboard':stats,
            'assignments':assignments,'registered_agents':len(store.agents),
            'actual_invoice_count':store.db.execute("SELECT COUNT(*) FROM scm_invoices WHERE mode='manual'").fetchone()[0],
            'old_ledger_rows':store.db.execute('SELECT COUNT(*) FROM portfolio_ledger').fetchone()[0],
            'external_send_attempts':store.db.execute('SELECT COUNT(*) FROM ix_attempts').fetchone()[0],
            'foreign_key_violations':len(store.db.execute('PRAGMA foreign_key_check').fetchall()),
            'actual_external_services':[],'paid_api_calls':0,'production_started':False}
        assert result['actual_invoice_count']==result['old_ledger_rows']==result['external_send_attempts']==result['foreign_key_violations']==0
        assert stats['confirmed_net_yen'] is None and result['registered_agents']==28
        store.close(); return result


if __name__=='__main__': print(json.dumps(demo(),ensure_ascii=False,indent=2))
