"""Trusted local-only CLI. Representative actions require the human operator."""
import argparse
import json
import os
from pathlib import Path

from .content import ContentOffice, SAMPLE_BRIEF, CHECKS
from .store import Store


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description='バイオレット原稿管理（外部送信なし）')
    p.add_argument('command',choices=['generate','report','dashboard','revise','audit','confirm','decide','publication'])
    p.add_argument('--db',default='var/office.sqlite3')
    p.add_argument('--key')
    p.add_argument('--brief',default=SAMPLE_BRIEF)
    p.add_argument('--days',type=int,choices=[7,14,30],default=7)
    p.add_argument('--platform',choices=['Instagram','Facebook','LINE'],default='Instagram')
    p.add_argument('--start-date')
    p.add_argument('--campaign-id')
    p.add_argument('--content-id')
    p.add_argument('--body-file')
    p.add_argument('--check',choices=CHECKS)
    p.add_argument('--evidence')
    p.add_argument('--decision',choices=['approve','reject'])
    p.add_argument('--output')
    args=p.parse_args()
    requirements={'generate':['key'],'report':['campaign_id'],'dashboard':['output'],
        'revise':['content_id','body_file'],'audit':['content_id'],'confirm':['content_id','check','evidence'],
        'decide':['content_id','decision'],'publication':['content_id','evidence']}
    for field in requirements[args.command]:
        if not getattr(args,field):
            p.error('Missing --'+field.replace('_','-'))
    db=Path(args.db)
    db.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    store=Store(db)
    try:
        db.chmod(0o600)
        office=ContentOffice(store)
        if args.command=='generate':
            result={'campaign_id':office.generate(args.key,args.brief,args.days,args.platform,args.start_date)}
        elif args.command=='report':
            result=office.report(args.campaign_id)
        elif args.command=='dashboard':
            path=Path(args.output)
            # Never blindly overwrite a user file.
            with path.open('x',encoding='utf-8') as file:
                file.write(office.dashboard())
            result={'dashboard':str(path),'mode':'local_read_only'}
        elif args.command=='revise':
            result={'content_id':office.revise(args.content_id,json.loads(Path(args.body_file).read_text(encoding='utf-8')))}
        elif args.command=='audit':
            office.audit(args.content_id)
            result={'status':'quality_checked'}
        elif args.command=='confirm':
            office.confirm_check(args.content_id,args.check,args.evidence)
            result={'status':'confirmation_recorded'}
        elif args.command=='decide':
            office.decide(args.content_id,args.decision,'representative')
            result={'status':'decision_recorded'}
        else:
            office.record_publication(args.content_id,args.evidence,'representative')
            result={'status':'manual_publication_recorded','network_operation':False}
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (ValueError,OSError):
        store.db.execute('INSERT INTO pipeline_errors(command,code,created_at) VALUES (?,?,?)',(args.command,'invalid_input_or_state',__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()))
        p.exit(2,'処理できません。入力・状態・未確認項目を確認してください。本文や秘密値は表示しません。\n')
    finally:
        store.close()


if __name__=='__main__':
    main()
