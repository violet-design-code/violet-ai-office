"""Opt-in local scheduler. Starts no daemon or external integration by import."""
import argparse
import json
import os
import time
import sqlite3
from pathlib import Path

from .store import Store
from .office import Office
from .growth import CADENCES


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description='日次・週次・月次のローカル業務管理（本番運用なし）')
    p.add_argument('command',choices=['run','tick','watch','pause','resume','status'])
    p.add_argument('--db',default='var/office.sqlite3')
    p.add_argument('--cadence',choices=CADENCES,default='daily')
    p.add_argument('--period',choices=['current','previous'],default='current')
    p.add_argument('--key')
    p.add_argument('--schedule-id')
    p.add_argument('--interval',type=int,default=30)
    args=p.parse_args()
    if os.environ.get('INTEGRATION_MODE','mock') not in ('local','test','mock'):
        p.error('本番モードは未対応です')
    if not Path(args.db).is_file():
        p.error('先にWebのinitで管理DBを作成してください')
    if not 5<=args.interval<=60:
        p.error('監視間隔は5〜60秒です')
    if args.command=='run' and not args.key or args.command in ('pause','resume') and not args.schedule_id:
        p.error('--key または --schedule-id が必要です')
    try:
        while True:
            store=Store(args.db)
            try:
                office=Office(store)
                growth=office.growth
                if args.command in ('tick','watch'):
                    result={'run_ids':growth.tick(),'mode':'local_rules_v1','actual_api_calls':0}
                elif args.command=='run':
                    result={'run_id':growth.run(args.key,args.cadence,mode=args.period),'actual_api_calls':0}
                elif args.command in ('pause','resume'):
                    growth.schedule_state(args.schedule_id,args.command=='resume','local_os_operator')
                    result={'state':'enabled_local' if args.command=='resume' else 'paused'}
                else:
                    result={'schedules':[dict(r) for r in store.db.execute('SELECT * FROM growth_schedules')],
                        'runs':[dict(r) for r in store.db.execute('SELECT id,cadence,status,attempts,error_code,decision FROM growth_runs ORDER BY created_at DESC LIMIT 30')]}
                print(json.dumps(result,ensure_ascii=False),flush=True)
            finally:
                store.close()
            if args.command!='watch':
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print('ローカルスケジューラーを停止しました。外部処理はありません。')
    except (ValueError,OSError,KeyError,sqlite3.Error):
        p.exit(2,'処理できません。設定・状態・試行上限を確認してください。秘密値は表示しません。\n')


if __name__=='__main__':
    main()
