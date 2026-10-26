"""Escaped phase-9 UI; participant portal never renders other participant data."""
import html
import json
from datetime import timedelta,datetime
from uuid import uuid4
from .store import now
from .office import JST
from .web_views import e,dt,form,fields,table
from .seminars import SEGMENTS,AUDIENCES,ANSWERS,BUSINESS_COPY
from .selection import CAPABILITIES

STATE_LABELS={'approval_pending':'代表承認待ち','approved':'承認済み','revoked':'撤回済み','rejected':'却下','open':'受付中','closed':'受付終了','cancelled':'中止・取消','retry_wait':'再試行待ち','mock_sent':'モック完了・実送信なし','failed':'失敗','proposed':'提案中','assigned':'担当確定・未実行','prepared_only':'下書き準備のみ'}
CAP_LABELS={'planning':'業務計画','research':'調査','sns':'SNS原稿','creative':'画像・動画指示','schedule':'投稿計画','referral':'紹介原稿','referral_design':'紹介導線設計','booking':'面談案内','line':'LINE原稿','analysis':'実績分析','management':'部門管理','integration':'連携設計','audit':'品質監査'}
from .integrations.adapters import Principal,require


def options(name,values,selected=''):
    return '<select name="'+name+'">'+''.join('<option value="'+e(k)+'"'+(' selected' if k==selected else '')+'>'+e(v)+'</option>' for k,v in values.items())+'</select>'


def member_form(session,action,body='',data=None,caption='保存'):
    return '<form method="post" action="/actions/'+action+'">'+fields({'csrf':session['csrf'],'nonce':uuid4().hex,**(data or {})})+body+'<button>'+caption+'</button></form>'


def principal(session): return Principal(session['role'],session['username'])


def selection(office,session):
    require(principal(session),True)
    enabled=office.selection.enabled()
    body='<div class="notice">測定できない実績は評価不能です。選抜はローカル担当案だけで、自律実行・外部送信を開始しません。役割や権限は変更しません。</div>'
    body+='<p>自動選抜：'+('有効' if enabled else '停止中（既存主担当を提案）')+'</p>'+form(session,'selection_toggle',data={'enabled':'0' if enabled else '1'},caption='自動選抜を停止' if enabled else '自動選抜を有効化')
    body+=form(session,'selection_propose','<label>業務</label>'+options('kind',{k:CAP_LABELS[k]+' / '+','.join(v) for k,v in CAPABILITIES.items()})+'<label>開始日（JST）</label><input type="date" name="start" required value="'+now().astimezone(JST).date().replace(day=1).isoformat()+'"><label>終了日（含まない）</label><input type="date" name="end" required value="'+(now().astimezone(JST).date()+timedelta(days=1)).isoformat()+'">',{'key':'selection-'+uuid4().hex},'選抜案を作成')
    body+='<p><a href="/learning">28人の業務・完了率・品質・エラー・料金の評価一覧</a>。選抜は同業務候補の品質評点、レビュー、失敗率を比較します。承認率だけで判断しません。</p>'
    for row in office.db.execute('SELECT * FROM selection_plans ORDER BY created_at DESC LIMIT 50'):
        data=json.loads(row['selection'])
        body+='<details class="panel"><summary>'+e(CAP_LABELS[row['kind']])+' / 提案 '+e(row['chosen'])+' / '+e(STATE_LABELS.get(row['status'],row['status']))+'</summary><p>'+e(row['reason'])+'</p><p>対象期間：'+e(data['period'])+'</p>'
        body+=table(['担当','処理対象件数','品質評点','レビュー通過%','失敗試行%'],[[e(a['agent']+' '+a['role']),e(a['metrics']['sample_size']),e(a['metrics']['quality_score'] if a['metrics']['quality_score'] is not None else '評価不能'),e(a['metrics']['quality_pass_percent'] if a['metrics']['quality_pass_percent'] is not None else '評価不能'),e(a['metrics']['error_percent'] if a['metrics']['error_percent'] is not None else '評価不能')] for a in data['candidates']])
        body+=form(session,'selection_assign','<label>代表が担当を確定・変更</label>'+options('agent',{a:office.store.agents[a]['role'] for a in CAPABILITIES[row['kind']]},row['chosen']),{'id':row['id']},'ローカル担当タスクを登録')
        if row['task_id']: body+='<p>タスク：'+e(row['task_id'])+'（未着手のみ担当変更可。旧タスクは停止し履歴保持）</p>'
        changes=[dict(r) for r in office.db.execute('SELECT event,detail,created_at FROM selection_events WHERE ref=? ORDER BY id',(row['id'],))]
        body+='<details><summary>担当変更履歴</summary><pre>'+e(changes)+'</pre></details></details>'
    return body


def seminar_fields(row=None):
    row=dict(row) if row else {}
    body=''
    for key,label,kind in [('title','セミナー名','text'),('start_at','開催日・開始時刻（JST）','datetime-local'),('end_at','終了日時（JST）','datetime-local'),('location','会場またはオンライン開催情報','text'),('fee_yen','参加費（円、無料は0）','number'),('deadline','申込期限（JST）','datetime-local')]:
        value=str(row.get(key,''))
        if key in ('start_at','end_at','deadline') and value: value=datetime.fromisoformat(value).astimezone(JST).strftime('%Y-%m-%dT%H:%M')
        body+='<label>'+label+'</label><input name="'+key+'" type="'+kind+'" value="'+e(value)+'" required maxlength="1000"'+(' min="0"' if kind=='number' else '')+'>'
    body+='<label>セミナー内容（公開可能な情報のみ）</label><textarea name="description" required maxlength="4000">'+e(row.get('description',''))+'</textarea><label>対象区分</label>'+options('audience',AUDIENCES,row.get('audience','public'))+'<label>定員（任意）</label><input name="capacity" type="number" min="1" value="'+e(row.get('capacity') or '')+'">'
    return body


def admin(office,session,params):
    p=principal(session); require(p,True)
    sem=office.seminars
    get=lambda k:params.get(k,[''])[0]
    body='<div class="notice">代表専用・合成データでのローカル検証用です。実LINEは未接続。本番配信はできません。氏名はこの画面だけに表示し、AI成果物には渡しません。</div>'
    body+='<h2>本人確認済みアカウントの発行</h2>'+form(session,'seminar_account','<label>ユーザー名（仮名ID推奨）</label><input name="username" required maxlength="40"><label>氏名（代表のみ閲覧）</label><input name="display_name" required maxlength="100"><label>初回パスワード（12文字以上、安全な別経路で本人に伝達）</label><input type="password" name="password" autocomplete="new-password" required minlength="12" maxlength="256"><label>本人確認記録の参照ID（証明書や秘密値は入力しない）</label><input name="evidence" required maxlength="120"><label><input name="attested" type="checkbox" value="1" required>代表が本人確認を実施済み</label>',caption='参加者専用アカウントを作成')
    body+='<h2>初回登録の手動承認</h2>'
    registrations=[]
    for r in office.db.execute('SELECT r.*,a.display_name FROM seminar_registrations r JOIN seminar_accounts a USING(username) ORDER BY r.created_at DESC LIMIT 200'):
        actions=''.join(form(session,'seminar_registration',data={'username':r['username'],'decision':d},caption=t,style='inline') for d,t in [('approve','確認して承認'),('reject','却下'),('revoke','承認撤回')])
        registrations.append([html.escape(r['display_name']),e(SEGMENTS[r['segment']]),e(r['card_suffix'] or '不要'),e(STATE_LABELS.get(r['status'],r['status'])),actions])
    body+=table(['氏名','区分','カード末尾（全文非保存）','初回登録状態','操作'],registrations)
    body+='<h2>セミナー登録</h2>'+form(session,'seminar_save',seminar_fields(),caption='セミナーを登録')
    scheduler=office.db.execute('SELECT * FROM seminar_scheduler WHERE id=1').fetchone()
    enabled=scheduler['enabled']
    if scheduler['state']=='error_hold': body+='<div class="notice">準備処理エラーにより自動停止しています。履歴を確認してから再開してください。</div>'
    body+='<h2>開催3日前の再案内</h2><p>実行状態：'+('準備処理が有効' if enabled else '停止中')+'。日付はJST。処理は承認待ちの下書き作成だけです。</p>'
    body+=form(session,'seminar_schedule',data={'enabled':'0' if enabled else '1'},caption='停止' if enabled else '準備処理を有効化')+form(session,'seminar_tick',caption='3日前の処理を1回実行')
    body+='<form method="get"><label>開催日（JST）</label><input type="date" name="day" value="'+e(get('day'))+'"><label>区分</label>'+options('segment',{'':'すべて',**SEGMENTS},get('segment'))+'<label>回答状況</label>'+options('answer',{'':'すべて',**ANSWERS},get('answer'))+'<button>絞り込み</button></form>'
    for r in office.db.execute('SELECT * FROM seminars ORDER BY start_at LIMIT 200'):
        if get('day') and datetime.fromisoformat(r['start_at']).astimezone(JST).date().isoformat()!=get('day'): continue
        recipients=sem.recipients(p,r['id'],get('segment'),get('answer'))
        all_recipients=sem.recipients(p,r['id'])
        counts={label:sum(a['answer']==key for a in all_recipients) for key,label in ANSWERS.items()}
        body+='<details class="panel"><summary>'+e(r['title'])+' / '+dt(r['start_at'])+' / '+e(STATE_LABELS.get(r['status'],r['status']))+'</summary><p>'+e(AUDIENCES[r['audience']])+' / v'+str(r['revision'])+'</p><p>対象者全体：'+e(counts)+'。以下の名簿は絞り込み後。</p>'
        if r['status']=='open':
            body+=form(session,'seminar_save',seminar_fields(r),{'id':r['id'],'revision':r['revision']},'編集を保存')
            body+=''.join(form(session,'seminar_state',data={'id':r['id'],'status':s},caption=t,style='inline') for s,t in [('closed','受付終了'),('cancelled','中止')])
        elif r['status']=='closed': body+=form(session,'seminar_state',data={'id':r['id'],'status':'cancelled'},caption='中止')
        body+=form(session,'seminar_work',data={'id':r['id']},caption='必要なAI担当のローカル原稿・集計を作成')
        rows=[]
        for member in recipients:
            history=[dict(h) for h in office.db.execute("SELECT detail,created_at FROM seminar_events WHERE actor=? AND ref=? AND event='response_changed' ORDER BY id",(member['username'],r['id']))]
            reminder=office.db.execute('SELECT * FROM seminar_reminders WHERE seminar_id=? AND username=?',(r['id'],member['username'])).fetchone()
            rows.append([html.escape(member['display_name']),e(SEGMENTS[member['segment']]),e(ANSWERS[member['answer']]),dt(member['answered_at']),'<details><summary>変更履歴</summary><pre>'+e(history)+'</pre></details>',e(STATE_LABELS.get(reminder['status'],reminder['status']) if reminder else '未準備')])
        body+=table(['氏名','区分','回答','回答日時','履歴','再案内'],rows)
        for reminder in office.db.execute('SELECT * FROM seminar_reminders WHERE seminar_id=? ORDER BY rowid',(r['id'],)):
            body+='<details><summary>再案内 '+e(reminder['id'])+' / '+e(STATE_LABELS.get(reminder['status'],reminder['status']))+'</summary><pre>'+e(reminder['body'])+'</pre>'
            if reminder['status']=='approval_pending':
                body+=''.join(form(session,'seminar_approve',data={'id':reminder['id'],'decision':d},caption=t,style='inline') for d,t in [('approve','この内容のモック実行を承認'),('reject','却下')])
            elif reminder['status'] in ('approved','retry_wait'):
                body+=form(session,'seminar_mock',data={'id':reminder['id']},caption='承認済み内容をモック実行（実送信なし）')
            elif reminder['status'] in ('cancelled','rejected') and not reminder['attempts']:
                body+=form(session,'seminar_reprepare',data={'id':reminder['id']},caption='条件を再確認して下書きを再準備（再承認が必要）')
            attempts=[dict(a) for a in office.db.execute('SELECT outcome,code,mode,created_at FROM seminar_attempts WHERE reminder_id=?',(reminder['id'],))]
            body+='<pre>'+e(attempts)+'</pre></details>'
        body+='</details>'
    body+='<h2>処理履歴</h2>'+table(['日時','状態','準備件数'],[[dt(r['created_at']),e(STATE_LABELS.get(r['status'],r['status'])),e(r['prepared'])] for r in office.db.execute('SELECT * FROM seminar_ticks ORDER BY created_at DESC LIMIT 20')])
    body+='<h2>監査履歴</h2>'+table(['日時','操作','参照ID'],[[dt(r['created_at']),e(r['event']),e(r['ref'])] for r in office.db.execute('SELECT * FROM seminar_events ORDER BY id DESC LIMIT 50')])
    return body


def portal(office,session,identifier=None):
    if session['role']!='participant': raise PermissionError('Participant portal only')
    sem=office.seminars; username=session['username']; reg=sem.member(username)
    body='<pre>'+e(BUSINESS_COPY)+'</pre><p>登録情報や参加回答は代表が管理します。他の参加者の氏名・回答は表示しません。</p>'
    if not reg:
        return body+member_form(session,'seminar_member_register','<label>区分</label>'+options('segment',SEGMENTS,'cast')+'<label>カード記載番号（キャストは空欄）</label><input name="card" maxlength="80" autocomplete="off"><p>カード記載番号だけでは本人確認になりません。初回登録は代表が手動確認します。</p>',caption='初回登録')
    body+='<p>区分：'+e(SEGMENTS[reg['segment']])+' / 初回登録：'+e(STATE_LABELS.get(reg['status'],reg['status']))+'</p>'
    consent=sem.latest_consent(username)
    body+='<p>セミナー再案内の同意：'+('同意あり' if consent and consent['granted'] else '同意なし・停止中')+'</p>'
    body+=member_form(session,'seminar_member_consent',data={'granted':'1'},caption='セミナー再案内に同意する（モックのみ）')+member_form(session,'seminar_member_consent',data={'granted':'0'},caption='セミナー再案内を停止する')
    if reg['status']!='approved': return body+'<p>代表による初回確認をお待ちください。</p>'
    listing=[dict(sem.visible(username,identifier))] if identifier else sem.listing(username)
    for r in listing:
        old=office.db.execute('SELECT answer,answered_at FROM seminar_responses WHERE seminar_id=? AND username=?',(r['id'],username)).fetchone()
        body+='<article class="panel"><h2>'+e(r['title'])+'</h2><p>'+dt(r['start_at'])+' 〜 '+dt(r['end_at'])+'</p><p>'+e(r['location'])+'</p><pre>'+e(r['description'])+'</pre><p>参加費 '+str(r['fee_yen'])+'円 / 申込期限 '+dt(r['deadline'])+'</p><p>回答：'+e(ANSWERS[old['answer']] if old else '未回答')+' / '+dt(old['answered_at'] if old else None)+'</p>'
        if r['status']=='open' and r['deadline']>=now().isoformat():
            body+=member_form(session,'seminar_member_response',options('answer',{k:v for k,v in ANSWERS.items() if k!='unanswered'},old['answer'] if old else 'maybe'),{'id':r['id']},'参加可否を保存・変更')
        else: body+='<p>'+e('中止' if r['status']=='cancelled' else '受付終了')+'</p>'
        body+='</article>'
    return body
