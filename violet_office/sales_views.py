"""Japanese sales desk, same scoped ACL for manual and AI-assisted workflows."""
import html,json
from uuid import uuid4
from datetime import timedelta
from .store import now
from .seminars import JST
from .integrations.adapters import Principal
from .portfolio_views import pf,inp,opts
from .web_views import e,dt,fields,table,card
from .sales import STAGES,STATES,ACTIONS,ACTIVITIES
from .sales_finance import TAX

STATUS={'approval_pending':'代表承認待ち','approved':'承認済み・未実行','stale':'条件変更・再承認が必要','rejected':'却下','superseded':'旧版','recorded_manual':'手動確認申告・外部未照合','mock_succeeded':'モック成功・実処理なし','retry_wait':'再試行待ち','manual_reconciliation':'結果不明・手動確認待ち','failed':'失敗'}
WORK={'classify':'問い合わせ分類案','reply':'返信案','proposal':'提案書案','estimate':'見積準備案','booking':'予約候補整理','followup':'フォロー案','unhandled':'未対応確認','analysis':'集計案','improvement':'改善提案','quality':'品質確認案'}

def p(s): return Principal(s['role'],s['username'])
def scope(o,s,params):
    bid=params.get('business',[''])[0]
    if bid:
        o.sales.access(p(s),bid)
        if s['role']!='representative':
            if not o.db.execute('SELECT can_write FROM portfolio_acl WHERE business_id=? AND username=?',(bid,s['username'])).fetchone()[0]: s={**s,'portfolio_readonly':True}
    elif s['role'] not in ('representative','business_operator'): raise PermissionError('Business scope required')
    return bid,s

def business_links(o,s):
    body='<p>事業を選択してください。事業・顧客を自動統合しません。</p>'
    for b in o.db.execute('SELECT id,name FROM portfolio_businesses ORDER BY id'):
        try: o.sales.access(p(s),b['id'])
        except PermissionError: continue
        body+='<p><a href="/sales?business='+e(b['id'])+'">'+e(b['name'])+'</a> / <a href="/sales-dashboard?business='+e(b['id'])+'">売上・営業指標</a></p>'
    return body

def home(o,s,params):
    b,s=scope(o,s,params)
    if not b: return business_links(o,s)
    owner=s['role']=='representative'; hidden={'business':b}
    body='<div class="notice">事業 '+e(b)+' の営業管理。AI案・内部メモ・モック・代表の手動確認を区別します。メール/LINE/銀行/契約APIは未接続です。</div><p><a href="/sales-dashboard?business='+e(b)+'">売上・営業指標</a> / <a href="/business?business='+e(b)+'">既存の事業管理</a></p>'
    contacts=o.db.execute('SELECT * FROM portfolio_contacts WHERE business_id=? ORDER BY created_at DESC LIMIT 200',(b,)).fetchall(); choices={c['id']:c['display_name'] for c in contacts if c['state']=='active'}
    body+='<details class="panel"><summary>顧客登録・一覧・同意・停止</summary>'+pf(s,'scm_customer',inp('name','表示名（必要最小限）')+inp('channel','連絡先または安全な参照（秘密値不可）'),hidden,'この事業に顧客登録')
    for c in contacts:
        state=o.db.execute('SELECT stopped FROM scm_customers WHERE business_id=? AND contact_id=?',(b,c['id'])).fetchone()
        body+='<details><summary>'+html.escape(c['display_name'])+' / '+('連絡停止中' if state and state[0] else '目的別同意を確認')+'</summary><p>顧客ID '+e(c['id'])+'</p><p>'+html.escape(c['channel_ref'])+'</p>'
        body+=pf(s,'scm_stop',data={**hidden,'contact':c['id'],'stopped':'0' if state and state[0] else '1'},caption='停止解除（同意は別）' if state and state[0] else '連絡を停止')
        if owner: body+=pf(s,'scm_customer_update',inp('name','同一本人の表示名訂正',c['display_name'])+inp('channel','連絡先訂正',c['channel_ref'])+'<label><input type="checkbox" name="verified" value="1" required>別人への変更ではなく本人確認済みの訂正。停止・再同意・関連付け再確認が必要</label>',{**hidden,'contact':c['id']},'代表が訂正して連絡を停止')+pf(s,'scm_unlink',data={**hidden,'contact':c['id']},caption='他事業との本人関連付けを解除（各事業の記録は維持）')
        if owner:
            group=o.db.execute('SELECT person_id FROM scm_identity_links WHERE business_id=? AND contact_id=? AND active=1',(b,c['id'])).fetchone()
            if group: body+='<p>代表専用・関連付け先参照：</p><pre>'+e([dict(r) for r in o.db.execute('SELECT business_id,contact_id FROM scm_identity_links WHERE person_id=? AND active=1',(group[0],))])+'</pre>'
        body+='</details>'
    body+=pf(s,'scm_consent',opts('contact',choices)+opts('purpose',{'contact':'営業連絡','schedule':'予約調整','followup':'フォロー','identity_link':'事業間本人照合'})+opts('granted',{'1':'同意あり','0':'撤回'})+inp('notice','本人同意・利用目的確認の参照ID'),hidden,'目的別同意を保存')
    if owner: body+=pf(s,'scm_link',opts('contact',choices)+inp('other_business','本人確認した別事業ID')+inp('other_contact','別事業の顧客ID')+inp('evidence','両事業の利用目的・本人確認記録の参照'),hidden,'代表が関連付け（同意や停止は共有しない）')
    body+='</details>'
    body+='<details class="panel"><summary>問い合わせ・営業案件の登録</summary>'+pf(s,'scm_deal',inp('key','受付キー',uuid4().hex)+opts('contact',choices)+inp('subject','業務件名（個人情報不可）')+'<label>問い合わせ内容（必要最小限。医療歴等は入力しない）</label><textarea name="inquiry" required maxlength="2000"></textarea>'+opts('mode',{'manual':'本人からの問い合わせ・人の記録','mock':'合成モック（実績集計から除外）'})+inp('agent','担当AI ID（任意）',required=False)+inp('human','担当者ID（任意）',required=False)+inp('next_day','次回予定日（任意）',kind='date',required=False),hidden,'案件を登録')+'</details>'
    stage=params.get('stage',[''])[0]; state=params.get('state',[''])[0]
    body+='<form method="get">'+fields(hidden)+opts('stage',{'':'全工程',**STAGES},stage)+opts('state',{'':'全状態',**STATES},state)+'<button>絞り込み</button></form>'
    for d in o.db.execute('SELECT * FROM scm_deals WHERE business_id=? ORDER BY created_at DESC LIMIT 200',(b,)):
        if stage and d['stage']!=stage or state and d['state']!=state: continue
        body+='<article class="panel"><h2><a href="/sales-deal?business='+e(b)+'&amp;id='+e(d['id'])+'">'+e(d['subject'])+'</a></h2><p>'+e(STAGES[d['stage']]+' / '+STATES[d['state']])+' / '+e('モック' if d['mode']=='mock' else '人の記録')+'</p><p>担当 '+e(d['agent'] or '手動対応')+' / '+e(d['human_owner'] or '未割当')+' / 次回 '+e(d['next_day'] or '未設定')+'</p><p>最終対応 '+dt(d['last_response_at'])+'</p></article>'
    if owner:
        tax=o.db.execute('SELECT * FROM scm_tax WHERE business_id=?',(b,)).fetchone(); tax=dict(tax) if tax else {}
        body+='<details class="panel"><summary>事業別料金・税設定</summary>'+pf(s,'scm_tax',opts('tax_type',TAX,tax.get('tax_type','unknown'))+inp('rate','代表が確認した税率%（未確認は空欄）',tax.get('rate',''),required=False)+opts('rounding',{'':'未確認','floor':'行別切捨て','half_up':'行別四捨五入','ceil':'行別切上げ'},tax.get('rounding',''))+'<label><input type="checkbox" name="confirmed" value="1">代表が税区分・税率・端数処理を確認</label>',hidden,'この事業の税設定を保存')
        kind=o.db.execute('SELECT kind FROM portfolio_businesses WHERE id=?',(b,)).fetchone()[0]
        if kind=='cleaning': body+=pf(s,'scm_cleaning_candidate',data=hidden,caption='佐久市内6,000円の未確認候補を追加')
        body+=pf(s,'scm_rate',inp('label','サービス・明細名')+inp('unit','単価円（未確認は空欄）',required=False)+inp('conditions','適用条件')+'<label><input type="checkbox" name="confirmed" value="1">料金の確認済み部分を代表が確認</label>',hidden,'料金を追加')
        for rate in o.db.execute('SELECT * FROM scm_rates WHERE business_id=?',(b,)):
            body+=pf(s,'scm_rate',inp('label','明細名',rate['label'])+inp('unit','単価円',rate['unit_yen'] or '',required=False)+inp('conditions','条件',rate['conditions'])+'<label><input type="checkbox" name="confirmed" value="1">料金確認済み</label>',{**hidden,'id':rate['id'],'revision':rate['revision']},'改訂を保存')
        body+='</details>'
    body+='<details class="panel"><summary>AI役割と手動対応</summary><p>既存28役を変更・増員しません。以下の固定テンプレート以外は未実装として人が対応できます。</p>'+table(['役割','今回の支援'],[[e(a+' '+v['role']),e('・'.join(WORK[k] for k,ids in ACTIVITIES.items() if a in ids) or '既存役職を維持・今回の自動処理なし')] for a,v in o.store.agents.items()])+pf(s,'scm_role_request',inp('work','必要な業務')+inp('agent','候補AI ID（任意、既存定義のみ）',required=False)+inp('reason','提案理由'),hidden,'役割拡張を提案（権限は変えない）')+'</details>'
    body+='<details><summary>役割拡張の提案記録（自動適用なし）</summary><pre>'+e([dict(r) for r in o.db.execute('SELECT work,proposed_agent,reason,status FROM scm_role_requests WHERE business_id=? ORDER BY created_at DESC LIMIT 30',(b,))])+'</pre></details>'
    return body


def detail(o,s,params,identifier):
    b,s=scope(o,s,params); d=o.sales.row(p(s),b,'scm_deals',identifier); hidden={'business':b,'deal':identifier}; owner=s['role']=='representative'
    body='<div class="notice">'+e(d['subject'])+' / '+e(STAGES[d['stage']]+' / '+STATES[d['state']])+'. 実APIによる送信・予約・契約・決済は実行しません。</div><p>最終対応 '+dt(d['last_response_at'])+' / 次回 '+e(d['next_day'] or '未設定')+'</p><pre>'+html.escape(d['inquiry'])+'</pre>'
    body+='<details class="panel"><summary>担当・予定・進捗・再開</summary>'+pf(s,'scm_assign',inp('next_day','次回対応日',d['next_day'] or '',kind='date',required=False)+inp('human','担当者ID',d['human_owner'] or '',required=False)+inp('agent','担当AI ID',d['agent'] or '',required=False),{**hidden,'revision':d['revision']},'担当と予定を更新')
    body+=pf(s,'scm_stage',opts('stage',STAGES),{**hidden,'revision':d['revision']},'次の内部工程へ（重要事実は別承認）')+pf(s,'scm_stage',opts('state',STATES),{**hidden,'revision':d['revision']},'保留・失注・取消・再開を記録')+'</details>'
    body+='<details class="panel"><summary>対応履歴・AI案・人の作業</summary>'+pf(s,'scm_note','<label>内部対応メモ（必要最小限、送信済みとは扱わない）</label><textarea name="body" required maxlength="2000"></textarea>',hidden,'内部メモを保存')+pf(s,'scm_work',inp('key','作業キー',uuid4().hex)+opts('activity',WORK)+inp('agent','担当候補ID（空欄は既存専門分野内から選択）',required=False)+'<label>人が直接作る下書き（空欄はAI役割の固定テンプレート）</label><textarea name="body" maxlength="4000"></textarea>',hidden,'手動下書き保存またはタスク割当')
    for w in o.db.execute('SELECT w.*,t.agent,t.status FROM scm_work w JOIN tasks t ON t.id=w.task_id WHERE w.business_id=? AND w.deal_id=?',(b,identifier)):
        body+='<p>'+e(w['agent']+' / '+WORK[w['activity']])+' / '+e({'pending':'未着手','running':'内部作業中','review_pending':'レビュー待ち','approval_pending':'代表承認待ち','paused':'停止中','failed':'失敗'}.get(w['status'],w['status']))+'</p>'+pf(s,'scm_run',data={**hidden,'id':w['task_id']},caption='ローカル下書きを作成（送信しない）')
    for n in o.db.execute('SELECT * FROM scm_notes WHERE business_id=? AND deal_id=? ORDER BY created_at DESC',(b,identifier)): body+='<details><summary>'+e({'internal_human_note':'内部メモ','human_draft_not_sent':'人の下書き・未送信','local_ai_role_template_not_sent':'AI役割の固定下書き・未送信'}[n['mode']])+' / '+dt(n['created_at'])+'</summary><pre>'+html.escape(n['body'])+'</pre></details>'
    body+='</details>'
    quotes=o.db.execute('SELECT * FROM scm_quotes WHERE business_id=? AND deal_id=? ORDER BY version DESC',(b,identifier)).fetchall()
    body+='<details class="panel"><summary>見積もり明細・税・改訂</summary><p>料金を推測で補完しません。税の必須設定が未確認なら承認・送信・契約を停止します。</p>'
    items=''
    for i in range(1,6): items+=inp('line_label_'+str(i),'明細'+str(i)+'（空欄は未使用）',required=False)+inp('quantity_'+str(i),'数量','1',required=False)+inp('unit_'+str(i),'明示する単価円',required=False)
    body+=pf(s,'scm_quote',items+inp('conditions','適用条件・範囲（個人情報不可）')+inp('expires','有効期限',(now().astimezone(JST).date()+timedelta(days=7)).isoformat(),'date'),{**hidden,'previous':quotes[0]['id'] if quotes else ''},'最新見積を新規・改訂保存')
    for q in quotes:
        data=json.loads(q['body']); body+='<details><summary>第'+str(q['version'])+'版 / '+e(STATUS[q['status']])+'</summary><p>ID '+e(q['id'])+' / 有効期限 '+e(q['expires_day'])+'</p><pre>'+e(data)+'</pre>'
        if data['totals'] is None: body+='<p>税区分・税率・端数処理の確認待ち。設定を確認し、新しい見積版を作成してください。</p>'
        else: body+=table(['税抜円','税円','税込総額円'],[[e(data['totals']['net_yen']),e(data['totals']['tax_yen']),e(data['totals']['gross_yen'])]])
        if owner and q['status']=='approval_pending': body+=''.join(pf(s,'scm_quote_decide','<label><input type="checkbox" name="checked" value="1">代表が料金・税・範囲・期限・表現を確認</label>',{**hidden,'id':q['id'],'decision':v},caption) for v,caption in [('approve','見積を承認（送信しない）'),('reject','却下')])
        body+='</details>'
    body+='</details>'
    bookings=o.db.execute('SELECT * FROM scm_bookings WHERE business_id=? AND deal_id=? ORDER BY created_at',(b,identifier)).fetchall()
    body+='<details class="panel"><summary>面談・サービス予約候補（セミナーは別）</summary>'+pf(s,'scm_booking',opts('kind',{'service':'サービス予約','interview':'個別面談'})+inp('start','候補開始（JST）',kind='datetime-local')+inp('end','候補終了（JST）',kind='datetime-local'),hidden,'候補だけ登録')+table(['ID','種別','開始','状態'],[[e(r['id']),e('サービス' if r['kind']=='service' else '面談'),dt(r['start_at']),e({'candidate':'候補・未確定','confirmed':'確認記録あり','cancelled':'取消'}[r['status']])] for r in bookings])
    if owner:
        body+=pf(s,'scm_seminar',inp('seminar','代表が登録・事業へ明示関連付けしたセミナーID')+inp('participant','参加者アカウント参照（任意）',required=False)+inp('evidence','顧客と参加者の本人確認参照（指定時必須）',required=False),hidden,'権限付きの参照だけ追加（回答は変更しない）')
        body+='<pre>'+e([dict(r) for r in o.db.execute('SELECT seminar_id,participant_username FROM scm_seminars WHERE business_id=? AND deal_id=?',(b,identifier))])+'</pre>'
    body+='</details>'
    invoices=o.db.execute('SELECT * FROM scm_invoices WHERE business_id=? AND deal_id=? ORDER BY created_at',(b,identifier)).fetchall()
    body+='<details class="panel"><summary>代表承認・予約/契約/請求/入金（自動実行なし）</summary>'+pf(s,'scm_action',inp('key','操作キー',uuid4().hex)+opts('kind',ACTIONS)+opts('quote',{'':'指定なし',**{q['id']:'見積第'+str(q['version'])+'版' for q in quotes}})+opts('booking',{'':'指定なし',**{r['id']:r['kind']+' / '+r['start_at'] for r in bookings}})+opts('invoice',{'':'指定なし',**{i['id']:'請求 '+str(i['gross_yen'])+'円 / '+i['id'] for i in invoices}})+inp('amount','入金/全額返金の金額円（必要な場合）',required=False)+opts('purpose',{'contact':'営業連絡','followup':'フォロー'})+'<label>送信文案（送信はしない）</label><textarea name="message" maxlength="4000"></textarea>',hidden,'内容を固定して承認待ちへ')
    for a in o.db.execute('SELECT * FROM scm_actions WHERE business_id=? AND deal_id=? ORDER BY created_at DESC',(b,identifier)):
        body+='<details><summary>'+e(ACTIONS[a['kind']])+' / '+e(STATUS[a['status']])+'</summary><pre>'+e(a['body'])+'</pre><p>試行数 '+str(a['attempts'])+'</p>'
        if owner and a['status']=='approval_pending': body+=''.join(pf(s,'scm_action_decide','<label><input name="checked" type="checkbox" value="1">対象・同意・停止・税・原稿・現在の条件を代表が確認</label>',{**hidden,'id':a['id'],'decision':v},caption) for v,caption in [('approve','この操作だけ承認（実行しない）'),('reject','却下')])
        if owner and a['status'] in ('approved','retry_wait'):
            body+=pf(s,'scm_mock',opts('outcome',{'success':'モック成功','transient':'一時障害（モック）','timeout':'結果不明（モック）','permanent':'恒久失敗（モック）'}),{**hidden,'id':a['id']},'モックだけ実行・本番送信なし')
            if a['kind']=='delete': body+=pf(s,'scm_delete','<label><input name="checked" type="checkbox" value="1" required>保存義務/バックアップを確認し、この事業の顧客情報の論理消去を承認</label>',{**hidden,'id':a['id']},'承認済みの論理消去')
            else: body+=pf(s,'scm_manual',inp('evidence','既に人が確認した事実の証跡参照（秘密値/本文不可）')+'<label><input name="observed" type="checkbox" value="1" required>代表が確認した事実の申告であり、システムは外部処理を実行しない</label>',{**hidden,'id':a['id']},'手動確認を記録（APIは呼ばない）')
        body+='</details>'
    body+=table(['請求ID','税抜円','税円','税込円','区分','未入金円'],[[e(i['id']),e(i['net_yen']),e(i['tax_yen']),e(i['gross_yen']),e('モック' if i['mode']=='mock' else '手動申告・外部未照合'),e(o.sales.invoice_balance(b,i)[0])] for i in invoices])+'</details>'
    body+='<details><summary>監査・進捗履歴</summary><pre>'+e([dict(r) for r in o.db.execute('SELECT actor,event,ref,detail,created_at FROM scm_events WHERE business_id=? AND deal_id=? ORDER BY id DESC LIMIT 100',(b,identifier))])+'</pre></details>'
    return body


def dashboard(o,s,params):
    b,s=scope(o,s,params)
    if not b:
        body=business_links(o,s)
        if s['role']=='representative':
            today=now().astimezone(JST).date(); start=today.replace(day=1).isoformat(); end=(today+timedelta(days=1)).isoformat()
            body+='<h2>今月の事業別記録（当日まで）</h2><p>SCMの税抜請求売上と旧売上は別掲。未計測を0で埋めて全事業合計にしません。</p>'
            for business in o.db.execute('SELECT id,name FROM portfolio_businesses ORDER BY id'):
                stats=o.sales.dashboard(p(s),business['id'],start,end)
                body+='<article class="panel"><h2>'+e(business['name'])+'</h2><p>請求確認売上円：'+e(stats['confirmed_net_yen'] if stats['confirmed_net_yen'] is not None else '未計測')+' / 入金−返金円：'+e(stats['paid_net_cash_yen'] if stats['paid_net_cash_yen'] is not None else '未計測')+'</p><p>旧台帳売上（重複未照合）：'+e(stats['legacy_revenue_yen'] if stats['legacy_revenue_yen'] is not None else '未計測')+'</p></article>'
        return body
    today=now().astimezone(JST).date(); start=params.get('start',[today.replace(day=1).isoformat()])[0]; end=params.get('end',[(today+timedelta(days=1)).isoformat()])[0]; data=o.sales.dashboard(p(s),b,start,end)
    labels={'legacy_revenue_yen':'旧台帳売上（重複未照合・別掲）','expected_gross_yen':'見込み売上（税込、未請求）','quote_gross_yen':'月内案件の最新見積金額（税込）','confirmed_net_yen':'代表確認の請求売上（税抜）','paid_net_cash_yen':'入金−返金（税込）','expense_yen':'記帳済み経費','approx_profit_yen':'概算利益（税抜売上−記帳経費）','contact_count':'対応済み案件','proposal_count':'提案段階以降の案件','quote_approval_percent':'見積承認率%（決定済み最新見積）','booking_percent':'予約率%（候補ありの案件）','contract_percent':'成約記録率%（受付コホート）','repeat_percent':'リピート率%（期間内提供顧客）','unhandled_count':'未対応案件','overdue_count':'次回予定が到来した案件'}
    body='<div class="notice">実測の手動申告のみ。モック・第11段階の予測を混ぜません。集計の現在状態・母数・期間に注意してください。</div><form method="get">'+fields({'business':b})+inp('start','開始日（JST）',start,'date')+inp('end','終了日（含まない）',end,'date')+'<button>この事業・期間で集計</button></form><div class="grid">'
    for key,label in labels.items(): body+=card(label,data[key])
    body+='</div><p>'+e(data['definitions'])+'</p><p>'+e(data['exclusions'])+'</p><pre>'+e(data['denominators'])+'</pre>'
    return body
