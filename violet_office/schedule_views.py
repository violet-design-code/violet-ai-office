"""Representative-only server rendered calendar, text/voice candidates and explicit saves."""
from datetime import datetime,date,time,timedelta
from uuid import uuid4
from .business_management_views import input_,select as base_select,check
from .web_views import e,fields,table
from .integrations.adapters import Principal
from .store import now
from .seminars import JST
from .schedule import local_input

NOTICES={'pending':'通知準備済み','retry_wait':'モック再試行待ち','mock_sent':'モック成功・実配信なし','failed':'モック失敗・停止','missed':'遅延のため未実行','superseded':'変更・キャンセルにより無効','consent_hold':'同意撤回・停止','recovery_hold':'復元後・再確認待ち'}
WORK={'rule_based_manual_record':'代表操作・ルール処理','admin_review':'事業内のAI権限を管理者確認待ち'}
KINDS={'created':'登録','updated':'変更','cancelled':'キャンセル'}


def select(key,label,values,current=''):
    identifier=key+'-'+uuid4().hex
    return base_select(key,label,values,current).replace('for="'+key+'"','for="'+identifier+'"').replace('id="'+key+'"','id="'+identifier+'"')

def actor(s):return Principal(s['role'],s['username'])
def form(s,action,body='',data=None,label='確認して保存'):
    return '<form method="post" action="/schedule-action">'+fields({'csrf':s['csrf'],'nonce':uuid4().hex,'action':action,**(data or {})})+body+'<button>'+e(label)+'</button></form>'
def local(value):return datetime.fromisoformat(value).astimezone(JST).strftime('%Y-%m-%dT%H:%M') if value else ''

def home(o,s,params,result=None):
    service=o.schedule;p=actor(s);service.access(p)
    if result and result.get('saved_id'):
        saved=service.row(p,result['saved_id']);params={**params,'date':[datetime.fromisoformat(saved['start_at']).astimezone(JST).date().isoformat()]}
    day=params.get('date',[now().astimezone(JST).date().isoformat()])[0];start=datetime.combine(date.fromisoformat(day),time.min,JST);business=params.get('business',[''])[0] or None;service.access(p,business)
    data=service.listing(p,start,start+timedelta(days=7),business);editing=params.get('id',[''])[0];r=dict(service.row(p,editing)) if editing else {};candidate=result.get('candidate',{}) if result else {}
    body='<div class="notice">代表本人専用・日本時間。AI23（面談予約管理担当）／AI01（統括）。現在はルールベース。音声の解析結果は候補で、確認・保存前には登録しません。iPhone実通知・Google同期は未接続です。既存の顧客予約・セミナー日程は自動取込しません。代表本人の必要な予定だけ登録してください。</div>'
    if result:body+='<p id="schedule-answer" role="status">'+e(result['answer'])+'</p>'
    else:body+='<p id="schedule-answer" role="status">予定を確認するか、入力してください。</p>'
    body+='<section class="panel"><h2>音声・日本語指示</h2><p>ローカル認識が利用可能なブラウザーだけマイクを使用できます。音声を外部サービスへ送る方式は無効です。未対応なら文字入力、または端末側の音声入力をご利用ください（端末側の規約・プライバシー設定は別途確認）。</p><p id="voice-status" role="status">音声機能の対応状況を確認中です。</p><button type="button" id="voice-start" disabled>端末内で音声入力</button> <button type="button" id="voice-stop" disabled>音声入力を停止</button> <button type="button" id="voice-read" disabled>回答を端末内で読み上げ</button>'
    body+=form(s,'interpret','<label for="schedule-instruction">日本語の指示（音声認識後も内容を確認）</label><textarea id="schedule-instruction" name="instruction" maxlength="2000" required placeholder="11月5日の14時から石川さんと佐久市で打ち合わせするから予定入れて"></textarea>',label='指示を確認・検索する（まだ登録しない）')+'</section>'
    if result and result.get('questions'):body+='<ul>'+''.join('<li>'+e(q)+'</li>' for q in result['questions'])+'</ul>'
    body+='<h2>'+('予定の変更' if r else '予定の新規登録')+'</h2>'
    businesses={r['id']:r['name'] for r in o.db.execute('SELECT id,name FROM portfolio_businesses ORDER BY id')}
    editor=input_('title','予定の内容',r.get('title',candidate.get('title','')),required=True)+input_('person','相手（必要最小限）',r.get('person',candidate.get('person','')))+input_('location','場所',r.get('location',candidate.get('location','')))+input_('start','開始日時（日本時間）',local(r.get('start_at',candidate.get('start'))),'datetime-local',True)+input_('end','終了日時（推測で補完しません）',local(r.get('end_at')),'datetime-local',True)+select('business','事業（任意）',{'':'代表個人の予定',**businesses},r.get('business_id') or business or '')+check('checked','日時・相手・場所・内容を確認しました')
    if r and r['status']=='cancelled':body+='<p>キャンセル済み。履歴は保持され、通知対象外です。</p>'
    else:body+=form(s,'save',editor,{'key':uuid4().hex,'id':r.get('id',''),'revision':r.get('revision',0)},'確認した予定を保存')
    if r and r['status']=='active':body+=form(s,'cancel',check('checked','この予定をキャンセルします（物理削除せず履歴保持）'),{'id':r['id'],'revision':r['revision']},'予定をキャンセル')
    body+='<h2>予定の確認（選択日から7日間）</h2><form method="get">'+input_('date','表示開始日',day,'date',True)+select('business','表示する事業',{'':'自分の全事業・個人予定',**businesses},business or '')+'<button>予定を表示</button></form>'
    body+=table(['日時（JST）','内容','相手・場所','操作'],[[e(local(r['start_at']).replace('T',' ')+'〜'+local(r['end_at']).replace('T',' ')),e(r['title']),e(r['person']+' / '+r['location']),'<a href="/schedule?id='+e(r['id'])+'&amp;date='+e(day)+'">変更・キャンセル</a>'] for r in data])
    if result and result.get('events') is not None:body+='<h3>指示した期間の検索結果</h3>'+table(['日時（JST）','内容'],[[e(local(r['start_at']).replace('T',' ')),e(r['title'])] for r in result['events']])
    body+='<h2>空き時間</h2>'+form(s,'free',input_('day','検索する日',day,'date',True)+input_('from_time','検索開始（営業時間の確定値ではありません）','09:00','time',True)+input_('to_time','検索終了','18:00','time',True),label='全事業の予定を除いて空き時間を確認')
    setting=o.db.execute('SELECT * FROM sc_settings WHERE owner=?',(p.identity,)).fetchone();body+='<h2>通知の設定・状態</h2><p>標準：前日21:00の翌日一覧、開始60分前。5分以上遅れた通知は送信せず未実行にします。モック成功はiPhone配信成功ではありません。</p>'+form(s,'settings',check('enabled','30秒周期のローカル通知モックを有効にする',bool(setting and setting['enabled']))+check('consent','代表本人の予定を通知準備に使うことに同意',bool(setting and setting['consent']))+check('checked','設定を確認しました'),label='通知設定を保存')
    body+=form(s,'tick',label='現在時刻で通知をモック検証（実送信なし）')
    notes=service.notifications(p);body+=table(['通知予定（JST）','種類','状態','試行回数'],[[e(local(n['due_at']).replace('T',' ')),e('前日21時・翌日一覧' if n['kind']=='daily' else '60分前'),e(NOTICES.get(n['state'],'要確認')),e(n['attempts'])] for n in notes])
    assignment=service.assignment(p,business);body+='<p>AI23の当該事業内部作業権限：'+e('個人予定・代表本人が操作' if business is None else 'あり' if assignment['business_work_allowed'] else '管理者確認待ち（自動割り当てしません）')+'。<a href="/ai-management'+('?business='+e(business) if business else '')+'">既存の担当割り当て画面</a></p>'
    work=o.db.execute('SELECT w.agent,w.kind,w.state,w.created_at FROM sc_work w JOIN sc_events e ON e.id=w.event_id WHERE e.owner=? ORDER BY w.created_at DESC LIMIT 30',(p.identity,)).fetchall()
    body+='<h2>スケジュール担当の処理記録</h2>'+table(['担当','処理','実行区分','日時'],[[e(w['agent']),e(KINDS.get(w['kind'],'要確認')),e(WORK.get(w['state'],'要確認')),e(local(w['created_at']).replace('T',' '))] for w in work])
    body+='<h2>iPhoneカレンダー向け手動書き出し</h2><p>次の31日分のICSをダウンロードできます。カレンダーやロック画面に予定内容が表示される場合があります。60分前アラームと前日21時の一覧イベントを含みます。端末への取込・通知許可は手動、実機未検証です。自動同期や変更・キャンセルの自動反映はありません。再取込で重複する場合は古い専用カレンダーを端末側で整理してください。</p>'+form(s,'calendar',check('checked','個人予定をカレンダーファイルへ持ち出すことを確認しました'),label='確認してICSを書き出す（外部送信なし）')
    return body+'<script src="/assets/schedule.js" defer></script>'

def action(o,s,data):
    service=o.schedule;p=actor(s);service.access(p);kind=data.get('action');checked=data.get('checked')=='1'
    if kind=='interpret':result=service.interpret(p,data.get('instruction',''))
    elif kind=='save':
        ref=service.save(p,data['key'],data['title'],local_input(data['start']),local_input(data['end']),data.get('person',''),data.get('location',''),data.get('business') or None,data.get('id') or None,int(data.get('revision',0)),checked)
        r=service.row(p,ref);result={'answer':datetime.fromisoformat(r['start_at']).astimezone(JST).strftime('%m月%d日%H時%M分')+'から'+r['person']+('との' if r['person'] else '')+r['title']+'を保存しました。','saved_id':ref}
    elif kind=='cancel':service.cancel(p,data['id'],int(data['revision']),checked);result={'answer':'予定をキャンセルしました。通知対象から除外し、履歴は保持しています。'}
    elif kind=='settings':service.settings(p,data.get('enabled')=='1',data.get('consent')=='1',checked);result={'answer':'通知設定を保存しました。実通知サービスは未接続です。'}
    elif kind=='tick':result={'answer':'ローカル通知モックを確認しました。実送信はありません。','tick':service.tick(owner=p.identity)}
    elif kind=='free':
        slots=service.free(p,data['day'],data['from_time'],data['to_time']);result={'answer':data['day']+'の空き時間：'+('、'.join(local(a).split('T')[1]+'〜'+local(z).split('T')[1] for a,z in slots) or 'ありません。')}
    else:raise ValueError('Known schedule operation required')
    return result
