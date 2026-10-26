"""Local synthetic demo and authenticated read-only management commands."""
import argparse
import json
import os
from pathlib import Path

from .store import Store
from .integrations.adapters import Authorizer
from .integrations.service import Integrations
from .integrations.demo import run_demo


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description='第4段階 外部連携モック（実送信なし）')
    p.add_argument('command',choices=['demo','status','dashboard'])
    p.add_argument('--db',default='var/phase4-mock.sqlite3')
    p.add_argument('--output')
    args=p.parse_args()
    if args.command=='dashboard' and not args.output:
        p.error('--output が必要です')
    path=Path(args.db)
    if args.command!='demo' and not path.exists():
        p.error('DBが存在しません')
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    store=Store(path)
    try:
        path.chmod(0o600)
        if args.command=='demo':
            service,principal,result=run_demo(store)
            result['notice']='合成イベント・承認・予約確定のモック試験。実際の顧客や代表承認ではありません。'
            if args.output:
                with Path(args.output).open('x',encoding='utf-8') as file:
                    file.write(service.dashboard_html(principal))
        else:
            # Tokens are read from safe environment injection, never CLI args or logs.
            names=['INTEGRATION_ADMIN_TOKEN','INTEGRATION_VIEWER_TOKEN','CRM_PSEUDONYM_KEY','LINE_CHANNEL_SECRET','LINE_EXPECTED_DESTINATION','INTEGRATION_SESSION_TOKEN']
            if any(not os.environ.get(name) for name in names):
                raise ValueError('Missing configuration')
            if os.environ.get('INTEGRATION_MODE','mock')!='mock':
                raise ValueError('Only mock mode is supported')
            auth=Authorizer(os.environ['INTEGRATION_ADMIN_TOKEN'],os.environ['INTEGRATION_VIEWER_TOKEN'])
            principal=auth.authenticate(os.environ['INTEGRATION_SESSION_TOKEN'])
            service=Integrations(store,os.environ['CRM_PSEUDONYM_KEY'],os.environ['LINE_CHANNEL_SECRET'],os.environ['LINE_EXPECTED_DESTINATION'])
            result=service.dashboard_data(principal)
            if args.command=='dashboard':
                with Path(args.output).open('x',encoding='utf-8') as file:
                    file.write(service.dashboard_html(principal))
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (ValueError,PermissionError,OSError):
        p.exit(2,'処理できません。設定・認証・状態・出力先を確認してください。秘密値は表示しません。\n')
    finally:
        store.close()


if __name__=='__main__':
    main()
