import argparse
import json
from pathlib import Path

from .registry import load_agents
from .store import Store


def main():
    parser = argparse.ArgumentParser(description='Violet offline phase-one administration')
    parser.add_argument('command', choices=['agents', 'init', 'status'])
    parser.add_argument('--db', default='var/office.sqlite3')
    args = parser.parse_args()
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
        print(json.dumps({'status': 'initialized' if args.command == 'init' else store.summary()}, ensure_ascii=False))
    finally:
        store.close()


if __name__ == '__main__':
    main()
