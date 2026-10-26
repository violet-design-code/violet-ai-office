"""Small read-model sections embedded in existing portfolio pages."""
from .integrations.adapters import Principal
from .web_views import e,table,card
from .portfolio_views import pf,opts,inp
from .business_operations import SOURCES


def principal(s): return Principal(s['role'],s['username'])
def kpi_table(rows):
    result=[]
    for r in rows:
        state='要再確認' if r['target_state']=='needs_review' else '達成' if r['achieved'] is True else '未達' if r['achieved'] is False else '未判定'
        result.append([e(('標準：' if r['source']=='standard' else '追加：')+r['label']),e(r['unit']),e(r['recorded'] if r['recorded'] is not None else '未計測'),e('全期間あり' if r['complete'] else '欠測・集計条件未確認'),e(r['target'] if r['target'] is not None else '未設定'),e(state),e(str(r['attainment_percent'])+'%' if r['attainment_percent'] is not None else '未判定')])
    return '<p class="micro">狭い画面では表を横にスクロールできます。</p>'+table(['KPI','単位','記録分','計測範囲','期間目標','判定','達成率'],result).replace('class="table-wrap"','class="table-wrap ops-wide"')


def section(o,s,b,start,end):
    p=principal(s); service=o.business_operations; data=service.summary(p,b,start,end); counts=data['counts']; hidden={'business':b,'start':start,'end':end}
    body='<section class="panel" id="business-operations"><h2>事業別の経営状況</h2><p>'+e(data['name'])+' / '+e(start)+' 以上〜 '+e(end)+' 未満（JST）</p><p>売上基準：'+e(data['finance_definition'])+'</p><div class="grid">'
    for label,key in [('選択基準の売上円','revenue_yen'),('記帳済み経費円','expense_yen'),('概算利益円（売上−経費）','profit_yen'),('期間内の入金−返金円','cash_yen'),('終了日時点の登録未入金円（税込）','outstanding_yen')]: body+=card(label,data[key])
    body+='</div>'+table(['記録に基づく件数','件数'],[[label,e(counts[key])] for key,label in [('customers','終了日時点の有効登録顧客ID'),('new_customers','期間中の新規登録顧客ID'),('interview_bookings','期間中の面談予約確認（SCM）'),('interviews_completed','期間中の面談完了確認（第14段階）'),('contracts_recorded','期間中の成約記録案件（SCM）'),('contracts_cancelled','期間中の契約取消案件（SCM）'),('contracts_active','期間中の成約で終了時点有効（SCM）')]])+'<p>旧経路の契約申告件数（取消未照合・別掲）：'+e(data['legacy_contract_records'] if data['legacy_contract_records'] is not None else '未計測')+'</p><p>'+e(data['measurement'])+'</p><p>'+e(data['limitations'])+'</p>'
    body+='<details><summary>売上の別系統（重複照合前・合算しない）</summary>'+table(['旧台帳記帳額円','SCM税抜請求売上円'],[[e(data['legacy_revenue_yen'] if data['legacy_revenue_yen'] is not None else '未計測'),e(data['scm_revenue_yen'] if data['scm_revenue_yen'] is not None else '未計測')]])+'</details>'
    if s['role']=='representative':
        policy=o.db.execute('SELECT * FROM ops_finance_policy WHERE business_id=?',(b,)).fetchone()
        body+='<details><summary>代表が売上集計基準を確認</summary><p>同じ取引を旧台帳とSCMへ記録している可能性があります。基準を確認して選択してください。記録や税区分の正しさは自動保証しません。</p>'+pf(s,'ops_finance',opts('source',SOURCES,data['finance_source'])+inp('evidence','代表が確認した基準・根拠の参照'),{**hidden,'revision':policy['revision'] if policy else 0},'集計基準だけ保存（取引を作らない）')+'</details>'
    body+='<h3>事業別KPI・達成状況</h3><p>実測だけを使用。欠測や集計方法不明では達成と判定しません。標準目標は既存の月別設定を利用します。</p>'+kpi_table(data['kpis'])
    if s['role']=='representative':
        definitions=o.db.execute('SELECT * FROM bf_kpis WHERE business_id=? AND active=1',(b,)).fetchall()
        for definition in definitions:
            key=definition['metric_key']; rule=o.db.execute('SELECT * FROM ops_kpi_rules WHERE business_id=? AND metric_key=?',(b,key)).fetchone(); target=o.db.execute('SELECT * FROM ops_kpi_targets WHERE business_id=? AND metric_key=? AND start_day=? AND end_day=?',(b,key,start,end)).fetchone()
            body+='<details><summary>'+e(definition['label'])+' の集計条件・この期間の目標</summary>'+pf(s,'ops_rule',opts('aggregation',{'sum':'日別の合計（全日分が必要）','last':'終了日前日の値（残高等）'},rule['aggregation'] if rule else '')+inp('evidence','集計方法の確認根拠'),{**hidden,'metric_key':key,'revision':rule['revision'] if rule else 0},'集計方法を確認（旧目標は再確認が必要）')
            if rule: body+=pf(s,'ops_target',inp('target','代表が設定する目標',target['target'] if target else '')+opts('direction',{'at_least':'以上を目標','at_most':'以下を目標'},target['direction'] if target else 'at_least')+inp('evidence','目標の根拠参照'),{**hidden,'metric_key':key,'revision':target['revision'] if target else 0},'この期間の目標を保存')
            body+='</details>'
    body+='<details><summary>この事業のAI担当状況（確認のみ）</summary><p>既存28役の設定は維持。割当の完成操作画面は今回追加しません。</p>'+table(['社員','既存役職','担当補足','有効状態'],[[e(r['agent']),e(r['role']),e(r['role_note']),e('有効' if r['active'] else '停止')] for r in service.assignments(p,b)])+'</details></section>'
    return body


def global_section(o,s,start,end):
    data=o.business_operations.global_summary(principal(s),start,end)
    body='<section class="panel"><h2>全事業の経営集計</h2><p>'+e(data['note'])+'</p><div class="grid">'
    for label,key in [('同基準の全事業売上円','revenue_yen'),('全事業の記帳経費円','expense_yen'),('同基準の全事業概算利益円','profit_yen')]: body+=card(label,data[key])
    body+='</div>'+table(['事業','売上基準','売上円','経費円','概算利益円','有効登録ID','新規登録ID','面談完了確認','有効成約案件'],[[e(r['name']),e(SOURCES[r['finance_source']]),*[e(r[k] if r[k] is not None else '未計測') for k in ('revenue_yen','expense_yen','profit_yen')],*[e(r['counts'][k]) for k in ('customers','new_customers','interviews_completed','contracts_active')]] for r in data['businesses']])
    body+='<details><summary>基準別の集計（旧台帳とSCMは別）</summary>'+table(['基準','事業数','売上円','経費円','概算利益円'],[[e(SOURCES[source]),e(values['businesses']),*[e(values[key] if values[key] is not None else '未計測') for key in ('revenue_yen','expense_yen','profit_yen')]] for source,values in data['by_basis'].items()])+'</details><p>全事業の登録顧客ID延べ件数：'+e(data['counts']['customers'])+'。同一人物の事業間利用を自動統合せず、個人情報は表示しません。</p></section>'
    return body
