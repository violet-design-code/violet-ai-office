"""Opt-in local reminder preparation worker. No sending, payment or public server."""
import argparse
import json
import time
from .store import Store
from .office import Office
from .integrations.adapters import Principal

def main():
    p=argparse.ArgumentParser(description='セミナー3日前の下書き準備のみ。初期停止、実配信なし')
    p.add_argument('command',choices=['status','tick','watch'])
    p.add_argument('--db',default='var/office.sqlite3')
    p.add_argument('--interval',type=int,default=60)
    a=p.parse_args()
    if not 30<=a.interval<=3600: p.error('interval must be 30..3600')
    # Never create a business DB implicitly from a scheduled command.
    from pathlib import Path
    if not Path(a.db).is_file(): p.error('Initialize a local office database first')
    try:
        while True:
            store=Store(a.db)
            try:
                office=Office(store)
                if a.command=='status':
                    result=dict(store.db.execute('SELECT * FROM seminar_scheduler WHERE id=1').fetchone())
                else:
                    # Trusted local OS operator, CLI cannot approve or dispatch reminders.
                    try: result={'prepared':office.seminars.tick(Principal('representative','local_preparation_worker')),'mode':'draft_only'}
                    except Exception:
                        store.db.execute("UPDATE seminar_scheduler SET enabled=0,state='error_hold' WHERE id=1")
                        office.seminars.event('local_worker','scheduler_error','scheduler',{'code':'preparation_failed'})
                        result={'state':'error_hold','code':'preparation_failed'}
                print(json.dumps(result,ensure_ascii=False),flush=True)
            finally: store.close()
            if a.command!='watch': break
            time.sleep(a.interval)
    except KeyboardInterrupt: pass

if __name__=='__main__': main()
