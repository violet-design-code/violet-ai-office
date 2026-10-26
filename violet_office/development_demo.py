"""Reproducible synthetic assumptions only. Never opens a business DB."""
import json,tempfile
from pathlib import Path
from .store import Store,now
from .seminars import JST
from .office import Office
from .integrations.adapters import Principal

PARAMETERS={'initial_cost_yen':'30000','fixed_cost_yen':'10000','variable_cost_per_sale_yen':'1000','price_yen':'6000','leads_month':'20','conversion_percent':'50','hours_per_sale':'1','hours_month':'20'}


def demo():
    with tempfile.TemporaryDirectory() as directory:
        store=Store(Path(directory)/'synthetic.db'); office=Office(store); office.add_user('synthetic_owner','representative','SyntheticDemoPass!123'); p=Principal('representative','synthetic_owner'); d=office.development
        ids=[]
        for title in ('セルフホワイトニングの合成候補','スマホ教室の合成候補','鏡清掃の合成候補','独立新規事業の合成候補'):
            ids.append(d.create(p,title,{}))
        for name in ('pessimistic','standard','optimistic'):
            # Same explicit fixture for all labels; never invents plus/minus multipliers.
            d.scenario(p,ids[2],name,PARAMETERS,'合成テストの明示的仮定。市場・実収益の根拠として使用不可。')
        proposal=d.proposal(p,ids[2],True)
        before=store.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
        d.decide_proposal(p,proposal,'approve',True)
        plan=d.plan(p,proposal,7,{'start_day':now().astimezone(JST).date().isoformat(),'api_budget_microusd':0,'kpi':'根拠と計測方法の確認（合成）','withdrawal':'条件未確認の外部実行はしない'})
        count=d.run(p,plan); d.pause(p,plan)
        comparison,data=d.compare(p,ids)
        review=d.validation_review(p,ids[2])
        result={'mode':'synthetic_temporary_database_not_real_market_or_income','real_representative_approval':False,
            'ideas':[dict(r) for r in store.db.execute('SELECT id,title,business_id,brief,status FROM bd_ideas')],
            'forecast':json.loads(store.db.execute('SELECT body FROM bd_proposals WHERE id=?',(proposal,)).fetchone()[0])['scenarios'],
            'before_proposal_approval_internal_tasks':before,'selected_draft_roles':[r[0] for r in store.db.execute('SELECT agent FROM bd_draft_work WHERE proposal_id=?',(proposal,))],
            'completed_local_checks':count,'plan_state':'paused','comparison':data,'validation_review_state':'approval_pending',
            'actual_records':store.db.execute('SELECT COUNT(*) FROM bd_actuals').fetchone()[0],
            'portfolio_ledger_records':store.db.execute('SELECT COUNT(*) FROM portfolio_ledger').fetchone()[0],
            'registered_real_businesses':store.db.execute('SELECT COUNT(*) FROM portfolio_businesses').fetchone()[0],
            'real_external_services':[],'new_paid_api_calls':0,'external_attempts':store.db.execute('SELECT COUNT(*) FROM ix_attempts').fetchone()[0],
            'foreign_key_violations':len(store.db.execute('PRAGMA foreign_key_check').fetchall()),'production_started':False}
        assert before==0 and count==7 and result['actual_records']==result['portfolio_ledger_records']==result['external_attempts']==0
        store.close(); return result

if __name__=='__main__': print(json.dumps(demo(),ensure_ascii=False,indent=2))
