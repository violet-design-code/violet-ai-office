"""Offline template content pipeline. No network, LLM, sender or publisher."""
import hashlib
import html
import json
import re
from datetime import date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from .store import now

SAMPLE_BRIEF = 'バイオレットの認知を広げ、ベネシードディーラーに関心を持つ人との接点を増やすため、40代〜60代向けのInstagram投稿を7日分作成し、LINE登録から面談予約につながる導線を提案してください'
SOURCES = [
    {'name':'消費者庁 特定商取引法ガイド','url':'https://www.no-trouble.caa.go.jp/','status':'未取得・要確認'},
    {'name':'消費者庁','url':'https://www.caa.go.jp/','status':'未取得・要確認'},
    {'name':'個人情報保護委員会','url':'https://www.ppc.go.jp/','status':'未取得・要確認'},
    {'name':'厚生労働省','url':'https://www.mhlw.go.jp/','status':'未取得・要確認'},
    {'name':'ベネシード公式規約','url':None,'status':'代表から正規の最新版提供が必要'},
]
CHECKS = ['事実・出典','誤字脱字・媒体表現','特定商取引法','景品表示法','薬機法・健康増進法','個人情報・同意','ベネシード公式規約','リンク・予約導線','LINE利用規約','SNS利用規約']
SCHEMA = '''
CREATE TABLE IF NOT EXISTS campaigns (
 id TEXT PRIMARY KEY, campaign_key TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
 brief TEXT NOT NULL, days INTEGER NOT NULL, platform TEXT NOT NULL, start_date TEXT NOT NULL,
 mode TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS content_versions (
 id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id), logical_key TEXT NOT NULL,
 version INTEGER NOT NULL, kind TEXT NOT NULL, scheduled_date TEXT, body TEXT NOT NULL,
 digest TEXT NOT NULL, status TEXT NOT NULL, task_id TEXT REFERENCES tasks(id),
 UNIQUE(campaign_id,logical_key,version));
CREATE TABLE IF NOT EXISTS content_events (
 id INTEGER PRIMARY KEY, content_id TEXT NOT NULL REFERENCES content_versions(id),
 event TEXT NOT NULL, actor TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS content_audits (
 content_id TEXT PRIMARY KEY REFERENCES content_versions(id), digest TEXT NOT NULL,
 findings TEXT NOT NULL, blockers TEXT NOT NULL, checks TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS campaign_work (
 campaign_id TEXT NOT NULL REFERENCES campaigns(id), agent TEXT NOT NULL,
 task_id TEXT NOT NULL REFERENCES tasks(id), PRIMARY KEY(campaign_id,agent));
CREATE TABLE IF NOT EXISTS research_sources (
 id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id), url TEXT NOT NULL,
 excerpt TEXT NOT NULL, classification TEXT NOT NULL, evidence_ref TEXT NOT NULL, recorded_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS preference_events (
 id INTEGER PRIMARY KEY, subject_ref TEXT NOT NULL, purpose TEXT NOT NULL,
 consent INTEGER NOT NULL, stopped INTEGER NOT NULL, booked INTEGER NOT NULL,
 evidence_ref TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pipeline_errors (
 id INTEGER PRIMARY KEY, command TEXT NOT NULL, code TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audience_preferences (
 subject_ref TEXT NOT NULL, purpose TEXT NOT NULL, consent INTEGER NOT NULL,
 stopped INTEGER NOT NULL, booked INTEGER NOT NULL, evidence_ref TEXT NOT NULL,
 updated_at TEXT NOT NULL, PRIMARY KEY(subject_ref,purpose));
'''


def encode(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False)


def digest(body):
    return hashlib.sha256(encode(body).encode()).hexdigest()


def safe_input(text):
    # Conservative input guard, not a general PII detector. Synthetic briefs only.
    if not isinstance(text,str) or not text.strip() or len(text)>10000:
        raise ValueError('Invalid text')
    if re.search(r'(?i)[\"\']?(?:api[_-]?key|access[_-]?token|channel[_-]?secret|password)[\"\']?\s*[:=]\s*[\"\']?[A-Za-z0-9_-]{6,}|-----BEGIN [A-Z ]*PRIVATE KEY-----',text):
        raise ValueError('Do not enter credentials')
    if re.search(r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}|(?:sk-|Bearer\s+)[A-Za-z0-9_-]+|0\d{1,3}[- ]?\d{2,4}[- ]?\d{4}',text):
        raise ValueError('Do not enter personal information or credentials')


class ContentOffice:
    def __init__(self, store):
        self.store=store
        self.db=store.db
        self.db.executescript(SCHEMA)

    def _event(self, content, event, actor, detail=''):
        self.db.execute('INSERT INTO content_events(content_id,event,actor,detail,created_at) VALUES (?,?,?,?,?)',
                        (content,event,actor,detail,now().isoformat()))

    def _insert(self, campaign, key, kind, body, scheduled=None, version=1, task=None):
        content=uuid4().hex
        self.db.execute('INSERT INTO content_versions VALUES (?,?,?,?,?,?,?,?,?,?)',
                        (content,campaign,key,version,kind,scheduled,encode(body),digest(body),'draft',task))
        self._event(content,'draft','template_engine_v1')
        return content

    def _work(self, campaign, agent, output):
        task=self.store.create_task(f'campaign:{campaign}:{agent}',agent,{'campaign_id':campaign,'mode':'offline_template_v1'},action='customer_contact')
        artifact=self.store.finish(self.store.claim(task),encode(output))
        # Do not impersonate a human reviewer or record a legal pass.
        self.db.execute('INSERT INTO campaign_work VALUES (?,?,?)',(campaign,agent,task))
        return task,artifact

    def generate(self, key, brief, days=7, platform='Instagram', start_date=None):
        safe_input(brief)
        if not isinstance(key,str) or not key.strip() or len(key)>200 or type(days) is not int or days not in (7,14,30) or platform not in ('Instagram','Facebook','LINE'):
            raise ValueError('Invalid campaign settings')
        start=date.fromisoformat(start_date) if start_date else now().astimezone(ZoneInfo('Asia/Tokyo')).date()
        fingerprint=digest([brief,days,platform,start.isoformat()])
        def op():
            old=self.db.execute('SELECT * FROM campaigns WHERE campaign_key=?',(key,)).fetchone()
            if old:
                if old['fingerprint']!=fingerprint:
                    raise ValueError('Campaign key conflict')
                return old['id']
            campaign=uuid4().hex
            self.db.execute('INSERT INTO campaigns VALUES (?,?,?,?,?,?,?,?,?)',
                (campaign,key,fingerprint,brief,days,platform,start.isoformat(),'offline_template_v1',now().isoformat()))
            research={'confirmed_facts':[], 'hypotheses':['健康・美容・暮らし・働き方への関心は人により異なる。今回の企画仮説であり市場調査結果ではない。'],
                'sources':SOURCES,'method':'ネットワーク取得なし。公開情報の参照候補と検証計画のみ。無断スクレイピングなし。'}
            self._work(campaign,'AI03',research)
            themes=['暮らしで大切にしたいこと','自分のペースの健康習慣','美容について知りたいこと','人とのつながり','新しい働き方の情報整理','バイオレットの活動紹介','希望する方への相談のご案内']
            plan=[{'day':i+1,'theme':themes[i%7],'date':(start+timedelta(days=i)).isoformat()} for i in range(days)]
            self._work(campaign,'AI08',{'plan':plan,'audience':'40〜60代を主な想定とする成人。属性から希望を断定しない。'})
            task,_=self._work(campaign,'AI09',{'method':'以下の原稿は固定テンプレートを媒体別に調整。生成AI未使用。','count':days})
            for item in plan:
                title=item['theme']
                body=f'バイオレットから、{title}について考えるきっかけをお届けします。関心や生活のペースは一人ひとり異なります。ご自身が知りたいことを大切にしてください。'
                if platform=='Facebook':
                    body+=' 私たちがどのような情報をお届けすると役立つか、ご希望のある方はお聞かせください。'
                elif platform=='LINE':
                    body='【バイオレット】'+body+' 配信停止をご希望の場合は停止方法をご確認ください（実際の手順は既存LINE側で要確認）。'
                cta='関心のある成人の方へ。プロフィールの公式LINE案内（登録先は代表確認後に設定）から、希望する情報と利用目的を確認のうえ任意で登録できます。面談は希望者のみ候補日時をご相談ください。'
                if item['day']%7==6:
                    body+=' ベネシードディーラーについて知りたい方向けの案内を準備しています。具体的な条件・費用・制度は公式資料の確認後にご説明します。'
                self._insert(campaign,f'post-{item["day"]}','sns',{'title':title,'body':body,'hashtags':['#バイオレット','#暮らしを考える','#人とのつながり'] if platform!='LINE' else [],'cta':cta,'platform':platform,'day':item['day'],'disclosure':'事業活動の案内・下書き'},item['date'],task=task)
            visual={'image':'落ち着いた紫を基調に、暮らしの風景と読みやすい見出しを配置。人物写真は許諾済み素材のみ。効能や収益の図示、ビフォーアフターは禁止。',
                'video':'15〜30秒。問いかけ→日常の風景→任意の情報登録案内。字幕を付け、健康効果・成功実績を捏造しない。','generation_api':'未接続・制作指示書のみ'}
            task,_=self._work(campaign,'AI10',visual)
            self._insert(campaign,'creative','creative',visual,task=task)
            referral={
                '初めて知る方へ':'バイオレットでは、ベネシードディーラーについて関心をお持ちの成人の方向けに情報案内を準備しています。契約条件・費用・制度は公式資料で確認してから判断してください。',
                '知人への紹介依頼':'もし関心がありそうな成人のお知り合いがいて、ご自身が紹介したいと感じた場合のみ、案内文をご共有いただけます。相手の連絡先をこちらへ渡す必要はありません。',
                '第三者へ渡す案内':'バイオレットの活動について、知りたい場合だけ公式案内をご確認ください。登録や面談は任意です。紹介者から個人情報は受け取りません。',
                '興味を示した方への返信':'ご関心をお寄せいただきありがとうございます。どのような情報をご希望でしょうか。ご希望の範囲で、確認済みの資料からご案内します。',
                '面談導線':'案内を読む→利用目的と同意を確認→本人がLINE登録→希望内容を選択→面談希望を確認→候補日時を調整→代表承認と本人確認後に予約確定。',
                '紹介ページ':'事業活動の案内／運営者情報・問い合わせ先（要設定）／目的・同意／公式資料／任意の登録／停止方法。公開先未設定。',
                '報酬・特典':'公式規約未確認のため金額・条件・特典の記載なし。'}
            task,_=self._work(campaign,'AI16',referral)
            self._insert(campaign,'referral','referral',referral,task=task)
            line=[]
            for i,theme in enumerate(themes,1):
                draft={'day':i,'purpose':'本人が希望する情報の提供','topic':theme,
                    'body':f'バイオレットです。今日は「{theme}」についての情報案内です。知りたい内容があれば、ご希望の範囲をお知らせください。',
                    'cta':'面談を希望する場合のみ候補日時をご相談ください。停止・再同意・予約確定時停止は既存システムの判断を優先します。',
                    'delivery':'送信禁止・既存7日間配信への取込未接続'}
                line.append(draft)
            task,_=self._work(campaign,'AI22',{'days':7,'integration':'既存システム未接続。既存DBの変更なし。'})
            for item in line:
                self._insert(campaign,f'line-{item["day"]}','line',item,task=task)
            # Rule audit actually executes, while legal/factual checks stay unresolved.
            ids=[r[0] for r in self.db.execute('SELECT id FROM content_versions WHERE campaign_id=?',(campaign,))]
            for content in ids:
                self.audit(content)
            self._work(campaign,'AI28',{'method':'自動ルール監査','checked':len(ids),'human_checks':CHECKS,'legal_compliance':'保証しない'})
            self._work(campaign,'AI01',{'summary':'原稿と出典候補を集約。全件代表承認待ち（要確認項目あり）。','content_ids':ids,'mode':'offline_template_v1'})
            return campaign
        return self.store._transaction(op)

    def _latest(self, content):
        row=self.db.execute('SELECT * FROM content_versions WHERE id=?',(content,)).fetchone()
        if not row:
            raise ValueError('Unknown content')
        latest=self.db.execute('SELECT MAX(version) FROM content_versions WHERE campaign_id=? AND logical_key=?',(row['campaign_id'],row['logical_key'])).fetchone()[0]
        if row['version']!=latest:
            raise ValueError('Superseded content')
        return row

    def audit(self, content):
        def op():
            row=self._latest(content)
            if row['status']!='draft':
                raise ValueError('Audit requires draft')
            text=row['body']
            blockers=[word for word in ('必ず儲かる','収入保証','絶対に稼げる','病気が治る','必ず痩せる','副作用なし','誰でも成功') if word in text]
            findings=['公開情報の本文取得・事実照合は未実施','公式規約・法令・誤字脱字は人による確認が必要','登録先・運営者情報・停止方法・予約先は要確認']
            self.db.execute('INSERT INTO content_audits VALUES (?,?,?,?,?)',(content,row['digest'],encode(findings),encode(blockers),encode({c:None for c in CHECKS})))
            self.db.execute("UPDATE content_versions SET status='quality_checked' WHERE id=?",(content,))
            self._event(content,'quality_checked','AI28:rules_v1',encode({'blockers':blockers,'findings':findings}))
            status='changes_requested' if blockers else 'approval_pending'
            self.db.execute('UPDATE content_versions SET status=? WHERE id=?',(status,content))
            self._event(content,status,'AI01:rules_v1')
        return self.store._transaction(op)

    def revise(self, content, body):
        if not isinstance(body,dict):
            raise ValueError('Structured body required')
        safe_input(encode(body))
        def op():
            row=self._latest(content)
            self.db.execute("UPDATE content_versions SET status='superseded' WHERE id=?",(content,))
            new=self._insert(row['campaign_id'],row['logical_key'],row['kind'],body,row['scheduled_date'],row['version']+1,row['task_id'])
            self._event(content,'superseded','representative',new)
            return new
        return self.store._transaction(op)

    def confirm_check(self, content, check, evidence, actor='representative'):
        safe_input(evidence)
        if actor!='representative' or check not in CHECKS:
            raise ValueError('Representative confirmation required')
        def op():
            row=self._latest(content)
            if row['status']!='approval_pending':
                raise ValueError('Not awaiting checks')
            audit=self.db.execute('SELECT * FROM content_audits WHERE content_id=?',(content,)).fetchone()
            checks=json.loads(audit['checks'])
            checks[check]=evidence
            self.db.execute('UPDATE content_audits SET checks=? WHERE content_id=?',(encode(checks),content))
            self._event(content,'check_confirmed',actor,encode({'check':check,'evidence':evidence}))
        return self.store._transaction(op)

    def verify_quality(self, content):
        row=self._latest(content)
        audit=self.db.execute('SELECT * FROM content_audits WHERE content_id=?',(content,)).fetchone()
        if not audit or audit['digest']!=row['digest'] or digest(json.loads(row['body']))!=row['digest'] or json.loads(audit['blockers']):
            raise ValueError('Unresolved audit checks')
        checks=json.loads(audit['checks'])
        if not all(isinstance(checks.get(key),str) and checks[key].strip() for key in CHECKS):
            raise ValueError('Current legal and platform checks required')
        return row

    def verify_approved(self, content):
        row=self.verify_quality(content)
        if row['status']!='approved':
            raise ValueError('Current approved content required')
        return row

    def decide(self, content, decision, actor):
        if actor!='representative' or decision not in ('approve','reject'):
            raise ValueError('Representative required')
        def op():
            row=self._latest(content)
            if row['status']!='approval_pending':
                raise ValueError('Quality review required')
            audit=self.db.execute('SELECT * FROM content_audits WHERE content_id=?',(content,)).fetchone()
            if decision=='approve':
                self.verify_quality(content)
            status='approved' if decision=='approve' else 'rejected'
            self.db.execute('UPDATE content_versions SET status=? WHERE id=?',(status,content))
            self._event(content,status,actor,row['digest'])
        return self.store._transaction(op)

    def record_publication(self, content, evidence, actor):
        # Only manual evidence recording; never makes an HTTP request.
        safe_input(evidence)
        def op():
            row=self._latest(content)
            if actor!='representative' or row['status']!='approved':
                raise ValueError('Approved content and representative required')
            self.verify_approved(content)
            self.db.execute("UPDATE content_versions SET status='published' WHERE id=?",(content,))
            self._event(content,'published_manual_record',actor,evidence)
        return self.store._transaction(op)

    def set_preference(self, subject_ref, purpose, consent, stopped, booked, evidence_ref):
        if not re.fullmatch(r'subject_[a-zA-Z0-9_-]{1,64}',subject_ref) or purpose not in ('健康','美容','事業紹介') or any(type(v) is not bool for v in (consent,stopped,booked)):
            raise ValueError('Pseudonymous reference and explicit preferences required')
        safe_input(evidence_ref)
        def op():
            self.db.execute('INSERT INTO preference_events(subject_ref,purpose,consent,stopped,booked,evidence_ref,created_at) VALUES (?,?,?,?,?,?,?)',(subject_ref,purpose,int(consent),int(stopped),int(booked),evidence_ref,now().isoformat()))
            self.db.execute('INSERT INTO audience_preferences VALUES (?,?,?,?,?,?,?) ON CONFLICT(subject_ref,purpose) DO UPDATE SET consent=excluded.consent,stopped=excluded.stopped,booked=excluded.booked,evidence_ref=excluded.evidence_ref,updated_at=excluded.updated_at',
            (subject_ref,purpose,int(consent),int(stopped),int(booked),evidence_ref,now().isoformat()))
        return self.store._transaction(op)

    def eligibility(self, subject_ref, purpose):
        row=self.db.execute('SELECT * FROM audience_preferences WHERE subject_ref=? AND purpose=?',(subject_ref,purpose)).fetchone()
        suppressed=self.db.execute('SELECT 1 FROM audience_preferences WHERE subject_ref=? AND (stopped=1 OR booked=1) LIMIT 1',(subject_ref,)).fetchone()
        return {'local_candidate':bool(row and row['consent'] and not suppressed),
            'send_allowed':False,'reason':'準備用のローカル選好のみ。既存LINE側の同意・停止・予約状態の再確認と承認・接続が必要。'}

    def report(self, campaign):
        row=self.db.execute('SELECT * FROM campaigns WHERE id=?',(campaign,)).fetchone()
        if not row:
            raise ValueError('Unknown campaign')
        items=[dict(r) for r in self.db.execute('SELECT * FROM content_versions WHERE campaign_id=? ORDER BY logical_key,version',(campaign,))]
        for item in items:
            item['body']=json.loads(item['body'])
        work=[dict(r) for r in self.db.execute('SELECT cw.agent,cw.task_id,t.status FROM campaign_work cw JOIN tasks t ON t.id=cw.task_id WHERE campaign_id=? ORDER BY cw.agent',(campaign,))]
        return {'campaign':dict(row),'contents':items,'work':work,'cost':{'actual_api_microusd':0,'mode':'offline_template_v1','paid_api':'未接続'},'sources':SOURCES,'recorded_research':[dict(r) for r in self.db.execute('SELECT * FROM research_sources WHERE campaign_id=?',(campaign,))]}

    def record_source(self, campaign, url, excerpt, classification, evidence_ref):
        from urllib.parse import urlparse
        if classification not in ('hypothesis','reported_fact') or urlparse(url).scheme!='https' or not urlparse(url).hostname or urlparse(url).username or urlparse(url).query:
            raise ValueError('Public HTTPS source without credentials or query required')
        safe_input(excerpt)
        safe_input(evidence_ref)
        source=uuid4().hex
        self.db.execute('INSERT INTO research_sources VALUES (?,?,?,?,?,?,?)',(source,campaign,url,excerpt,classification,evidence_ref,now().isoformat()))
        return source

    def dashboard(self):
        rows=self.db.execute('SELECT * FROM content_versions ORDER BY rowid DESC').fetchall()
        latest=[r for r in rows if r['status']!='superseded']
        sections={'今日の作成コンテンツ':[r for r in latest if datetime.fromisoformat(self.db.execute('SELECT created_at FROM campaigns WHERE id=?',(r['campaign_id'],)).fetchone()[0]).astimezone(ZoneInfo('Asia/Tokyo')).date()==now().astimezone(ZoneInfo('Asia/Tokyo')).date()],
            'SNS投稿予定':[r for r in latest if r['kind']=='sns'], '紹介用文章':[r for r in latest if r['kind']=='referral'],
            'LINE配信原稿':[r for r in latest if r['kind']=='line'],'承認待ち':[r for r in latest if r['status']=='approval_pending'],
            '公開済み（手動記録）':[r for r in latest if r['status']=='published'],'エラー・修正必要':[r for r in latest if r['status'] in ('changes_requested','rejected')]}
        parts=['<!doctype html><html lang="ja"><meta charset="utf-8"><title>バイオレット管理</title><h1>バイオレット 原稿管理</h1><p>ローカル閲覧用。テンプレート実行・有料API未接続・実利用料金0 USD。外部送信機能なし。承認操作はCLI。</p>']
        for title,items in sections.items():
            parts.append('<h2>'+html.escape(title)+f'（{len(items)}）</h2>')
            for r in items:
                parts.append('<details><summary>'+html.escape(f"{r['logical_key']} / {r['status']} / {r['scheduled_date'] or '未設定'} / {r['id']}")+'</summary><pre>'+html.escape(json.dumps(json.loads(r['body']),ensure_ascii=False,indent=2))+'</pre></details>')
        parts.append('<h2>生成処理エラー</h2><pre>'+html.escape(encode([dict(r) for r in self.db.execute('SELECT command,code,created_at FROM pipeline_errors')]))+'</pre>')
        errors=self.db.execute("SELECT task_id,error_code FROM runs WHERE status='failed'").fetchall()
        parts.append('<h2>実行エラー</h2><pre>'+html.escape(encode([dict(r) for r in errors]))+'</pre></html>')
        return ''.join(parts)
