"""Japanese seminar calendar, private operations and participant self-service."""
import calendar,html,json
from datetime import datetime,date,timedelta
from uuid import uuid4
from .seminars import JST,ANSWERS
from .store import now
from .integrations.adapters import Principal
from .portfolio_views import pf,inp,opts
from .sales_views import scope
from .phase9_views import member_form
from .web_views import e,dt,table,card,fields
from .seminar_support import LABELS,INTERESTS,KINDS,ACTIVITIES

WORK_LABELS={'planning':'企画・統括','copy':'案内原稿','reception':'受付手順','reminder':'日程案内準備','followup':'参加後・面談導線','analysis':'成果分析','audit':'品質確認','integration':'未接続APIの条件確認','research':'調査資料の整理','creative':'画像・動画の制作指示','sales':'紹介・説明の導線'}
TASK_STATUS={'pending':'未着手','running':'ローカル処理中','review_pending':'代表確認待ち','manual_execution_pending':'内部成果物承認済み・外部未実行','rejected':'却下','failed':'失敗','paused':'停止中','approval_pending':'承認待ち'}
STATUS={'approval_pending':'代表承認待ち','send_waiting_no_api':'承認済み・送信待ち（API未接続）','retry_wait':'模擬再試行待ち','mock_sent':'モック成功・実送信なし','failed':'模擬失敗','manual_reconciliation':'結果不明・再試行停止','rejected':'却下','stale':'条件変更・無効'}

def p(s): return Principal(s['role'],s['username'])
def local(value): return datetime.fromisoformat(value).astimezone(JST).strftime('%Y-%m-%dT%H:%M') if value else ''
def checkboxes(prefix,labels,selected=()):
    return ''.join('<label><input type="checkbox" name="'+prefix+k+'" value="1"'+(' checked' if k in selected else '')+'>'+e(v)+'</label>' for k,v in labels.items())
def seminar_fields(row=None,meta=None):
    row=dict(row or {}); meta=dict(meta or {})
    return inp('title','セミナー名',row.get('title',''))+inp('start_at','開始（JST）',local(row.get('start_at')),'datetime-local')+inp('end_at','終了（JST）',local(row.get('end_at')),'datetime-local')+inp('location','会場・オンライン開催情報',row.get('location',''))+inp('online','オンラインURL（HTTPS、秘密値・クエリなし）',meta.get('online_url',''),required=False)+inp('fee_yen','参加費円（明示設定）',row.get('fee_yen',''),'number')+inp('capacity','定員（任意）',row.get('capacity') or '','number',False)+inp('deadline','申込期限（JST）',local(row.get('deadline')),'datetime-local')+'<label>内容（確認済み情報のみ）</label><textarea name="description" required maxlength="4000">'+e(row.get('description',''))+'</textarea>'+checkboxes('target_',LABELS,json.loads(meta.get('segments','[]')))+opts('mode',{'mock':'合成モック・実績除外','manual':'代表による確認記録用（外部未照合）'},meta.get('mode','mock'))+inp('lead_hours','締切前案内：何時間前（仮設定24、代表変更可）',meta.get('lead_hours',24),'number')+inp('clock','前日・当日案内のJST時刻（仮設定09:00、代表変更可）',meta.get('clock','09:00'),'time')

def office(o,s,params):
    b,s=scope(o,s,params); actor=p(s); service=o.seminar_support; owner=s['role']=='representative'
    if not b:
        body='<p>許可された事業を選びます。既存顧客/参加者を自動で他事業へ関連付けません。</p>'
        for row in o.db.execute('SELECT id,name FROM portfolio_businesses ORDER BY id'):
            try: o.portfolio.access(actor,row['id'])
            except PermissionError: continue
            body+='<p><a href="/seminar-office?business='+e(row['id'])+'">'+e(row['name'])+'</a></p>'
        return body
    hidden={'business':b}; body='<div class="notice">セミナー管理・参加促進・面談導線。固定テンプレート・ルールのみ、LLM/LINE/予約/決済APIは未接続。区分は代表の初回手動確認で、公式の契約照合済みを意味しません。</div>'
    if owner:
        body+='<details class="panel"><summary>セミナーを手動登録</summary>'+pf(s,'s14_save',seminar_fields(),hidden,'この事業に登録（配信しない）')+'</details>'
        body+='<details><summary>既存セミナーを明示的に拡張へ関連付け</summary><p>先に既存「複数事業」でセミナーと事業を関連付けてください。目的別同意は新たに必要です。</p>'+pf(s,'s14_adopt',inp('seminar','既存セミナーID')+opts('mode',{'mock':'合成モック','manual':'代表の確認記録用'}),hidden,'明示的に採用（既存データを自動移行しない）')+'</details>'
        scheduler=o.db.execute('SELECT enabled FROM s14_scheduler WHERE business_id=?',(b,)).fetchone(); enabled=bool(scheduler and scheduler[0])
        body+='<details><summary>締切前・前日・当日案内の下書き予定</summary><p>初期停止。Web起動だけでは準備/送信しません。3日前は既存処理を維持。締切前と前日等が同日なら当日→前日→締切前の順に1種を準備する仮設定です。</p>'+pf(s,'s14_schedule',data={**hidden,'enabled':'0' if enabled else '1'},caption='下書き予定を停止' if enabled else '下書き予定を有効化（送信しない）')+pf(s,'s14_tick',data=hidden,caption='本日分の下書きを確認・準備（上限200、送信なし）')+'</details>'
    month=params.get('month',[now().astimezone(JST).strftime('%Y-%m')])[0]
    parsed=date.fromisoformat(month+'-01'); body+='<form method="get">'+fields(hidden)+inp('month','開催月（JST）',month,'month')+'<button>一覧・カレンダーを表示</button></form>'
    metas=o.db.execute('SELECT * FROM s14_meta WHERE business_id=?',(b,)).fetchall(); seminars=[(m,o.seminars.get(m['seminar_id'])) for m in metas]
    body+='<h2>開催カレンダー</h2><p>月 '+e(month)+' / 受付状況と区分は詳細をご確認ください。</p>'
    rows=[]
    for week in calendar.Calendar().monthdatescalendar(parsed.year,parsed.month):
        cells=[]
        for day in week:
            cell=e(day.day)
            for meta,core in seminars:
                if datetime.fromisoformat(core['start_at']).astimezone(JST).date()==day: cell+='<p><a href="#seminar-'+e(core['id'])+'">'+e(core['title'])+'</a></p>'
            cells.append(cell)
        rows.append(cells)
    body+=table(['月','火','水','木','金','土','日'],rows)
    for meta,core in sorted(seminars,key=lambda r:r[1]['start_at']):
        if datetime.fromisoformat(core['start_at']).astimezone(JST).strftime('%Y-%m')!=month: continue
        sid=core['id']; h={**hidden,'seminar':sid}; metrics=service.metrics(actor,b,sid)
        body+='<article class="panel" id="seminar-'+e(sid)+'"><h2>'+e(core['title'])+'</h2><p>'+dt(core['start_at'])+' 〜 '+dt(core['end_at'])+' / '+e({'open':'受付中','closed':'受付終了','cancelled':'中止'}[core['status']])+' / '+e('合成モック' if meta['mode']=='mock' else '代表の実績確認記録・外部未照合')+'</p><p>ID '+e(sid)+' / '+e(core['location'])+'</p><p>オンライン '+e(meta['online_url'] or '未登録')+' / 参加費 '+e(core['fee_yen'])+'円 / 定員 '+e(core['capacity'] or '未設定')+' / 締切 '+dt(core['deadline'])+'</p><p>対象区分 '+e('・'.join(LABELS[k] for k in json.loads(meta['segments'])))+'</p><pre>'+e(core['description'])+'</pre>'
        values=metrics['actual'] if meta['mode']=='manual' else metrics['mock_preview']
        body+='<details><summary>参加・面談・収支の分析</summary><p>'+e('実績の確認記録' if meta['mode']=='manual' else '合成モックの参考集計・実績ではない')+'</p>'+table(['指標','値'],[[label,e(values[key] if values[key] is not None else '未計測')] for key,label in [('applicants','累計申込者'),('planned','参加予定'),('cancelled','キャンセル'),('attended','実参加確認'),('absent','欠席確認'),('interview_requested','面談希望'),('interview_completed','面談完了'),('attendance_percent','参加率%'),('cancellation_percent','キャンセル率%'),('interview_percent','面談化率%')]])+'<p>'+e(metrics['denominators'])+'</p><p>'+e(metrics['finance_definition'])+'</p><pre>'+e(metrics)+'</pre></details>'
        if owner:
            if core['status']=='open': body+='<details><summary>登録情報を編集</summary>'+pf(s,'s14_save',seminar_fields(core,meta),{**h,'revision':meta['revision']},'版を確認して編集・旧案内を無効化')+'</details>'
            body+=pf(s,'s14_state',opts('state',{'closed':'受付終了','cancelled':'中止'}),h,'代表が状態を変更（送信しない）')
            body+='<details><summary>参加者・申込状況・確認済み結果（代表専用）</summary>'
            recipients=o.seminars.recipients(actor,sid)
            body+=table(['本人確認済みアカウント','氏名','区分','回答'],[[e(r['username']),e(r['display_name']),e(LABELS[r['segment']]),e(ANSWERS[r['answer']])] for r in recipients])
            body+=pf(s,'s14_engagement',inp('username','確認対象アカウント')+opts('state',{'attended':'実参加確認','absent':'欠席確認','interview_requested':'本人の面談希望確認','interview_completed':'面談完了確認'})+inp('evidence','観測済み事実の根拠参照（秘密・相談本文不可）')+inp('when','面談完了時刻（完了時は必須）',kind='datetime-local',required=False),h,'代表が確認済みの結果を記録（予約しない）')+'</details>'
            body+='<details><summary>個別案内・一斉案内・参加後フォロー</summary><p>本人の目的別同意と配信停止を検証します。一斉は各人ごとの下書き・個別承認で、未対象者はスキップします。</p>'+pf(s,'s14_prepare',inp('username','本人確認済みアカウント')+opts('kind',KINDS),h,'個別下書きを準備')+pf(s,'s14_bulk',opts('kind',KINDS),h,'対象者ごとの一斉案内下書きだけ準備')+'</details>'
            body+='<details><summary>確認済み財務の明示関連付け</summary><p>売上を新規作成せず、同じ事業の既存請求/経費のみ参照。1根拠の複数セミナー配賦は禁止。</p>'+pf(s,'s14_attribute',opts('kind',{'invoice':'実請求記録','expense':'実経費記録'})+inp('source','既存記録ID')+inp('evidence','関連の確認根拠参照'),h,'代表が明示配賦（元台帳は変更しない）')
            for attribution in o.db.execute('SELECT * FROM s14_attributions WHERE business_id=? AND seminar_id=? AND active=1',(b,sid)):
                body+='<p>'+e(attribution['kind']+' / '+attribution['source_ref'])+'</p>'+pf(s,'s14_unattribute','<label><input name="checked" type="checkbox" value="1" required>代表が誤関連を確認（元台帳は維持）</label>',{**hidden,'id':attribution['id']},'配賦だけ解除・履歴を保持')
            body+='</details>'
        body+='<details><summary>既存AI社員への内部タスク</summary><p>必要な専門役だけ。成果物は固定テンプレートで、監査は人の最終判断が必要です。</p>'+pf(s,'s14_work',opts('activity',{k:WORK_LABELS[k]+' / '+','.join(v) for k,v in ACTIVITIES.items()})+inp('key','作業キー',uuid4().hex)+inp('agent','担当AI ID（空欄は同専門から）',required=False),h,'内部タスク割当')
        for task in o.db.execute('SELECT w.*,t.agent,t.status FROM s14_work w JOIN tasks t ON t.id=w.task_id WHERE w.business_id=? AND w.seminar_id=?',(b,sid)):
            body+='<details><summary>'+e(task['agent'])+' / '+e('停止中' if task['paused'] else TASK_STATUS.get(task['status'],task['status']))+'</summary><pre>'+e(task['result'] or '未作成')+'</pre>'
            if task['status']=='pending' and not task['paused']: body+=pf(s,'s14_run',data={**hidden,'task':task['task_id']},caption='固定テンプレートを作成')
            if owner:
                body+=pf(s,'s14_control',opts('control',{'pause':'停止','resume':'再開','approve':'代表が内部成果物を承認','reject':'内部成果物を却下'}),{**hidden,'task':task['task_id']},'代表の指示を記録（外部実行なし）')
            body+='</details>'
        body+='</details></article>'
    if owner:
        queue={r['id'] for r in service.queue(actor,b)}
        body+='<h2>原稿・予約予定・承認待ち・模擬履歴</h2>'
        for row in o.db.execute('SELECT * FROM s14_drafts WHERE business_id=? ORDER BY created_at DESC LIMIT 200',(b,)):
            body+='<details class="panel"><summary>'+e(KINDS[row['kind']])+' / '+e(STATUS[row['status']])+'</summary><p>対象 '+e(row['username'])+' / 予定 '+dt(row['due_at'])+' / 承認者 '+e(row['approved_by'] or '未承認')+' / 条件 '+e('有効' if row['id'] in queue else '未承認または再確認')+'</p><pre>'+e(row['body'])+'</pre>'
            if row['status']=='approval_pending':
                for decision,label in [('approve','この原稿を承認（送信しない）'),('reject','却下')]: body+=pf(s,'s14_decide','<label><input type="checkbox" name="checked" value="1" required>区分・同意・停止・日時・内容を代表が確認</label>',{**hidden,'id':row['id'],'decision':decision},label)
            if row['status'] in ('approval_pending','send_waiting_no_api') and not row['attempts']: body+=pf(s,'s14_revise','<label>修正原稿（再承認が必要）</label><textarea name="body" required maxlength="7000">'+e(row['body'])+'</textarea>',{**hidden,'id':row['id']},'旧版を残して修正・承認を解除')
            if row['status'] in ('send_waiting_no_api','retry_wait'): body+=pf(s,'s14_mock',opts('outcome',{'success':'模擬成功','transient':'模擬一時失敗','timeout':'模擬結果不明','permanent':'模擬恒久失敗'}),{**hidden,'id':row['id']},'期限・条件を確認してモック実行（実送信なし）')
            body+='<pre>'+e([dict(r) for r in o.db.execute('SELECT outcome,mode,created_at FROM s14_attempts WHERE draft_id=?',(row['id'],))])+'</pre></details>'
        body+='<details><summary>監査・操作履歴</summary><pre>'+e([dict(r) for r in o.db.execute('SELECT event,ref,detail,created_at FROM s14_events WHERE business_id=? ORDER BY id DESC LIMIT 100',(b,))])+'</pre></details>'
    return body


def participant(o,s,params):
    actor=p(s)
    if s['role']!='participant': raise PermissionError('Participant portal required')
    sem=o.seminars; service=o.seminar_support; sem.member(s['username'],True)
    sid=params.get('id',[None])[0]
    listing=[dict(sem.visible(s['username'],sid))] if sid else sem.listing(s['username'])
    body='<div class="notice">申込・区分の確認は既存の初回登録を利用します。権限や実契約は公式照合未接続。参加は任意で、送信はモックのみです。</div><a href="/seminars">既存の初回登録・区分・全体の配信停止へ</a>'
    for core in listing:
        sid=core['id']; meta=o.db.execute('SELECT * FROM s14_meta WHERE seminar_id=?',(sid,)).fetchone()
        if not meta: continue
        service.member(actor,sid); h={'id':sid}
        response=o.db.execute('SELECT answer FROM seminar_responses WHERE seminar_id=? AND username=?',(sid,s['username'])).fetchone()
        body+='<article class="panel"><h2>'+e(core['title'])+'</h2><p>'+dt(core['start_at'])+' 〜 '+dt(core['end_at'])+'</p><p>'+e(core['location'])+' / '+e(meta['online_url'] or 'オンラインURL未登録')+'</p><pre>'+e(core['description'])+'</pre><p>参加費 '+e(core['fee_yen'])+'円 / 締切 '+dt(core['deadline'])+' / 回答 '+e(ANSWERS[response[0]] if response else '未回答')+'</p>'
        if core['status']=='open' and core['deadline']>now().isoformat(): body+=member_form(s,'s14_member_response',opts('answer',{'yes':'申し込む・参加予定','maybe':'検討中','no':'参加しない'}),h,'本人の参加希望を保存')
        if response and response[0]!='no': body+=member_form(s,'s14_member_response',data={**h,'answer':'cancel'},caption='申込・参加予定をキャンセル（締切後も可）')
        body+='<details><summary>自分の配信目的・停止・関心</summary><p>全体の再案内同意に加え、下記の目的別同意が必要です。実配信ではなくローカルの模擬同意です。</p>'
        for purpose,label in [('invite','案内'),('reminders','日程リマインド'),('followup','参加後フォロー')]:
            consent=o.db.execute('SELECT granted FROM s14_consents WHERE seminar_id=? AND username=? AND purpose=? ORDER BY id DESC LIMIT 1',(sid,s['username'],purpose)).fetchone()
            body+='<p>'+label+'：'+e('同意あり' if consent and consent[0] else '未同意・停止')+'</p>'+member_form(s,'s14_member_consent',opts('granted',{'0':'同意しない・停止','1':'このセミナーの目的に同意'}),{**h,'purpose':purpose},label+'の同意を保存')
        pref=o.db.execute('SELECT interests FROM s14_preferences WHERE seminar_id=? AND username=?',(sid,s['username'])).fetchone()
        body+=member_form(s,'s14_member_preferences',checkboxes('interest_',INTERESTS,json.loads(pref[0]) if pref else []),h,'希望する関心だけ保存（健康の詳細は入力しない）')+'</details>'
        if s['username'] in service.applicants(sid) and core['status']!='cancelled': body+=member_form(s,'s14_member_interview',data=h,caption='個別面談を希望する（予約確定ではない）')
        body+='</article>'
    return body
