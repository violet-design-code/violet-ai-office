"""Phase20 additive metadata, scoped monthly goals and confirmation forms."""
import re
from pathlib import Path
from .store import now
from .portfolio import text


def migrate(store):
    if store.db.execute('SELECT 1 FROM office_migrations WHERE version=20').fetchone():return
    def op():
        for sql in (Path(__file__).resolve().parent.parent/'migrations/020_business_management.sql').read_text().split(';'):
            if sql.strip():store.db.execute(sql)
        store.db.execute('INSERT INTO office_migrations VALUES(20,?)',(now().isoformat(),))
    store._transaction(op)


def validate(o,p,v,b=None):
    o.readiness.access(p,b,True)
    if not isinstance(v,dict) or set(v)!={'provided_services','audience','manager','month','revenue_yen','profit_yen','status','confirmed'}:raise ValueError('管理設定の項目を確認してください。')
    result=dict(v)
    for k in ('provided_services','audience'):
        result[k]=text(v[k],2000) if v[k] else ''
    if v['manager'] is not None:
        row=o.db.execute("SELECT role FROM office_users WHERE username=?",(v['manager'],)).fetchone()
        if not row or row['role'] not in ('representative','business_operator'):raise ValueError('既存の管理者・事業担当者を選択してください。')
    if v['status'] not in ('preparing','active','paused') or type(v['confirmed']) is not bool or v['status']=='active' and not v['confirmed']:raise ValueError('運営中への変更は提供状況の代表確認が必要です。')
    if v['month'] is not None and (not isinstance(v['month'],str) or not re.fullmatch(r'[0-9]{4}-(0[1-9]|1[0-2])',v['month']) or v['month'].startswith('0000')):raise ValueError('対象月を確認してください。')
    for k in ('revenue_yen','profit_yen'):
        n=v[k]
        if n is not None and (type(n) is not int or abs(n)>10**12 or k=='revenue_yen' and n<0):raise ValueError('目標は円単位の整数で入力してください。')
        if n is not None and not v['month']:raise ValueError('目標の対象月が必要です。')
    return result


def apply(o,p,b,v):
    v=validate(o,p,v,b)
    o.db.execute('INSERT INTO bm_profiles VALUES(?,?,?,?) ON CONFLICT(business_id) DO UPDATE SET provided_services=excluded.provided_services,audience=excluded.audience,manager=excluded.manager',(b,v['provided_services'],v['audience'],v['manager']))
    if v['month']:
        o.db.execute('INSERT INTO bm_month_goals VALUES(?,?,?,?) ON CONFLICT(business_id,month) DO UPDATE SET revenue_yen=excluded.revenue_yen,profit_yen=excluded.profit_yen',(b,v['month'],v['revenue_yen'],v['profit_yen']))
    r=o.business_foundation.get(p,b,True)
    o.business_foundation.update(p,b,r['overview'],r['category'],r['started_on'],v['status']!='paused',v['status'],v['confirmed'],r['revision'],r['business_revision'])
    # Internal goals/manager metadata do not rewrite or publish the existing public profile.
