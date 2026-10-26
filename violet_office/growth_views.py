"""Japanese goal/scheduler/report views; authenticated by the existing Web layer."""
import json
from datetime import datetime
from decimal import Decimal
from uuid import uuid4
from .store import now
from .growth import GOALS,METRICS,CADENCES,JST
from .web_views import e,dt,form,table,card

GOAL_LABELS={**METRICS,'line_registrations':'月間新規LINE登録','inquiries':'月間問い合わせ','referrals':'月間紹介','appointments':'月間面談予約','contracts':'月間契約','sns_posts':'SNS投稿（月間）','ai_budget_microusd':'月間AI予算（USD）','owner_minutes':'代表の月間稼働上限（分）'}
STATE={'paused':'停止中','auto_paused':'障害で自動停止','attempt_limit':'試行上限で停止','enabled_local':'有効（ローカルのみ）','recovery_hold':'復元後の確認待ち',
    'pending':'未着手','running':'ローカル実行中','failed':'失敗・自動停止','succeeded_local':'ローカル処理完了','approval_pending':'代表承認待ち','approve':'承認済み・未実行','approved_local':'承認済み・手動作業待ち','changes_requested':'修正依頼','reject':'却下'}


def growth(office,session,month=None):
    g=office.growth
    month=month or now().astimezone(JST).strftime('%Y-%m')
    goals=g.goals(month)
    body='<div class="notice">ローカル集計・ルール計画・固定テンプレートのみ。実LLM・本番APIは未接続。目標は未設定のまま保存できます。承認しても外部送信しません。</div>'
    body+='<form method="get" action="/growth"><label>目標の対象月</label><input type="month" name="month" value="'+e(month)+'" required><button>表示</button></form><h2>代表が設定する目標</h2>'
    goal_fields=''
    for key in GOALS:
        value=goals['values'][key]
        field='ai_budget_usd' if key=='ai_budget_microusd' else key
        rendered='' if value is None else str(Decimal(value)/1000000) if key=='ai_budget_microusd' else str(value)
        goal_fields+='<label>'+e(GOAL_LABELS[key])+'</label><input type="number" min="0" name="'+field+'" step="'+('0.000001' if key=='ai_budget_microusd' else '1')+'" value="'+e(rendered)+'" placeholder="未設定">'
    body+='<div class="split"><div class="panel">'+form(session,'growth_goals',goal_fields,{'month':month},'目標を保存')+'</div><div class="panel"><h2>目標の状態</h2>'+table(['項目','設定値'],[[e(GOAL_LABELS[k]),e((Decimal(v)/1000000 if k=='ai_budget_microusd' else v) if v is not None else '未設定')] for k,v in goals['values'].items()])+'<p class="micro">空欄は未設定、0は明示した目標0です。予算未設定でも従来の安全上限を解除しません。実測値を自動で目標に変更しません。</p></div></div>'
    body+='<h2>日本語で経営指示</h2><p>対応するキーワードで実行内容を選びます。自由な意味理解は未接続です。</p>'+form(session,'growth_instruction','<label>依頼（匿名情報のみ）</label><textarea name="instruction" required maxlength="4000" placeholder="今日の仕事を報告して／来週の集客計画を作って／紹介者が紹介しやすい文章を考えて／面談予約を増やす改善案を出して／今月の目標達成状況を教えて／今月のAI利用料金を教えて"></textarea>',{'key':'instruction-'+uuid4().hex},'ローカル処理を実行')
    body+='<h2>日次・週次・月次のスケジュール</h2><p>作成時は停止中です。有効化後、画面の1回実行または明示起動したCLIワーカーが処理します。Webサーバーはスケジューラーを自動起動しません。各頻度は1件までです。</p>'
    options='<label>頻度</label><select name="cadence">'+''.join('<option value="'+k+'">'+v+'</option>' for k,v in CADENCES.items())+'</select><label>集計期間</label><select name="period_mode"><option value="current">当期（実行日まで）</option><option value="previous">直前の完了期間</option></select><label>初回実行予定（日本時間）</label><input type="datetime-local" name="first_due" value="'+now().astimezone(JST).strftime('%Y-%m-%dT%H:%M')+'" required><label><input type="checkbox" name="prepare_draft" value="1"> 固定テンプレートのSNS下書き1件も準備する（承認待ち）</label>'
    body+=form(session,'growth_schedule',options,{'key':'schedule-'+uuid4().hex},'停止状態でスケジュール作成')
    rows=[]
    for s in office.db.execute('SELECT * FROM growth_schedules ORDER BY created_at'):
        controls=form(session,'growth_state',data={'id':s['id'],'enabled':'0' if s['enabled'] else '1'},caption='停止' if s['enabled'] else 'ローカル有効化',style='inline')
        if not s['enabled']:
            edit='<label>次回・基準時刻（JST）</label><input type="datetime-local" name="first_due" required value="'+datetime.fromisoformat(s['anchor']).astimezone(JST).strftime('%Y-%m-%dT%H:%M')+'"><label>集計期間</label><select name="period_mode">'+''.join('<option value="'+m+'"'+(' selected' if s['period_mode']==m else '')+'>'+t+'</option>' for m,t in [('current','当期'),('previous','直前の完了期間')])+'</select><label><input type="checkbox" name="prepare_draft" value="1"'+(' checked' if s['prepare_draft'] else '')+'>下書き1件を準備</label>'
            controls+='<details><summary>停止中の設定を変更</summary>'+form(session,'growth_schedule_edit',edit,{'id':s['id']},'設定を更新（停止のまま）')+'</details>'
        rows.append([e(CADENCES[s['cadence']]),e(STATE.get(s['state'],s['state'])),dt(s['next_due']),e('当期' if s['period_mode']=='current' else '直前の完了期間'),controls])
    body+=table(['頻度','状態','次回（JST）','集計期間','操作'],rows)
    last=office.db.execute("SELECT MAX(created_at) FROM growth_audit WHERE action='scheduler_tick'").fetchone()[0]
    body+='<p>最終の1回実行：'+(dt(last) if last else '未実行')+'。常駐ワーカーの生存監視は未実装です。</p>'
    body+=form(session,'growth_tick',caption='期限が来た定期処理を1回実行（最大3件）')
    body+='<h2>手動実測・推定の記録</h2><p>日別の確定集計値を1日・1指標・1区分につき1つ登録します。欠損日を0と扱いません。訂正は最新IDを指定して履歴を残します。実サービスからの自動取得は未接続。</p>'
    choices='<label>区分</label><select name="quality"><option value="estimated">推定</option><option value="measured">手動実測申告（根拠が必要）</option></select><label>情報源の種類</label><select name="mode"><option value="mock">モック（実績から除外）</option><option value="manual">代表の手動入力</option></select>'
    data_form='<label>指標</label><select name="metric">'+''.join('<option value="'+k+'">'+e(v)+'</option>' for k,v in METRICS.items())+'</select><label>対象日</label><input type="date" name="day" required><label>件数</label><input type="number" name="value" min="0" max="1000000" required>'+choices+'<label>情報源ID（英数字・ハイフン等、秘密値禁止）</label><input name="source" required maxlength="80"><label>根拠参照（個人情報禁止）</label><input name="evidence" required maxlength="1000"><label>訂正対象の最新ID（通常は空欄）</label><input name="replace_id" maxlength="64">'
    body+='<div class="split"><div class="panel">'+form(session,'growth_measure',data_form,{'key':'metric-'+uuid4().hex},'日別集計を登録')+'</div><div class="panel"><h3>代表の作業時間</h3>'+form(session,'growth_time','<label>対象日</label><input type="date" name="day" required><label>重複しない作業時間（分）</label><input type="number" name="minutes" min="0" max="1440" required>'+choices+'<label>根拠参照</label><input name="evidence" required maxlength="1000">',{'key':'time-'+uuid4().hex},'時間を登録')+'</div></div>'
    data=office.db.execute('SELECT * FROM growth_measurements ORDER BY rowid DESC LIMIT 60').fetchall()
    body+=table(['ID（訂正参照）','日','指標','値','区分','版'],[[e(r['id']),e(r['day']),e(METRICS[r['metric']]),e(r['value']),e(('実測申告' if r['quality']=='measured' else '推定')+'/'+('代表手動' if r['mode']=='manual' else 'モック')),e(r['revision'])] for r in data])
    body+='<h2>実行・レポート履歴</h2>'+run_list(office)
    return body


def run_list(office):
    rows=office.db.execute('SELECT * FROM growth_runs ORDER BY created_at DESC LIMIT 100').fetchall()
    return table(['レポート','期間','実行状態','承認','試行','結果コード'],[[f'<a href="/reports?id={r["id"]}">'+e(CADENCES[r['cadence']])+'</a>',e(r['period_key']),e(STATE.get(r['status'],r['status'])),e(STATE.get(r['decision'],r['decision'])),e(r['attempts']),e(r['error_code'] or 'なし')] for r in rows])


def reports(office,session,identifier=None):
    if not identifier:
        body='<div class="notice">計画承認はローカル手動タスクの開始許可までです。改善案の外部実行、原稿公開、LINE送信、予約確定を行いません。</div>'
        body+=form(session,'growth_report','<label>レポート種別</label><select name="cadence">'+''.join('<option value="'+k+'">'+v+'</option>' for k,v in CADENCES.items())+'</select><label>期間</label><select name="period_mode"><option value="current">当期</option><option value="previous">直前の完了期間</option></select>',{'key':'report-'+uuid4().hex},'レポートを生成')
        return body+run_list(office)
    run=office.db.execute('SELECT * FROM growth_runs WHERE id=?',(identifier,)).fetchone()
    if not run:
        raise ValueError('Unknown report')
    context=json.loads(run['context'])
    body='<p>'+e(CADENCES[run['cadence']])+' / '+e(run['period_key'])+' / '+e(STATE.get(run['status'],run['status']))+' / '+e(STATE.get(run['decision'],run['decision']))+'</p><div class="notice">ローカル計算・固定ルール。モデルAPI未接続。凍結したデータと目標の版を使用し、訂正後の分析は新しいレポートで生成します。</div>'
    if run['error_code']=='operator_paused':
        body+='<div class="notice">代表の停止または設定変更により、後続工程を止めました。</div>'
    elif run['error_code']:
        body+='<p class="error">'+e(run['error_code'])+'。スケジュールは停止し、管理者による原因確認と再開が必要です。</p>'
    body+='<h2>依頼・データの対象</h2><p>'+e((context.get('instruction') or '定期または種別指定のレポート') if session['role']=='representative' else '依頼本文は代表のみ閲覧できます')+'</p><p>'+e(context['period']['start'])+' 〜 '+e(context['period']['coverage_end_exclusive'])+'（終了日は含まない）</p>'
    body+='<h2>業務実績</h2>'+table(['指標','手動実測申告','推定','計測状態','計測日数'],[[e(m['title']),e(m['value'] if m['value'] is not None else '未計測'),e(m['estimated'] if m['estimated'] is not None else '未計測'),e(m['status']),e(str(m['coverage_days'])+'/'+str(m['expected_days']))] for m in context['kpis'].values()])
    body+='<h2>月間目標の達成状況</h2><p>対象月 '+e(context['monthly_goals']['month'])+'。週次/日次の件数を月目標から直接減算しません。</p>'+table(['指標','代表目標','月間実測','目標との差','達成率'],[[e(METRICS[k]),e(v['goal'] if v['goal'] is not None else '未設定'),e(v['actual'] if v['actual'] is not None else '未計測'),e(v['gap'] if v['gap'] is not None else '未計測'),e(str(v['achievement_percent'])+'%' if v['achievement_percent'] is not None else '未計測')] for k,v in context['monthly_progress'].items()])
    body+='<h2>転換・施策評価</h2>'+table(['段階','顧客単位の転換率','参考の期間件数比','注意'],[[e(k),'未計測',e(str(v['period_count_ratio_percent'])+'%' if v['period_count_ratio_percent'] is not None else '未計測'),e(v['note'])] for k,v in context['conversions'].items()])+'<p>'+e(context['attribution'])+'</p>'
    body+='<h2>費用・代表の時間</h2>'+table(['項目','値'],[[e(('確定記帳' if k=='confirmed' else '推定記帳')+' USD'),e(Decimal(v)/1000000 if v is not None else '未計測')] for k,v in context['cost_microusd'].items()]+[[e('記録済み月間代表時間 '+('実測申告' if k=='measured' else '推定')+' 分'),e(v if v is not None else '未計測')] for k,v in context['owner_month_minutes'].items()])
    task_labels={'completed_task_ids':'対象期間に人のレビューを通過した完了タスク','running':'現在の作業中タスク','approval_pending':'計測時の代表承認待ち','errors':'対象期間のタスク失敗','local_report_failures':'対象期間の定期ローカル処理失敗','mock_api_failures':'対象期間のモックAPI失敗','completed_task_count':'対象期間の完了タスク総数','running_task_count':'作業中タスク総数','error_task_count':'対象期間の失敗タスク総数','reference_ids_limit':'表示する参照IDの上限'}
    body+='<h2>業務の状態</h2>'+table(['対象','記録'],[[e(task_labels.get(k,k)),e(', '.join(v) if v else '0件') if isinstance(v,list) else e(v)] for k,v in context['local_tasks'].items()])
    if run['report']:
        report=json.loads(run['report'])
        body+='<h2>改善仮説・次期の優先業務</h2><p>'+e(report['plan']['next_priority'])+'</p><p>'+e(report['plan']['experiment'])+'</p>'+table(['担当','優先度','提案','根拠'],[[e(i['agent']),e(i['priority']),e(i['work']),e(i['reason'])] for i in report['plan']['items']])
        if report['draft_content_id']:
            body+='<p>原稿下書き：'+e(report['draft_content_id'])+'（別途、法令・規約確認と原稿承認が必要） <a href="/approvals">承認センター</a></p>'
        if run['decision']=='approval_pending':
            body+='<div class="actions">'+''.join(form(session,'growth_decide',data={'id':identifier,'decision':v},caption=t,style='inline') for v,t in [('approve','計画を承認（外部実行なし）'),('changes_requested','修正依頼'),('reject','却下')])+'</div>'
    work=office.db.execute('SELECT * FROM growth_work WHERE run_id=? ORDER BY rowid',(identifier,)).fetchall()
    body+='<h2>実際のローカル処理・成果物</h2>'+table(['担当','工程','結果','タスク/成果物参照'],[[e(r['agent']),e({'analysis':'実績分析','plan':'業務計画','content_plan':'投稿企画','copy':'文章案','quality':'機械的品質確認','report':'統括報告','referral':'紹介文案','booking_proposal':'面談改善案'}.get(r['step'],r['step'])),e(STATE.get(r['state'],r['state'])),e(r['task_id']+' / '+str(r['artifact_id']))] for r in work])
    proposals=office.db.execute('SELECT p.*,t.agent FROM growth_proposals p JOIN tasks t ON t.id=p.task_id WHERE p.run_id=?',(identifier,)).fetchall()
    body+='<h2>割り当てた提案タスク</h2>'+table(['担当','優先度','期限','承認状態'],[[f'<a href="/employees?id={r["agent"]}">'+e(r['agent'])+'</a>',e(r['priority']),dt(r['due_at']),e(STATE.get(r['status'],r['status']))] for r in proposals])
    decisions=office.db.execute('SELECT actor,decision,created_at FROM growth_decisions WHERE run_id=? ORDER BY id',(identifier,)).fetchall()
    body+='<h2>代表判断の履歴</h2>'+table(['担当','判断','日時'],[[e(r[0]),e(STATE.get(r[1],r[1])),dt(r[2])] for r in decisions])
    return body
