"""Read-only diagnostics, consistent SQLite backups and quarantined NEW restores.

Never overwrites or deletes an existing database. No provider calls or deployment.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import time
from collections import deque
from pathlib import Path
from uuid import uuid4

from .store import now


def read_db(path):
    path=Path(path).resolve()
    if not path.is_file():
        raise ValueError('Database does not exist')
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=10)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA trusted_schema=OFF')
    return db


def tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def health(path):
    db=read_db(path)
    try:
        integrity=[r[0] for r in db.execute('PRAGMA integrity_check')]
        foreign_keys=list(db.execute('PRAGMA foreign_key_check'))
        known=tables(db)
        cycles=False
        if 'dependencies' in known:
            edges=[('task:'+r[0],'task:'+r[1]) for r in db.execute('SELECT task_id,prerequisite FROM dependencies')]
            if 'growth_edges' in known:
                edges.extend(('work:'+r[0],'work:'+r[1]) for r in db.execute('SELECT work_id,prerequisite FROM growth_edges'))
            incoming={node:0 for edge in edges for node in edge}
            next_nodes={node:[] for node in incoming}
            for node,prerequisite in edges:
                incoming[node]+=1
                next_nodes[prerequisite].append(node)
            queue=deque(node for node,count in incoming.items() if not count)
            visited=0
            while queue:
                node=queue.popleft()
                visited+=1
                for following in next_nodes[node]:
                    incoming[following]-=1
                    if incoming[following]==0:
                        queue.append(following)
            cycles=visited!=len(incoming)
        application_schema={'tasks','runs','artifacts','budget'}<=known
        return {'application_schema':application_schema,'integrity_ok':integrity==['ok'],'foreign_key_violations':len(foreign_keys),
            'dependency_cycle':cycles,'schema_tables':len(known),
            'healthy':application_schema and integrity==['ok'] and not foreign_keys and not cycles}
    finally:
        db.close()


def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()


def reserve_file(path):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    os.close(descriptor)
    return path


def copy_database(source,target,timeout_seconds=30):
    deadline=time.monotonic()+timeout_seconds
    def progress(status,remaining,total):
        if time.monotonic()>deadline:
            raise TimeoutError('Snapshot timeout')
    source.backup(target,pages=256,sleep=0.05,progress=progress)


def backup(source,destination):
    source=Path(source).resolve()
    destination=Path(destination).resolve()
    if not health(source)['healthy']:
        raise ValueError('Source database requires investigation')
    manifest=Path(str(destination)+'.backup.json')
    if destination.exists() or manifest.exists():
        raise FileExistsError('Output already exists')
    created=False
    try:
        reserve_file(destination)
        created=True
        src=read_db(source)
        target=sqlite3.connect(destination)
        try:
            copy_database(src,target)
        finally:
            src.close()
            target.close()
        check=health(destination)
        if not check['healthy']:
            raise ValueError('Snapshot validation failed')
        metadata={'format_version':1,'snapshot_id':uuid4().hex,'created_at':now().isoformat(),
            'sha256':file_hash(destination),'bytes':destination.stat().st_size,
            'health':check,'encryption':'none','trust':'checksum only; use a trusted, access-controlled backup source'}
        with manifest.open('x',encoding='utf-8') as file:
            os.chmod(manifest,0o600)
            json.dump(metadata,file,ensure_ascii=False,indent=2)
        return metadata
    except BaseException:
        # Clean up only this call's newly created incomplete output, never an existing file.
        if created:
            destination.unlink(missing_ok=True)
        raise


def restore(snapshot,destination):
    snapshot=Path(snapshot).resolve()
    destination=Path(destination).resolve()
    if destination.exists():
        raise FileExistsError('Restore requires a NEW destination')
    metadata=json.loads(Path(str(snapshot)+'.backup.json').read_text())
    if metadata.get('format_version')!=1 or metadata.get('sha256')!=file_hash(snapshot) or not health(snapshot)['healthy']:
        raise ValueError('Invalid backup or checksum')
    created=False
    try:
        reserve_file(destination)
        created=True
        src=read_db(snapshot)
        db=sqlite3.connect(destination,isolation_level=None)
        try:
            copy_database(src,db)
            known=tables(db)
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('BEGIN IMMEDIATE')
            counts={}
            if 'office_sessions' in known:
                counts['sessions_invalidated']=db.execute('DELETE FROM office_sessions').rowcount
            if 'runs' in known:
                counts['runs_quarantined']=db.execute("UPDATE runs SET status='interrupted',error_code='restore_interrupted' WHERE status='running'").rowcount
            if 'tasks' in known:
                counts['tasks_paused']=db.execute("UPDATE tasks SET status='paused' WHERE status='running'").rowcount
            if 'ix_operations' in known:
                counts['operations_held']=db.execute("UPDATE ix_operations SET status='manual_reconciliation',approved_digest=NULL,next_attempt=NULL WHERE status IN ('running','approved','retry_wait','approval_pending')").rowcount
            if 'ix_steps' in known:
                counts['steps_held']=db.execute("UPDATE ix_steps SET status='recovery_hold' WHERE status='scheduled_mock'").rowcount
            if 'growth_schedules' in known:
                counts['growth_schedules_paused']=db.execute("UPDATE growth_schedules SET enabled=0,state='recovery_hold'").rowcount
            if 'growth_runs' in known:
                counts['growth_runs_held']=db.execute("UPDATE growth_runs SET status='paused',lease=NULL,error_code='recovery_hold' WHERE status IN ('running','pending')").rowcount
                counts['growth_plans_reapproval']=db.execute("UPDATE growth_runs SET decision='approval_pending' WHERE decision='approve'").rowcount
            if 'growth_proposals' in known:
                counts['growth_proposals_held']=db.execute("UPDATE growth_proposals SET status='approval_pending' WHERE status='approved_local'").rowcount
            if 'sc_settings' in known:
                counts['private_schedule_stopped']=db.execute('UPDATE sc_settings SET enabled=0,consent=0').rowcount
            if 'sc_notifications' in known:
                counts['private_notifications_held']=db.execute("UPDATE sc_notifications SET state='recovery_hold' WHERE state IN ('pending','retry_wait')").rowcount
            if 'br_requests' in known:
                counts['readiness_reapproval']=db.execute("UPDATE br_requests SET status='approval_pending',approved_by=NULL,approved_at=NULL WHERE status='approved'").rowcount
            if 'aw_jobs' in known:
                counts['collaboration_jobs_held']=db.execute("UPDATE aw_jobs SET state='paused',next_at=NULL").rowcount
                db.execute("UPDATE aw_changes SET status='approval_pending',approved_by=NULL,approved_at=NULL WHERE status='approved_manual_only'")
            if 'cj_profiles' in known:
                counts['journey_profiles_recheck']=db.execute("UPDATE cj_profiles SET status='pending',approved_by=NULL,approved_at=NULL").rowcount
            if 'bf_ai_assignments' in known:
                counts['business_assignments_held']=db.execute('UPDATE bf_ai_assignments SET active=0,revision=revision+1').rowcount
            if 's14_work' in known:
                counts['s14_paused']=db.execute('UPDATE s14_work SET paused=1,approved_by=NULL').rowcount
                counts['s14_task_recheck']=db.execute("UPDATE tasks SET status='review_pending' WHERE id IN(SELECT task_id FROM s14_work WHERE artifact_id IS NOT NULL) AND status IN ('manual_execution_pending','approval_pending')").rowcount
                counts['s14_drafts_recheck']=db.execute("UPDATE s14_drafts SET status='approval_pending',approved_by=NULL,next_at=NULL WHERE status IN ('send_waiting_no_api','retry_wait')").rowcount
                db.execute('UPDATE s14_scheduler SET enabled=0')
            if 'wf_tasks' in known:
                counts['workflow_paused']=db.execute('UPDATE wf_tasks SET paused=1,approved_by=NULL').rowcount
                counts['workflow_tasks_recheck']=db.execute("UPDATE tasks SET status='review_pending' WHERE id IN(SELECT task_id FROM wf_tasks WHERE artifact_id IS NOT NULL) AND status IN ('manual_execution_pending','approval_pending')").rowcount
                counts['workflow_drafts_recheck']=db.execute("UPDATE wf_drafts SET status='approval_pending',approved_by=NULL WHERE status='send_waiting_no_api'").rowcount
            if 'scm_actions' in known:
                counts['sales_actions_recheck']=db.execute("UPDATE scm_actions SET status='approval_pending',next_at=NULL WHERE status IN ('approved','retry_wait')").rowcount
                counts['sales_quotes_recheck']=db.execute("UPDATE scm_quotes SET status='approval_pending' WHERE status='approved'").rowcount
            if 'bd_plans' in known:
                counts['development_plans_paused']=db.execute("UPDATE bd_plans SET status='paused'").rowcount
                counts['development_reviews_recheck']=db.execute("UPDATE bd_validation_reviews SET status='approval_pending' WHERE status='approved'").rowcount
                counts['development_proposals_recheck']=db.execute("UPDATE bd_proposals SET status='approval_pending' WHERE status='approved'").rowcount
            if 'portfolio_controls' in known:
                counts['portfolio_halted']=db.execute('UPDATE portfolio_controls SET halted=1').rowcount
                counts['portfolio_documents_recheck']=db.execute("UPDATE portfolio_documents SET status='approval_pending' WHERE status='approved'").rowcount
                counts['portfolio_operations_recheck']=db.execute("UPDATE portfolio_operations SET status='approval_pending' WHERE status='approved'").rowcount
                counts['portfolio_reports_recheck']=db.execute("UPDATE portfolio_reports SET status='approval_pending' WHERE status='approved'").rowcount
            if 'seminar_scheduler' in known:
                counts['seminar_scheduler_paused']=db.execute("UPDATE seminar_scheduler SET enabled=0,state='recovery_hold'").rowcount
                counts['seminar_reminders_held']=db.execute("UPDATE seminar_reminders SET status='cancelled' WHERE status IN ('approval_pending','approved','retry_wait')").rowcount
                counts['seminar_registrations_recheck']=db.execute("UPDATE seminar_registrations SET status='approval_pending',revision=revision+1 WHERE status='approved'").rowcount
            if 'selection_settings' in known:
                counts['selection_paused']=db.execute('UPDATE selection_settings SET enabled=0').rowcount
            if 'learning_knowledge' in known:
                counts['knowledge_recheck']=db.execute("UPDATE learning_knowledge SET status='approval_pending' WHERE status='approved'").rowcount
            if 'learning_feedback' in known:
                counts['feedback_recheck']=db.execute("UPDATE learning_feedback SET status='approval_pending' WHERE status='approved'").rowcount
            if 'learning_proposals' in known:
                counts['improvements_recheck']=db.execute("UPDATE learning_proposals SET status='approval_pending' WHERE status IN ('applied','approved')").rowcount
            if 'content_versions' in known:
                approved=db.execute("SELECT id FROM content_versions WHERE status='approved'").fetchall()
                counts['contents_requiring_reapproval']=len(approved)
                for row in approved:
                    db.execute("UPDATE content_versions SET status='approval_pending' WHERE id=?",(row[0],))
                    if 'content_events' in known:
                        db.execute('INSERT INTO content_events(content_id,event,actor,detail,created_at) VALUES (?,?,?,?,?)',
                            (row[0],'recovery_reapproval_required','recovery','Restored copy; human verification required',now().isoformat()))
            if 'office_audit' in known:
                db.execute('INSERT INTO office_audit(actor,action,ref,created_at) VALUES (?,?,?,?)',
                    ('recovery','restored_quarantined',metadata['snapshot_id'],now().isoformat()))
            db.execute('COMMIT')
        except BaseException:
            if db.in_transaction:
                db.execute('ROLLBACK')
            raise
        finally:
            src.close()
            db.close()
        check=health(destination)
        if not check['healthy']:
            raise ValueError('Restored database validation failed')
        return {'restored_to_new_file':True,'health':check,'quarantine':counts,'automatic_resume':False,'source_snapshot_id':metadata['snapshot_id']}
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise


def retention_plan(path):
    db=read_db(path)
    try:
        known=tables(db)
        count=db.execute('SELECT COUNT(*) FROM ix_customers WHERE expires_at<=?',(now().isoformat(),)).fetchone()[0] if 'ix_customers' in known else None
        return {'expired_customer_count':count,'deletion_executed':False,'requires_human_approval':True,
            'review':['保存義務・利用目的・本人確認','予約/結果不明操作の照合','同意/監査/重複防止記録の最小保持','暗号化バックアップと保管期限','既存LINE側との調整'],
            'retention_policy':'顧客は取得から90日を暫定上限、目的・法的根拠・バックアップを含む最終方針は代表確認'}
    finally:
        db.close()


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description='バイオレット診断・バックアップ・新規ファイル復元')
    p.add_argument('command',choices=['health','backup','restore','retention-plan'])
    p.add_argument('--db',default='var/office.sqlite3')
    p.add_argument('--output')
    args=p.parse_args()
    if args.command in ('backup','restore') and not args.output:
        p.error('--output が必要です。既存ファイルは上書きしません')
    try:
        if args.command=='health':
            result=health(args.db)
        elif args.command=='backup':
            result=backup(args.db,args.output)
        elif args.command=='restore':
            result=restore(args.db,args.output)
        else:
            result=retention_plan(args.db)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        if result.get('healthy') is False:
            p.exit(1)
    except (ValueError,OSError,sqlite3.Error,KeyError,TypeError):
        p.exit(2,'処理できません。ファイル・整合性・出力先を確認してください。既存DBは上書きしていません。\n')


if __name__=='__main__':
    main()
