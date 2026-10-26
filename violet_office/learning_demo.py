"""Reproducible synthetic phase-8 evidence; temporary DB, no external calls."""
import json
import tempfile
from datetime import timedelta
from pathlib import Path
from .store import Store,now
from .office import Office
from .content import SAMPLE_BRIEF
from .integrations.adapters import Principal


def demo():
    with tempfile.TemporaryDirectory() as directory:
        store=Store(Path(directory)/'synthetic.db')
        office=Office(store)
        learning=office.learning
        owner=Principal('representative','synthetic_test_owner')
        before=office.content.generate('synthetic-baseline',SAMPLE_BRIEF,start_date='2030-01-10')
        first=next(c for c in office.content.report(before)['contents'] if c['kind']=='sns')
        feedback=learning.feedback(owner,'synthetic-guidance','content',first['id'],'standard','もっと親しみやすく、本人の希望を尊重した案内にする',{'friendly':True,'soft_cta':True,'evidence_note':True},True)
        learning.decide_feedback(owner,feedback,'approve')
        knowledge=learning.promote_feedback(owner,feedback,'合成テスト用のSNS文章指導',['AI09'],(now()+timedelta(days=30)).isoformat())
        learning.decide_knowledge(owner,knowledge,'approve')
        evaluation,evaluated=learning.evaluate(owner,'sns',(now().date()-timedelta(days=1)).isoformat(),(now().date()+timedelta(days=1)).isoformat())
        proposals=learning.propose(owner,'sns',[knowledge],evaluation)
        style=proposals[0]
        comparison=learning.compare(owner,style)
        learning.decide_proposal(owner,style,'approve')
        learning.apply(owner,style)
        after=office.content.generate('synthetic-after',SAMPLE_BRIEF,start_date='2030-01-10')
        posts=[c for c in office.content.report(after)['contents'] if c['kind']=='sns']
        evidence={'mode':'local_synthetic_temporary_database','not_representative_production_approval':True,
            'recorded_at':now().isoformat(),'feedback_id':feedback,'knowledge_id':knowledge,
            'knowledge_version':1,'improvement_id':style,'comparison':comparison,
            'proposal_count':len(proposals),'non_style_proposals':'manual_only',
            'evaluation_id':evaluation,'evaluation':evaluated,
            'approval_events':[dict(r) for r in store.db.execute('SELECT actor,action,ref,created_at FROM learning_audit')],
            'sample_posts':[{'title':c['body']['title'],'body':c['body']['body'],'cta':c['body']['cta'],'status':c['status']} for c in posts],
            'knowledge_usage_count':store.db.execute('SELECT COUNT(*) FROM learning_usages WHERE knowledge_id=?',(knowledge,)).fetchone()[0],
            'external_attempts':store.db.execute('SELECT COUNT(*) FROM ix_attempts').fetchone()[0],
            'new_paid_api_calls':0,'actual_api_bill':'未取得・未計測','production_started':False}
        assert all(c['status']=='approval_pending' for c in posts)
        assert evidence['external_attempts']==0
        store.close()
        return evidence


if __name__=='__main__':
    print(json.dumps(demo(),ensure_ascii=False,indent=2))
