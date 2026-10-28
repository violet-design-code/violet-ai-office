"""Closed-DB diagnostics and exclusive SQLite backups only. No repair/apply mode."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from .repair_trial import snapshot


def digest_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for part in iter(lambda: f.read(1024*1024), b''):
            h.update(part)
    return h.hexdigest()


def closed_db(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise ValueError('Existing database file required')
    # immutable must only be used after ALL writers are stopped; it ignores WAL.
    for suffix in ('-wal', '-journal'):
        sidecar = Path(str(path)+suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError('Nonempty WAL/journal: stop; do not delete/checkpoint via this tool')
    return path


def open_closed(path):
    db = sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1', uri=True)
    db.execute('PRAGMA query_only=ON')
    db.execute('PRAGMA trusted_schema=OFF')
    return db


def fingerprint(db):
    schema, tables = snapshot(db)
    # Type-preserving Python representations, including duplicate row counts and blobs.
    return {'schema_sha256': hashlib.sha256(repr(schema).encode()).hexdigest(),
            'tables': {t: {'rows': sum(rows.values()), 'sha256': hashlib.sha256(
                repr(sorted((repr(row), count) for row, count in rows.items())).encode()).hexdigest()}
                       for t, rows in sorted(tables.items())}}


def inspect(path):
    path = closed_db(path)
    with closing(open_closed(path)) as db:
        db.execute('BEGIN')
        integrity = [r[0] for r in db.execute('PRAGMA integrity_check')]
        fk = [list(r) for r in db.execute('PRAGMA foreign_key_check')]
        result = {'path': str(path), 'file_sha256': digest_file(path),
                  'integrity': integrity, 'foreign_key_violations': fk, **fingerprint(db)}
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('portfolio_businesses', 'bf_profiles', 'bf_ai_assignments'):
            if table in names:
                cur = db.execute('SELECT * FROM '+table)
                columns = [r[0] for r in cur.description]
                result[table] = [dict(zip(columns, r)) for r in cur]
        result['agents_config_sha256'] = digest_file(Path(__file__).resolve().parent.parent/'config/agents.json')
        return result


def backup(source, destination):
    source = closed_db(source)
    destination = Path(destination).resolve()
    if source == destination or destination.exists() or not destination.parent.is_dir():
        raise ValueError('New distinct destination in an existing directory required')
    for suffix in ('-wal', '-shm', '-journal'):
        if Path(str(destination)+suffix).exists():
            raise ValueError('Destination sidecars exist')
    before = digest_file(source)
    with closing(open_closed(source)) as src:
        if src.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('Integrity check failed; stop and preserve source')
        expected = fingerprint(src)
        expected_fk = src.execute('PRAGMA foreign_key_check').fetchall()
        # Retain known FK errors in the evidence backup. Never disable constraints to hide them.
        with destination.open('xb'):
            pass
        try:
            with closing(sqlite3.connect(destination)) as dst:
                src.backup(dst)
                if fingerprint(dst) != expected or dst.execute('PRAGMA foreign_key_check').fetchall() != expected_fk or dst.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise ValueError('Backup verification failed')
            if digest_file(source) != before:
                raise ValueError('Source changed; stopped-writer precondition violated')
        except BaseException:
            # Preserve newly created failure evidence; do not delete anything automatically.
            raise
    return {'source': str(source), 'destination': str(destination),
            'source_unchanged': True, 'logical_data_equal': True,
            'foreign_key_errors_preserved': len(expected_fk),
            'destination_sha256': digest_file(destination)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('inspect', 'backup'))
    parser.add_argument('--source', required=True)
    parser.add_argument('--output')
    parser.add_argument('--writers-stopped', action='store_true', required=True,
                        help='Operator confirms all writers stopped; not an automatic process check')
    args = parser.parse_args()
    try:
        if args.command == 'backup':
            if not args.output:
                raise ValueError('--output required')
            result = backup(args.source, args.output)
        else:
            if args.output:
                raise ValueError('inspect writes only to stdout; use a NEW local report path')
            result = inspect(args.source)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, 'Preparation stopped: '+str(exc)+'\n')


if __name__ == '__main__':
    main()
