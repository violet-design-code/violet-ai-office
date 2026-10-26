"""Authenticated, administrator-assisted journey UI; no public registration endpoint."""
import json
from datetime import timedelta
from uuid import uuid4
from .web_views import e,table,dt
from .sales import STAGES,STATES
from .portfolio_views import LABELS
from .portfolio_views import pf,inp,opts,principal
from .customer_journey import DETAILS,ORIGINS,KINDS,OFFICIAL
from .sales_views import scope
from .store import now
from .seminars import JST


def home(o,s,params):
    b,s=scope(o,s,params); p=principal(s)
    if not b:
        body='<p>権限のある既存事業を選択してください。既存名称を自動変更しません。</p>'
        for r in o.db.execute('SELECT id,name FROM portfolio_businesses ORDER BY id'):
            try: o.customer_journey.access(p,r['id'])
            except PermissionError: continue
            body+='<p><a href="/customer-journey?business='+e(r['id'])+'">'+e(r['name'])+'</a></p>'
        return body
    b=b['id'] if not isinstance(b,str) else b
    service=o.customer_journey; service.access(p,b); business=o.db.execute('SELECT * FROM portfolio_businesses WHERE id=?',(b,)).fetchone()
    hidden={'business':b}; profile=service.profile(p,b); today=now().astimezone(JST).date(); details=json.loads(profile['details']) if profile else {}
    body='<div class="notice">ローカル・管理者による受付補助。公開フォーム・実LINE・外部予約・LLMは未接続。自動送信は無効。承認だけで送信・契約・予約確定しません。</div><p>選択中：'+e(business['name'])+'（'+e(b)+'）</p>'
    body+='<p>事業別紹介 → 興味選択 → 問い合わせ → 目的別同意 → 担当AIへ内部割当 → 相談・面談希望 → 管理者確認 → 予約確定記録 → フォロー</p>'
    body+='<p><a href="/businesses">事業一覧</a> / <a href="/sales?business='+e(b)+'">顧客・同意・営業・見積</a> / <a href="/workflow?business='+e(b)+'">AI作業・停止・代表承認</a></p>'
    state='承認済み・内部参照用（公開なし）' if service.usable(p,b) else '管理者承認待ち・要再確認'
    body+='<section class="panel"><h2>事業別紹介・出典</h2><p>'+e(state)+' / '+e(ORIGINS[profile['origin']] if profile else '区別未確認')+'</p>'
    body+=table(['確認項目','内容'],[[e(label),e(details.get(key) or '未確認') ] for key,label in DETAILS.items()])
    if s['role']=='representative':
        body+='<details><summary>紹介情報を登録・改訂（承認は失効）</summary>'+pf(s,'cj_profile',opts('origin',ORIGINS,profile['origin'] if profile else 'unconfirmed')+''.join(inp(k,v,details.get(k,''),required=False) for k,v in DETAILS.items()),{**hidden,'revision':profile['revision'] if profile else 0},'紹介下書きを保存')+'</details>'
    if profile:
        sources=o.db.execute('SELECT * FROM cj_sources WHERE business_id=? ORDER BY id',(b,)).fetchall()
        body+=table(['出典ID・URL','状態・確認日・期限','確認内容（人の申告）'],[[e(r['id'])+'<br>'+e(r['url']),e({'pending':'確認待ち','unavailable':'取得不可','verified':'代表確認済み'}[r['status']])+' / '+e(r['checked_day'])+' / '+e(r['expires_day']),e(r['excerpt'])] for r in sources]).replace('class="table-wrap"','class="table-wrap cj-wide"')+'<p class="micro">狭い画面では出典表を横にスクロールできます。</p>'
        if profile['origin']!='violet':
            body+='<p>指定公式資料（この環境では取得不可、内容未確認）：'+''.join('<br>'+e(url) for url in OFFICIAL)+'</p><p>公式事業であることとVIOLETの営業権限は別確認。20歳以上の社会人という条件の適用先、契約の法的分類、紹介報酬を契約資料で確認し、連鎖販売取引に該当する場合は必要な説明を省略しません。</p>'
        if s['role']=='representative':
            body+='<details><summary>出典・確認日・有効期限を記録</summary><p>自動取得しません。verifiedは代表が原資料を実際に確認した場合のみ指定してください。既存出典の改訂は現在の版番号を入力。</p>'+pf(s,'cj_source',inp('source_id','出典ID')+inp('url','公開HTTPS資料URL')+opts('status',{'unavailable':'取得不可','pending':'原文確認待ち','verified':'原資料を代表が確認済み'})+inp('checked_day','確認日',today.isoformat(),'date')+inp('expires_day','再確認期限',(today+timedelta(days=30)).isoformat(),'date')+inp('excerpt','確認内容・適用範囲（秘密値・顧客情報禁止）')+inp('revision','現在の出典版番号（新規0）','0','number'),hidden,'出典を保存・承認解除')+'</details>'
            body+=pf(s,'cj_decide',opts('decision',{'approve':'承認','reject':'却下'})+'<label><input type="checkbox" name="checked" value="1" required>原資料・契約・権限・表示・条件を代表が確認</label>',{**hidden,'revision':profile['revision']},'紹介情報を判断（外部公開なし）')
    body+='</section>'
    if not profile: return body
    contacts=o.db.execute("SELECT id,display_name FROM portfolio_contacts WHERE business_id=? AND state='active'",(b,)).fetchall()
    body+='<h2>本人希望の受付（公開フォームではありません）</h2>'+pf(s,'cj_intake',opts('contact',{'':'新しい事業別顧客として登録',**{r['id']:r['display_name'] for r in contacts}})+inp('name','新規の場合の表示名',required=False)+inp('channel','新規の場合の連絡先参照（必要最小限）',required=False)+inp('interest','本人が選んだ関心・希望')+inp('inquiry','問い合わせ（必要最小限）')+opts('mode',{'manual':'本人の希望を人が受付（実績を捏造しない）','mock':'合成テスト・模擬受付'}),{**hidden,'key':uuid4().hex},'受付を記録（同意は別途確認）')
    for case in service.summary(p,b):
        d=case['deal_id']; body+='<section class="panel"><h2>'+e(case['interest'])+'</h2><p>'+e('模擬受付・実績ではない' if case['mode']=='mock' else '人による受付記録')+' / 営業段階：'+e(STAGES[case['stage']])+' / '+e(STATES[case['state']])+'</p><p>連絡・日程・フォロー同意：'+e(' / '.join(label+('：同意あり' if case['consents'][key] else '：未取得・停止等で利用不可') for key,label in [('contact','連絡'),('schedule','日程'),('followup','フォロー')]))+'</p><p><a href="/sales-deal?business='+e(b)+'&amp;id='+e(d)+'">顧客の同意・停止・予約承認・既存見積を確認</a></p>'
        body+=table(['担当AI','理由','作業状態'],[[e(r['agent']),e(r['reason']),e(LABELS.get(r['status'],r['status']))] for r in case['tasks']])
        body+=pf(s,'cj_route',opts('kind',{k:v[1] for k,v in KINDS.items()})+inp('due','期限',today.isoformat(),'date'),{**hidden,'deal':d},'既存担当へ内部タスク作成（AI01・担当・AI28）')
        body+=table(['面談種別','開始','終了','状態'],[['個別面談' if r['kind']=='interview' else 'サービス予約',dt(r['start_at']),dt(r['end_at']),e({'candidate':'候補・未確定','confirmed':'確認記録あり','cancelled':'取消'}[r['status']])] for r in case['bookings']])
        body+='<details><summary>本人希望の面談候補を登録</summary>'+pf(s,'cj_request',inp('start','開始（例 2026-11-01T10:00:00+09:00）')+inp('end','終了（タイムゾーン必須）'),{**hidden,'deal':d,'key':uuid4().hex},'希望受付のみ・予約は確定しない')+'</details><p>予約の代表承認・模擬確定は既存営業画面で、見積・税設定・紹介条件・日程同意を確認した後だけ可能。フォロー案も目的別同意が必要です。</p></section>'
    return body
