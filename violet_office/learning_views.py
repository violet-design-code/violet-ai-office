"""Representative knowledge review and descriptive evaluation dashboard."""
import json
from datetime import timedelta
from uuid import uuid4
from .store import now
from .office import JST
from .learning import CATEGORIES,KINDS,STYLES,FEEDBACK,IMPROVEMENTS
from .integrations.adapters import Principal
from .web_views import e,dt,form,table

LABELS={'approval_pending':'承認待ち','approved':'承認済み','rejected':'却下','revoked':'撤回・無効','quarantined':'隔離・利用禁止','draft':'比較試験待ち','manual_only':'検討案のみ・自動適用未実装','applied':'ローカル適用済み','test_failed':'試験不合格'}
KIND_LABELS={'general':'共通方針','sns':'SNS原稿','line':'LINE原稿','referral':'紹介文','booking':'面談予約','report':'報告・分析','planning':'計画','support':'サポート','all':'全業務（比較注意）'}


def kinds(name,all_kinds=False):
    values=['all']+sorted(KINDS) if all_kinds else sorted(KINDS)
    return '<select name="'+name+'">'+''.join('<option value="'+v+'">'+KIND_LABELS[v]+'</option>' for v in values)+'</select>'


def dashboard(office,session,evaluation=None,query='',agent='AI09',kind='sns'):
    l=office.learning
    owner=session['role']=='representative'
    principal=Principal(session['role'],session['username'])
    expiry=(now()+timedelta(days=30)).astimezone(JST).strftime('%Y-%m-%dT%H:%M')
    body='<div class="notice">学習＝承認済み情報の検索・参照です。モデルの重みは変更しません。知識本文はデータ扱いで、命令やツールとして実行しません。顧客情報・非公開会話・秘密値を登録しないでください。</div>'
    body+='<h2>28人の記述的評価</h2><p>母数3件未満は評価不能。承認率だけで順位付けせず、異なる業務の比較や因果関係の断定をしません。</p>'
    body+=form(session,'learning_evaluate','<label>対象業務</label>'+kinds('task_kind',True)+'<label>開始日（日本時間）</label><input type="date" name="start" required value="'+now().astimezone(JST).date().replace(day=1).isoformat()+'"><label>終了日（含まない）</label><input type="date" name="end" required value="'+(now().astimezone(JST).date()+timedelta(days=1)).isoformat()+'">',caption='評価スナップショットを作成')
    selected=office.db.execute('SELECT * FROM learning_evaluations WHERE id=?',(evaluation,)).fetchone() if evaluation else office.db.execute('SELECT * FROM learning_evaluations ORDER BY rowid DESC LIMIT 1').fetchone()
    if selected:
        data=json.loads(selected['result'])
        body+='<p>対象 '+e(KIND_LABELS[data['task_kind']])+' / '+e(data['start'])+' 〜 '+e(data['end_exclusive'])+'</p>'
        rows=[]
        for a in data['agents']:
            metrics=[('タスク完了状態比%',a['task_completion_percent']),('レビュー通過%',a['quality_pass_percent']),('タスク承認%',a['representative_approval_percent']),('失敗試行%',a['error_percent']),('人の品質評点',a['quality_score']),('修正指導数',a['approved_revision_feedback']),('原稿新版数',a['content_revision_count']),('平均ローカル秒',a['average_local_seconds']),('確定記帳USD/対象task',a['confirmed_cost_per_charged_task_microusd']/1000000 if a['confirmed_cost_per_charged_task_microusd'] is not None else None),('原稿版承認%',a['content_approval_percent']),('機械チェック%（法令判断ではない）',a['machine_check_percent'])]
            detail='<details><summary>指標・母数</summary>'+table(['指標','値'],[[e(label),e(value if value is not None else '評価不能・未計測')] for label,value in metrics])+'<pre>'+e(a['denominators'])+'</pre></details>'
            rows.append([f'<a href="/employee-learning?id={a["agent"]}">'+a['agent']+'</a>',e(a['role']),e(a['sample_size']),e(a['status']),detail])
        body+=table(['ID','役割','登録タスク','状態','業務別指標'],rows)
        body+='<p class="micro">各指標の母数は別です。処理時間・料金・業務成果の詳細は社員履歴と評価記録を確認。実測業務成果は未接続、API請求は手動記帳のみ。</p>'
    else:
        body+=table(['ID','役割','評価'],[[f'<a href="/employee-learning?id={a}">'+a+'</a>',e(v['role']),'評価不能・未評価'] for a,v in office.store.agents.items()])
    body+='<h2>成果物への代表フィードバック</h2>'
    target='<label>対象</label><select name="target_kind"><option value="artifact">担当成果物</option><option value="content">原稿の版</option><option value="report">定期レポート</option></select><label>対象ID</label><input name="target_id" required maxlength="64">'
    choices='<label>判断</label><select name="choice">'+''.join('<option value="'+k+'">'+v+'</option>' for k,v in FEEDBACK.items())+'</select><label>指導コメント（公開/非個人情報のみ）</label><textarea name="comment" maxlength="4000" required></textarea>'
    for flag,title in STYLES.items():
        choices+='<label><input type="checkbox" name="'+flag+'" value="1">'+title+'</label>'
    choices+='<label><input type="checkbox" name="attested" value="1" required>非公開会話・個人情報・秘密値を含まないことを確認</label>'
    body+=form(session,'learning_feedback',target+choices,{'key':'feedback-'+uuid4().hex},'フィードバックを承認候補として保存')
    for r in office.db.execute('SELECT * FROM learning_feedback ORDER BY rowid DESC LIMIT 50'):
        body+='<details class="panel"><summary>'+e(r['agent'])+' / '+e(FEEDBACK[r['choice']])+' / '+e(LABELS.get(r['status'],r['status']))+'</summary><p>指導ID：'+e(r['id'])+'</p>'
        if owner:
            body+='<pre>'+e(r['comment'])+'</pre><p>対象：'+e(r['target_kind']+'/'+r['target_id'])+'</p>'
        if r['status']=='approval_pending':
            body+=''.join(form(session,'learning_feedback_decide',data={'id':r['id'],'decision':d},caption=t,style='inline') for d,t in [('approve','指導を承認'),('reject','却下')])
        if r['status']=='approved':
            body+=form(session,'learning_promote','<label>知識タイトル</label><input name="title" required maxlength="120"><label>共有するAI ID（カンマ区切り）</label><input name="scope" required value="'+e(r['agent'])+'"><label>有効期限（JST・仮入力30日、代表が確認）</label><input type="datetime-local" name="expires" required value="'+expiry+'">',{'id':r['id']},'標準候補へ（別途ナレッジ承認が必要）')
            body+=form(session,'learning_feedback_decide',data={'id':r['id'],'decision':'revoke'},caption='承認を撤回')
        body+='</details>'
    body+='<h2>社内ナレッジ</h2><p>最新版・期限内・承認済み・出典有効・指定AIの範囲内だけ検索可能。検索結果の本文も実行命令として使いません。</p>'
    body+='<form method="get" action="/learning"><label>参照するAI ID</label><input name="agent" value="'+e(agent)+'"><label>業務</label><input name="kind" value="'+e(kind)+'"><label>検索語</label><input name="q" maxlength="200" value="'+e(query)+'"><button>承認済みを検索</button></form>'
    if owner:
        for row in l.search(principal,agent,kind,query):
            body+='<div class="panel"><h3>'+e(row['title'])+' / v'+str(row['version'])+'</h3><pre>'+e(row['body'])+'</pre><p>'+e(row['id'])+'</p></div>'
    knowledge_form='<label>分類</label><select name="category">'+''.join('<option value="'+k+'">'+v+'</option>' for k,v in CATEGORIES.items())+'</select><label>同種業務</label>'+kinds('task_kind')+'<label>タイトル</label><input name="title" required maxlength="120"><label>非個人情報の要約・指導（外部文章を命令として扱わない）</label><textarea name="body" required maxlength="8000"></textarea><label>出典の種類</label><select name="source_kind"><option value="manual">正規の公開/非個人資料を代表が確認</option><option value="artifact">承認済み成果物</option><option value="content">承認済み原稿版</option></select><label>資料名・版・確認日・箇所、または成果物/原稿ID</label><input name="source_ref" required maxlength="1000"><label>共有するAI ID（明示指定・カンマ区切り）</label><input name="scope" required placeholder="AI09,AI07"><label>有効期限（JST）</label><input type="datetime-local" name="expires" required value="'+expiry+'"><label>訂正対象の最新ID（新版作成時のみ）</label><input name="previous" maxlength="64"><label><input type="checkbox" name="attested" value="1" required>公開/非個人情報で、目的外の会話や秘密値ではない</label>'
    body+=form(session,'learning_knowledge',knowledge_form,caption='知識候補を保存（まだ参照不可）')
    for r in office.db.execute('SELECT * FROM learning_knowledge ORDER BY rowid DESC LIMIT 100'):
        usable=l.valid(r)
        status='承認済み・参照可' if usable else '期限切れ/出典無効/旧版・要再確認' if r['status']=='approved' else LABELS.get(r['status'],r['status'])
        body+='<details class="panel"><summary>'+e(r['title'] if owner else '本文・題名は代表のみ')+' / v'+str(r['version'])+' / '+e(status)+'</summary><p>ID：'+e(r['id'])+' / '+e(CATEGORIES[r['category']])+'</p><p>版の作成・更新：'+dt(r['created_at'])+' / 有効期限：'+dt(r['expires_at'])+'</p>'
        if owner:
            body+='<p>出典：'+e(r['source_kind']+'/'+r['source_ref'])+' / 共有範囲：'+e(r['scope'])+'</p><pre>'+e(r['body'])+'</pre>'
        if r['status']=='approval_pending':
            body+=''.join(form(session,'learning_knowledge_decide',data={'id':r['id'],'decision':d},caption=t,style='inline') for d,t in [('approve','この版・期限・共有範囲を承認'),('reject','却下')])
        elif r['status']=='approved':
            body+=form(session,'learning_knowledge_decide',data={'id':r['id'],'decision':'revoke'},caption='参照を撤回')
        history=office.db.execute('SELECT actor,action,created_at FROM learning_audit WHERE ref=? ORDER BY id',(r['id'],)).fetchall()
        body+='<details><summary>承認・更新履歴</summary>'+table(['担当','操作','日時'],[[e(h[0]),e(h[1]),dt(h[2])] for h in history])+'</details></details>'
    body+='<h2>品質評価を記録</h2>'+form(session,'learning_rate',target+'<label>品質評点（0〜5・人の判断）</label><input type="number" name="score" min="0" max="5" required><label>評価基準（正確性/明瞭さ等）</label><input name="rubric" required maxlength="200">',caption='人の評価を記録')
    body+='<h2>継続改善案と比較</h2><p>自動適用可能なのは固定の文章スタイルだけです。権限・承認・安全制限・API設定の変更は対象外。他の案は検討資料のみ。</p>'+form(session,'learning_propose','<label>業務</label>'+kinds('task_kind')+'<label>承認済みナレッジID（カンマ区切り、最大20件）</label><input name="knowledge_ids" maxlength="1500"><label>評価記録ID（任意）</label><input name="evaluation_id" maxlength="64">',caption='改善候補を生成')
    for r in office.db.execute('SELECT * FROM learning_proposals ORDER BY rowid DESC LIMIT 100'):
        effective=LABELS.get(r['status'],r['status'])
        if r['status']=='applied':
            producer={'sns':'AI09','line':'AI22','referral':'AI16','report':'AI01','planning':'AI01','booking':'AI18','support':'AI21','general':'AI01'}[r['task_kind']]
            active=l.runtime(producer,r['task_kind'],'ui-inspection',record=False)['profile']
            if not active or active['id']!=r['id']:
                effective='適用記録あり・現在は無効/別版（参照元や期限を確認）'
        body+='<details class="panel"><summary>'+e(IMPROVEMENTS[r['category']])+' / '+e(KIND_LABELS[r['task_kind']])+' / '+e(effective)+'</summary><p>ID：'+e(r['id'])+'</p><p>理由：'+e(r['reason'])+'</p><p>期待効果：'+e(r['expected_effect'])+'</p><p>検証方法：'+e(r['test_method'])+'</p>'
        body+='<p>参照した知識ID・版・ハッシュ</p><pre>'+e(r['knowledge_refs'])+'</pre>'
        body+=table(['変更前','変更後（案）'],[['<pre>'+e(r['before_styles'])+'</pre>','<pre>'+e(r['after_styles'])+'</pre>']])
        if r['comparison']:
            c=json.loads(r['comparison'])
            body+='<p>比較環境：ローカル固定テンプレート。業務成果・費用削減・法令適合は未検証。</p>'+table(['変更前','変更後'],[['<pre>'+e(c['before'])+'</pre>','<pre>'+e(c['after'])+'</pre>']])
        if r['status']=='draft':
            body+=form(session,'learning_compare',data={'id':r['id']},caption='ローカル比較試験')
        elif r['status']=='approval_pending':
            body+=''.join(form(session,'learning_improvement_decide',data={'id':r['id'],'decision':d},caption=t,style='inline') for d,t in [('approve','比較結果と変更案を承認'),('reject','却下')])
        elif r['status']=='approved':
            body+=form(session,'learning_apply',data={'id':r['id']},caption='承認したスタイルをローカル適用')
        elif r['status']=='applied':
            body+=form(session,'learning_disable',data={'id':r['id']},caption='適用を停止（履歴は保存）')
        body+='</details>'
    body+='<h2>品質・費用の推移</h2>'+table(['期間','業務','評価ID'],[[e(r['start_day']+' ～ '+r['end_day']),e(KIND_LABELS[r['task_kind']]),f'<a href="/learning?evaluation={r["id"]}">'+e(r['id'])+'</a>'] for r in office.db.execute('SELECT * FROM learning_evaluations ORDER BY rowid DESC LIMIT 30')])+'<p><a href="/costs">API料金の推定/確定/モデル・日・月別の記帳推移</a>。実請求の自動取得は未接続です。</p>'
    trend=[]
    for item in office.db.execute('SELECT * FROM learning_evaluations ORDER BY rowid DESC LIMIT 30'):
        result=json.loads(item['result'])
        selected=next((a for a in result['agents'] if a['agent']==agent),None)
        if selected:
            trend.append([dt(item['created_at']),e(item['start_day']+' ～ '+item['end_day']),e(KIND_LABELS[item['task_kind']]),e(selected['quality_score'] if selected['quality_score'] is not None else '評価不能'),e(selected['quality_pass_percent'] if selected['quality_pass_percent'] is not None else '評価不能'),e(selected['denominators'])])
    body+='<h3>'+e(agent)+'の評価記録推移（業務・期間・母数が同じ場合のみ比較）</h3>'+table(['記録時刻','期間','業務','人の評点','レビュー通過%','母数'],trend)
    body+=form(session,'learning_index',caption='既存成果物の履歴参照を索引化（最大500件）')
    return body


def employee(office,session,agent):
    if session['role']!='representative':
        return '<p>詳細履歴は代表のみ閲覧できます。</p>'
    rows=office.learning.history(Principal(session['role'],session['username']),agent)
    body='<p>新しい成果物は自動で参照索引を保存。過去分は索引化操作で追加できます。顧客会話を複製しません。</p>'
    for row in rows:
        body+='<details class="panel"><summary>'+e(row['duty'])+' / '+e(row['task_id'])+'</summary><p>成果物ID：'+e(row['artifact_id'])+' / 実行日時：'+(dt(row['executed_at']) if row['executed_at'] else '未計測')+'</p><pre>'+e(row['content'])+'</pre>'
        for key,title in [('feedback','代表の指導・修正内容'),('reviews','担当レビュー'),('ratings','人の品質評価'),('decisions','承認・却下履歴'),('knowledge_refs','参照ナレッジID・版・ハッシュ'),('research_source_ids','参照調査ID（未確認を含む）'),('content_revisions','原稿の修正履歴'),('costs','料金の記帳（空なら未計測）'),('errors','エラー'),('improvements','同種業務の改善履歴')]:
            body+='<h3>'+title+'</h3><pre>'+e(row[key])+'</pre>'
        body+='</details>'
    return body or '<p>履歴がありません。</p>'
