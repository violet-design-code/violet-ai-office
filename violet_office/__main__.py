import argparse
import json
from pathlib import Path

from .registry import load_agents
from .store import Store
from .coordinator import Coordinator


def main():
    parser = argparse.ArgumentParser(description='Violet offline phase-one administration')
    parser.add_argument('command', choices=['agents', 'init', 'status', 'plan', 'submit', 'request', 'collect', 'result', 'review', 'decide', 'queue'])
    parser.add_argument('--db', default='var/office.sqlite3')
    parser.add_argument('--instruction')
    parser.add_argument('--key')
    parser.add_argument('--priority', type=int, choices=range(1,6), default=3)
    parser.add_argument('--request-id')
    parser.add_argument('--task-id')
    parser.add_argument('--artifact-id')
    parser.add_argument('--content-file', help='UTF-8 anonymized specialist result')
    parser.add_argument('--reviewer')
    parser.add_argument('--verdict', choices=['pass','changes_requested'])
    parser.add_argument('--decision', choices=['approve','reject'])
    args = parser.parse_args()
    requirements = {
        'plan':['instruction'], 'submit':['instruction','key'], 'request':['request_id'],
        'collect':['request_id'], 'result':['task_id','content_file'],
        'review':['artifact_id','reviewer','verdict'], 'decide':['artifact_id','decision'],
    }
    for field in requirements.get(args.command, []):
        if not getattr(args, field):
            parser.error('Missing --' + field.replace('_','-'))
    if args.command == 'agents':
        print(json.dumps(list(load_agents().values()), ensure_ascii=False, indent=2))
        return
    path = Path(args.db)
    if args.command == 'init':
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    elif not path.is_file():
        parser.error('Database does not exist; run init first')
    store = Store(path)
    try:
        path.chmod(0o600)
        coordinator = Coordinator(store)
        if args.command == 'init':
            output = {'status':'initialized'}
        elif args.command == 'status':
            output = {'status':store.summary()}
        elif args.command == 'queue':
            output = {'tasks':coordinator.queue()}
        elif args.command == 'plan':
            output = coordinator.analyze(args.instruction)
        elif args.command == 'submit':
            output = {'request_id':coordinator.submit(args.key,args.instruction,args.priority)}
        elif args.command == 'request':
            output = coordinator.report(args.request_id)
        elif args.command == 'collect':
            output = {'artifact_id':coordinator.collect(args.request_id)}
        elif args.command == 'result':
            content = Path(args.content_file).read_text(encoding='utf-8')
            if not content.strip() or len(content)>100000:
                raise ValueError('Result must contain 1 to 100000 characters')
            # Result import is atomic: a failed import does not leave an occupied task.
            output = store._transaction(lambda: {'artifact_id':store.finish(store.claim(args.task_id),content)})
        elif args.command == 'review':
            store.review(args.artifact_id,args.reviewer,args.verdict)
            output = {'status':'review_recorded'}
        else:
            store.decide(args.artifact_id,args.decision,'representative')
            output = {'status':'decision_recorded'}
        print(json.dumps(output, ensure_ascii=False, indent=2))
    except (ValueError, OSError) as error:
        # Do not echo content, input data or raw upstream errors.
        parser.exit(2, '操作を完了できません。引数・状態・ファイルの存在を確認してください。\n')
    finally:
        store.close()


if __name__ == '__main__':
    main()
