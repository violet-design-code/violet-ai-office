"""Offline trial only: read a validation DB, create a new isolated candidate DB."""
import argparse
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from .business_foundation import ai_allowed
from .portfolio import text
from .registry import load_agents


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def snapshot(db):
    schema = tuple(db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name'))
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    return schema, {t: Counter(tuple(r) for r in db.execute('SELECT * FROM '+quote(t))) for t in tables}


def paths(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    # The destination namespace is deliberately narrow; no in-place/apply mode exists.
    for p in (source, output):
        if p.name.lower() == 'office.sqlite3' or any('backup' in part.lower() for part in p.parts):
            raise ValueError('Live DB and backup paths are prohibited')
    if output.parent.name != 'repair-trials' or not output.name.startswith('trial-') or output.suffix != '.sqlite3':
        raise ValueError('Output must be repair-trials/trial-*.sqlite3')
    if source == output or output.exists() or not source.is_file():
        raise ValueError('Existing validation input and a new distinct output are required')
    wal = Path(str(source)+'-wal')
    if wal.exists() and wal.stat().st_size:
        raise ValueError('Input has a nonempty WAL; use a closed standalone validation copy')
    if not output.parent.is_dir():
        raise ValueError('Create the isolated repair-trials directory first')
    return source, output


def trial(source, output, category):
    source, output = paths(source, output)
    category = text(category, 120)  # Same extensible category validation as create/adopt/update.
    agents = load_agents()
    db = sqlite3.connect(':memory:')
    try:
        # mode=ro never creates an absent source or migrates the supplied database.
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro&immutable=1', uri=True)) as src:
            src.execute('PRAGMA query_only=ON')
            src.backup(db)
        before_schema, before = snapshot(db)
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('Input integrity_check failed')
        businesses = db.execute('SELECT id,services FROM portfolio_businesses').fetchall()
        if len(businesses) != 1 or db.execute('SELECT COUNT(*) FROM bf_profiles').fetchone()[0] != 0:
            raise ValueError('Expected exactly one business and zero profiles')
        business, overview = businesses[0]
        rows = db.execute('SELECT business_id,agent FROM bf_ai_assignments').fetchall()
        if len(rows) != 28 or {r[0] for r in rows} != {business} or {r[1] for r in rows} != set(agents):
            raise ValueError('Expected the same business and exactly AI01..AI28 assignments')
        fk_before = db.execute('PRAGMA foreign_key_check').fetchall()
        if len(fk_before) != 28 or any(r[0] != 'bf_ai_assignments' or r[2] != 'bf_profiles' for r in fk_before):
            raise ValueError('Unexpected foreign key violations')
        db.row_factory = sqlite3.Row
        store = SimpleNamespace(db=db, agents=agents)
        def permissions():
            return {(a,p,s): ai_allowed(store,a,business,p,strict=s)
                    for a in agents for p in ('business_read','work','finance_read') for s in (True,False)}
        old_permissions = permissions()
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('INSERT INTO bf_profiles (business_id,overview,category,started_on,enabled,ai_enforced,revision) VALUES (?,?,?,NULL,1,1,1)',
                   (business,overview,category))
        new_permissions = permissions()
        changes = [{'agent':a,'permission':p,'strict':s,'before':old_permissions[a,p,s],'after':v}
                   for (a,p,s),v in new_permissions.items() if old_permissions[a,p,s] != v]
        # Pending work and independent reviewers may lose legacy permissive access.
        impacts = []
        for row in db.execute("SELECT t.id,t.agent FROM tasks t WHERE t.status NOT IN ('completed','rejected','superseded') AND (t.id IN (SELECT task_id FROM portfolio_tasks WHERE business_id=?) OR t.id IN (SELECT task_id FROM wf_tasks WHERE business_id=?) OR t.id IN (SELECT task_id FROM s14_work WHERE business_id=?))",(business,business,business)):
            for role, agent in (('worker',row['agent']),('reviewer',agents.get(row['agent'],{}).get('reviewer'))):
                if not agent or not ai_allowed(store,agent,business,'work',strict=False):
                    impacts.append({'task':row['id'],'role':role,'agent':agent})
        db.row_factory = None
        after_schema, after = snapshot(db)
        expected = {t: c.copy() for t,c in before.items()}
        expected['bf_profiles'][(business,overview,category,None,1,1,1)] += 1
        if before_schema != after_schema or expected != after:
            raise ValueError('Unexpected schema/data change; output refused')
        if db.execute('PRAGMA foreign_key_check').fetchall() or db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('Candidate integrity checks failed')
        db.commit()
        # Exclusive creation prevents overwriting a DB, including a hard-link alias.
        with output.open('xb'):
            pass
        try:
            with closing(sqlite3.connect(output)) as dst:
                db.backup(dst)
                if snapshot(dst) != (after_schema, after):
                    raise ValueError('Output differs from checked candidate')
        except BaseException:
            output.unlink()
            raise
        return {'trial_only':True,'output':str(output),'ai_enforced_candidate':1,
                'added_profiles':1,'all_other_data_unchanged':True,'schema_unchanged':True,
                'foreign_key_errors_before':len(fk_before),'foreign_key_errors_after':0,
                'permission_changes':changes,'blocked_pending_work_or_review':impacts,
                'windows_validation_completed':False}
    finally:
        db.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--validation-db',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--category',required=True)
    args=parser.parse_args()
    try:
        print(json.dumps(trial(args.validation_db,args.output,args.category),ensure_ascii=False,indent=2))
    except (ValueError,sqlite3.Error,OSError) as exc:
        parser.exit(1,'Trial refused: '+str(exc)+'\n')


if __name__ == '__main__':
    main()
