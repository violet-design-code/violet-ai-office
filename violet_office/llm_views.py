"""Pilot UI: scoped drafts and model use approval, no publishing action."""
import json
from uuid import uuid4
from .integrations.adapters import Principal
from .web_views import e,fields,table
from .business_management_views import input_,select,check
from .llm_pilot import STAGES
from .llm_provider import config,ModelError,money
from .store import now
from .seminars import JST

STATE={'pending':'工程実行待ち','running':'モデル実行中','retry_wait':'30秒待機・手動再試行待ち','failed':'失敗・停止','paused':'一時停止','reconciliation_required':'結果不明・料金照合待ち（再実行禁止）','approval_pending':'代表承認待ち','approved_draft_no_execution':'下書き承認済み・外部未実行','changes_requested':'AI01から修正提案・停止','rejected':'代表却下','succeeded':'モデル出力保存済み','held':'停止・権限変更のため保留','unknown':'課金・結果不明（予算保持）','rejected':'却下・未受理','invalid_output':'出力形式・料金上限の確認が必要'}
def actor(s):return Principal(s['role'],s['username'])
def form(s,action,b,body='',data=None,label='保存'):
    if s['role']!='representative':return ''
    return '<form method="post" action="/llm-pilot-action">'+fields({'csrf':s['csrf'],'action':action,'business':b,'nonce':uuid4().hex,**(data or {})})+body+'<button>'+e(label)+'</button></form>'
def home(o,s,params):
    p=actor(s);b=params.get('business',[''])[0]
    if not b:
        o.portfolio.access(p,owner=True);return '<p>既存の事業を選択してください。事業・担当を自動登録しません。</p>'+table(['事業'],[['<a href="/llm-pilot?business='+e(r['id'])+'">'+e(r['name'])+'</a>'] for r in o.db.execute('SELECT id,name FROM portfolio_businesses ORDER BY id')])
    o.llm.access(p,b);name=o.db.execute('SELECT name FROM portfolio_businesses WHERE id=?',(b,)).fetchone()[0]
    body='<h2>'+e(name)+'</h2><p class="notice">Instagram下書き試験。AI01→AI07→AI09→AI01。既定はモック。実APIは環境設定・既存担当権限・月次予算・個別の有料API承認がそろうまで実行しません。投稿・LINE送信・公開機能はありません。</p><p><a href="/ai-management?business='+e(b)+'">既存の担当割り当て</a> / <a href="/business?business='+e(b)+'">事業・予算</a> / <a href="/costs">全体のAPI経費・予算</a></p>'
    body+=form(s,'create',b,'<label for="llm-brief">匿名化したInstagram投稿の依頼（個人情報・秘密情報は禁止）</label><textarea name="brief" id="llm-brief" maxlength="2000" required placeholder="Instagram投稿の下書きを作成してください。未確認の条件は断定しないでください。"></textarea>'+input_('count','投稿案の数（1〜3）',1,'number',True)+select('mode','実行区分',{'mock':'モック・API利用なし','openai':'OpenAI接続用・個別許可前は実行不可'},'mock')+check('checked','入力は匿名・公開可能な素材だけで、対象事業と目的を確認しました'),{'key':uuid4().hex},'AI01へ依頼を登録')
    halted=o.db.execute('SELECT halted FROM lp_controls').fetchone()[0];body+='<p>生成AI試験の全体停止：'+('停止中' if halted else '解除中（自動実行しません）')+'</p>'+form(s,'halt',b,check('checked','生成AI試験の全体制御を確認'),{'enabled':'0' if halted else '1'},'全体停止を解除' if halted else '生成AI試験を全体停止')
    for r in o.llm.listing(p,b):
        body+='<details class="panel" open><summary>'+e(r['mode'])+' / '+e(STATE.get(r['state'],r['state']))+' / '+e(r['id'])+'</summary><p>'+e(r['brief'])+'</p>'
        if r['mode']=='openai':
            try:
                cfg=config('openai');ceiling=money(25024,cfg['input_rate'])+money(cfg['max_output'],cfg['output_rate'])
                body+='<p>モデル設定：'+e(cfg['model'])+' / 入力USD/100万token：'+e(cfg['input_rate'])+' / 出力：'+e(cfg['output_rate'])+' / 価格根拠：'+e(cfg['price_ref'])+'</p><p>1呼出しの設定価格による保守的予約上限：USD '+e(ceiling/1000000)+'（請求保証ではありません）。最大4工程、工程あたり最大3試行。実API利用は環境側でも既定無効です。</p>'
            except ModelError:body+='<p>モデル名・トークン価格・価格根拠が未設定です。有料実行を停止します。</p>'
        if s['role']=='representative':
            if r['mode']=='openai' and r['state'] in ('pending','paused','retry_wait'):body+=form(s,'authorize',b,check('checked','この依頼の匿名化入力と設定済み価格・予算を確認し、有料モデルの利用を明示的に許可する'),{'id':r['id']},'有料API利用を許可（まだ実行しない）')
            if r['state'] in ('pending','retry_wait'):body+=form(s,'run',b,check('checked','表示した実行区分と対象事業を確認しました'),{'id':r['id'],'step':r['current_step']},'4工程をモック実行' if r['mode']=='mock' else '次の1工程を実APIで実行（課金が発生し得ます）')
            if r['state'] not in ('approved_draft_no_execution','rejected','paused'):body+=form(s,'stop',b,data={'id':r['id']},label='この依頼を停止')
            if r['state']=='paused':body+=form(s,'resume',b,data={'id':r['id']},label='再開待ちに戻す（自動実行なし）')
            if r['state'] in ('running','paused'):body+=form(s,'quarantine',b,data={'id':r['id']},label='90秒以上応答がない呼び出しを照合待ちへ')
        for step in o.db.execute('SELECT * FROM lp_steps WHERE job_id=? ORDER BY step',(r['id'],)):
            body+='<h3>'+e(step['agent'])+' '+e(o.store.agents[step['agent']]['role'])+' / '+e(step['kind'])+' / '+e(STATE.get(step['state'],step['state']))+'</h3>'
            if step['output']:body+='<pre>'+e(json.dumps(json.loads(step['output']),ensure_ascii=False,indent=2))+'</pre>'
        calls=o.db.execute('SELECT * FROM lp_calls WHERE job_id=? ORDER BY created_at,id',(r['id'],)).fetchall();body+=table(['実行区分/モデル','工程・試行','入力/出力トークン','推定USD（請求額ではない）','状態・エラー'],[[e(c['provider']+'/'+c['model']),e(str(c['step']+1)+' / '+str(c['attempt'])),e(str(c['input_tokens'])+' / '+str(c['output_tokens'])),e('未計測・予算予約を保持' if c['estimated_microusd'] is None else str(c['estimated_microusd']/1000000)),e(c['state']+' / '+(c['error_code'] or 'なし'))] for c in calls])
        if r['state']=='approval_pending':
            for decision,label in [('approve','下書きとして承認（公開・送信なし）'),('reject','却下')]:body+=form(s,'decide',b,check('checked','代表が原稿・AI01報告・事実・権利・個人情報・法令と公式条件の要確認事項を確認した')+input_('note','人間の確認記録（秘密情報は禁止）',required=True),{'id':r['id'],'decision':decision},label)
        body+='</details>'
    return body

def action(o,s,data):
    p=actor(s);b=data['business'];o.llm.access(p,b,True);kind=data['action'];identifier=data.get('id')
    if kind=='create':o.llm.create(p,b,data['key'],data['brief'],int(data['count']),data['mode'],data.get('checked')=='1')
    elif kind=='authorize':o.llm.authorize_live(p,b,identifier,data.get('checked')=='1')
    elif kind=='run':
        if data.get('checked')!='1':raise ValueError('Execution confirmation required')
        r=o.llm.row(p,b,identifier)
        if r['mode']=='mock':o.llm.execute_job(p,b,identifier,expected_step=int(data['step']))
        else:o.llm.execute_next(p,b,identifier,expected_step=int(data['step']))
    elif kind in ('stop','resume'):o.llm.control(p,b,identifier,kind)
    elif kind=='quarantine':o.llm.quarantine(p,b,identifier)
    elif kind=='halt':
        if data.get('checked')!='1':raise ValueError('Explicit control required')
        o.llm.halt(p,data['enabled']=='1')
    elif kind=='decide':o.llm.decide(p,b,identifier,data['decision'],data.get('checked')=='1',data['note'])
    else:raise ValueError('Pilot action required')
    return '/llm-pilot?business='+b
