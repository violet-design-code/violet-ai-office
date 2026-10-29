"""ISOLATED synthetic D prototype. No Office hook, CLI, network or live DB input.

Create a SyntheticRealm (TemporaryDirectory) explicitly. MockAuthority is a file-
backed fake, NOT an independent trusted service. HMAC keys/representative tokens
are fixture capabilities, NOT Windows authentication or asymmetric signatures.
"""
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
from uuid import uuid4

from .accounting_access import FIELDS, ROLES


class Denied(PermissionError):
    """Fixed non-sensitive failure for prototype callers."""


class Unavailable(Denied):
    """Persistence/transport failure; control changes latch the realm closed."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def mac(key, value):
    return hmac.new(bytes.fromhex(key), canonical(value).encode(), hashlib.sha256).hexdigest()


def record(key, previous, event):
    body = {'sequence':len(previous)+1, 'previous':digest(previous[-1]) if previous else '', 'event':event}
    return {**body, 'mac':mac(key, body)}


def verify(records, key):
    prior = ''
    for number, item in enumerate(records, 1):
        body = {k:v for k,v in item.items() if k != 'mac'}
        if (set(item) != {'sequence','previous','event','mac'} or item['sequence'] != number
                or item['previous'] != prior or not hmac.compare_digest(item['mac'], mac(key,body))):
            raise Denied('Synthetic history verification failed')
        prior = digest(item)
    return [len(records), prior]


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise Denied('Explicit timezone required')
    return parsed.astimezone(timezone.utc)


def valid_period(start, end):
    try:
        return (isinstance(start,str) and isinstance(end,str)
                and date.fromisoformat(start).isoformat() == start
                and date.fromisoformat(end).isoformat() == end and start < end)
    except (ValueError,TypeError):
        return False


@dataclass(frozen=True)
class Scope:
    business: str
    agent: str
    task: str
    section: str
    fields: tuple
    start: str
    end: str
    purpose_id: str

    def validate(self):
        if (not all(isinstance(v,str) and 0 < len(v) <= 100 for v in
                    (self.business,self.agent,self.task,self.section,self.purpose_id))
                or self.section not in ROLES.get(self.agent,set())
                or not isinstance(self.fields,tuple) or not self.fields
                or not all(isinstance(f,str) for f in self.fields)
                or len(set(self.fields)) != len(self.fields)
                or not set(self.fields) <= FIELDS[self.section]
                or not valid_period(self.start,self.end)):
            raise Denied('Synthetic scope denied')

    def data(self):
        self.validate()
        body = asdict(self); body['fields'] = sorted(self.fields)
        return body

    @classmethod
    def load(cls, data):
        return cls(**{**data,'fields':tuple(data['fields'])})


def covered(approved, requested):
    a, r = Scope.load(approved), Scope.load(requested)
    a.validate(); r.validate()
    if (any(getattr(a,k) != getattr(r,k) for k in ('business','agent','task','section','purpose_id'))
            or not set(r.fields) <= set(a.fields) or not a.start <= r.start < r.end <= a.end):
        raise Denied('Synthetic scope denied')


@dataclass(frozen=True)
class Context:
    root: str
    instance: str
    local_key: str
    external_key: str

    def validate(self):
        root = Path(self.root)
        if (root.is_symlink() or root.resolve().parent != Path(tempfile.gettempdir()).resolve()
                or not root.name.startswith('violet-d-synthetic-')
                or (root/'SYNTHETIC_ONLY').is_symlink()
                or (root/'SYNTHETIC_ONLY').read_text() != self.instance):
            raise Denied('Synthetic temporary realm required')
        for child in ('local','mock-external','outputs'):
            if (root/child).is_symlink():
                raise Denied('Synthetic temporary realm required')

    @property
    def local(self):
        return Path(self.root)/'local'/'synthetic-ledger.sqlite3'

    @property
    def external(self):
        return Path(self.root)/'mock-external'/'mock-state.sqlite3'


SCHEMA = 'CREATE TABLE journal(sequence INTEGER PRIMARY KEY, body TEXT NOT NULL)'


def create_database(path, local_key=None):
    with path.open('xb'):
        pass
    with closing(sqlite3.connect(path, isolation_level=None)) as db:
        db.execute(SCHEMA)
        if local_key is not None:
            db.execute('CREATE TABLE authority_checkpoint(id INTEGER PRIMARY KEY, body TEXT NOT NULL)')
            body = {'head':[0,'']}
            db.execute('INSERT INTO authority_checkpoint VALUES(1,?)',(canonical({'body':body,'mac':mac(local_key,body)}),))


@contextmanager
def database(ctx, path):
    try:
        ctx.validate()
        if path.is_symlink():
            raise Denied('Synthetic database unavailable')
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=rw', uri=True,
                                     isolation_level=None, timeout=5)) as db:
            db.execute('PRAGMA trusted_schema=OFF')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            try:
                objects = db.execute("SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
                columns = db.execute('PRAGMA table_info(journal)').fetchall()
                expected = [('table','authority_checkpoint'),('table','journal')] if path == ctx.local else [('table','journal')]
                if (objects != expected or [(r[1],r[2],r[3],r[5]) for r in columns]
                        != [('sequence','INTEGER',0,1),('body','TEXT',1,0)]
                        or db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]):
                    raise Denied('Synthetic database verification failed')
                yield db
                db.execute('COMMIT')
            except BaseException:
                db.execute('ROLLBACK')
                raise
    except Denied:
        raise
    except (sqlite3.Error,OSError,ValueError,TypeError,KeyError,OverflowError,IndexError,AttributeError,RecursionError):
        raise Unavailable('Synthetic recovery service unavailable') from None


def read_records(db, key):
    rows = db.execute('SELECT sequence,body FROM journal ORDER BY sequence').fetchall()
    result = []
    for seq, raw in rows:
        item = json.loads(raw)
        if item['sequence'] != seq:
            raise Denied('Synthetic history verification failed')
        result.append(item)
    verify(result,key)
    return result


def insert_record(db, item):
    db.execute('INSERT INTO journal VALUES(?,?)', (item['sequence'],canonical(item)))


class SyntheticRealm:
    """Owns NEW disposable files only; fixtures may pass Context to child processes."""
    def __init__(self):
        self._temp = tempfile.TemporaryDirectory(prefix='violet-d-synthetic-')
        root = Path(self._temp.name)
        self.context = Context(str(root),uuid4().hex,secrets.token_hex(32),secrets.token_hex(32))
        self.representative_token = secrets.token_urlsafe(32)
        (root/'SYNTHETIC_ONLY').write_text(self.context.instance)
        for child in ('local','mock-external','outputs'):
            (root/child).mkdir()
        create_database(self.context.local,self.context.local_key); create_database(self.context.external)
        state = {'instance':self.context.instance,'enrolled':False,'epoch':0,'mode':'closed',
                 'records':[],'safe_head':[0,''],'policies':{},'permits':{},'references':{},
                 'admin_hash':hashlib.sha256(self.representative_token.encode()).hexdigest()}
        with database(self.context,self.context.external) as db:
            insert_record(db,record(self.context.external_key,[],{'kind':'provision_mock','state':state}))

    def client(self):
        return LocalLedger(self.context)

    def close(self):
        self._temp.cleanup()


class MockAuthority:
    """Separate persisted state. Both this file AND its key/registry are mock trust."""
    def __init__(self, context):
        self.context = context

    def _op(self, kind, fn, write=False):
        with database(self.context,self.context.external) as db:
            records = read_records(db,self.context.external_key)
            if not records:
                raise Denied('Synthetic authority registration unavailable')
            state = json.loads(canonical(records[-1]['event']['state']))
            if state['instance'] != self.context.instance:
                raise Denied('Synthetic authority instance mismatch')
            result = fn(state)
            if write:
                insert_record(db,record(self.context.external_key,records,{'kind':kind,'state':state}))
            return result

    def attest(self, nonce, checkpoint=(0,'')):
        with database(self.context,self.context.external) as db:
            history = read_records(db,self.context.external_key)
            if not history:
                raise Denied('Synthetic authority registration unavailable')
            s = history[-1]['event']['state']
            number = checkpoint[0]
            ancestor = [0,''] if number == 0 else ([number,digest(history[number-1])] if number <= len(history) else None)
            body = {'instance':s['instance'],'nonce':nonce,'head':verify(s['records'],self.context.local_key),
                    'safe_head':s['safe_head'],'epoch':s['epoch'],'mode':s['mode'],'enrolled':s['enrolled'],
                    'transport_head':verify(history,self.context.external_key),'ancestor':ancestor}
            return {'body':body,'mac':mac(self.context.external_key,body)}

    @staticmethod
    def _admin(s, token):
        if not isinstance(token,str) or not hmac.compare_digest(s['admin_hash'],hashlib.sha256(token.encode()).hexdigest()):
            raise Denied('Synthetic representative proof required')

    @staticmethod
    def _no_permits(s, policy=None):
        if any(policy is None or policy in p['policies'] for p in s['permits'].values()):
            raise Denied('Synthetic output remains unresolved')

    def apply(self, item, expected, representative):
        def op(s):
            self._admin(s,representative)
            if verify(s['records'],self.context.local_key) != expected or s['safe_head'] != expected:
                raise Denied('Synthetic histories are not aligned')
            verify(s['records']+[item],self.context.local_key)
            e = item['event']; kind = e['kind']
            if kind == 'enroll':
                if s['enrolled'] or s['records']:
                    raise Denied('Synthetic realm already registered')
                s['enrolled'] = True; s['epoch'] = 1
                for policy in e['legacy_ids']:
                    s['policies'][policy] = {'status':'quarantined','epoch':0}
            elif not s['enrolled']:
                raise Denied('Synthetic realm not registered')
            elif kind == 'open':
                if s['mode'] != 'closed' or e['history_confirmed'] is not True:
                    raise Denied('Synthetic recovery review required')
                s['mode'] = 'active'
            elif kind == 'propose':
                if s['mode'] not in ('closed','active') or e['policy'] in s['policies']:
                    raise Denied('New synthetic policy required')
                Scope.load(e['scope']).validate()
                if timestamp(e['expires']) <= datetime.now(timezone.utc):
                    raise Denied('Synthetic policy expired')
                s['policies'][e['policy']] = {'scope':e['scope'],'expires':e['expires'],
                                             'epoch':s['epoch'],'status':'pending'}
            elif kind == 'approve':
                grant = s['policies'].get(e['policy'])
                if (s['mode'] not in ('closed','active') or not grant or grant['status'] != 'pending'
                        or grant['epoch'] != s['epoch'] or timestamp(grant['expires']) <= datetime.now(timezone.utc)):
                    raise Denied('Current pending synthetic policy required')
                grant['status'] = 'approved'
            elif kind == 'revoke':
                grant = s['policies'].get(e['policy'])
                if not grant or grant['status'] not in ('pending','approved'):
                    raise Denied('Synthetic policy is not revocable')
                grant['status'] = 'revoked'
            elif kind == 'complete_revoke':
                if s['policies'].get(e['policy'],{}).get('status') != 'revoked':
                    raise Denied('Synthetic revocation required')
                self._no_permits(s,e['policy'])
            elif kind == 'stop':
                if s['mode'] not in ('active','closed','draining'):
                    raise Denied('Synthetic stop unavailable')
                s['mode'] = 'draining'
            elif kind == 'complete_stop':
                if s['mode'] != 'draining':
                    raise Denied('Synthetic stop intent required')
                self._no_permits(s); s['mode'] = 'closed'
            elif kind == 'recover':
                if s['mode'] != 'closed' or e['history_confirmed'] is not True:
                    raise Denied('Synthetic recovery reconciliation required')
                self._no_permits(s); s['epoch'] += 1
                for grant in s['policies'].values():
                    if grant['status'] in ('pending','approved'):
                        grant['status'] = 'quarantined'
            else:
                raise Denied('Unknown synthetic operation')
            s['records'].append(item); s['safe_head'] = None
        self._op('domain_update',op,write=True)

    def confirm(self, head):
        def op(s):
            if verify(s['records'],self.context.local_key) != head or s['safe_head'] is not None:
                raise Denied('Synthetic confirmation mismatch')
            s['safe_head'] = head
        self._op('paired_confirmation',op,write=True)

    def _current(self, s, head):
        if not s['enrolled'] or verify(s['records'],self.context.local_key) != head or s['safe_head'] != head:
            raise Denied('Synthetic latest state unavailable')

    @staticmethod
    def _grant(s, policy, scope):
        Scope.load(scope).validate()
        grant = s['policies'].get(policy)
        if (s['mode'] != 'active' or not grant or grant['status'] != 'approved'
                or grant['epoch'] != s['epoch'] or timestamp(grant['expires']) <= datetime.now(timezone.utc)):
            raise Denied('Synthetic accounting approval unavailable')
        covered(grant['scope'],scope)

    @staticmethod
    def _guards(guards):
        if not isinstance(guards,(list,tuple)) or not 1 <= len(guards) <= 2:
            raise Denied('Explicit synthetic accounting scopes required')
        for policy, scope in guards:
            if not isinstance(policy,str) or not policy:
                raise Denied('Explicit synthetic policy required')
            Scope.load(scope).validate()

    def authorize(self, head, guards):
        self._guards(guards)
        def op(s):
            self._current(s,head)
            for policy,scope in guards:
                self._grant(s,policy,scope)
            return s['epoch']
        return self._op('authorize',op)

    def reserve(self, head, guards, owner, kind):
        self._guards(guards)
        if kind not in ('http','artifact','handoff'):
            raise Denied('Known synthetic output required')
        def op(s):
            self._current(s,head)
            for policy,scope in guards:
                self._grant(s,policy,scope)
            token, proof = uuid4().hex, secrets.token_hex(32)
            s['permits'][token] = {'owner':owner,'proof_hash':hashlib.sha256(proof.encode()).hexdigest(),
                                    'policies':sorted({p for p,_ in guards}),'epoch':s['epoch'],
                                    'kind':kind,'phase':'reserved'}
            return token,proof
        return self._op('reserve_delivery',op,write=True)

    @staticmethod
    def _permit(s, token, proof, owner):
        p = s['permits'].get(token)
        if (not p or p['owner'] != owner or not hmac.compare_digest(p['proof_hash'],hashlib.sha256(proof.encode()).hexdigest())):
            raise Denied('Synthetic delivery capability unavailable')
        return p

    def begin(self, head, token, proof, owner):
        def op(s):
            self._current(s,head)
            p = self._permit(s,token,proof,owner)
            if p['phase'] != 'reserved':
                raise Denied('Synthetic delivery already started')
            p['phase'] = 'emitting'
        self._op('begin_delivery',op,write=True)

    def mark_unresolved(self, token, proof, owner):
        def op(s):
            p = self._permit(s,token,proof,owner)
            if p['phase'] not in ('reserved','emitting','unresolved'):
                raise Denied('Synthetic delivery state unavailable')
            p['phase'] = 'unresolved'
            # Fail closed for new output as well. Repeated stop intents remain safe.
            s['mode'] = 'draining'
        # Fixed action and existing identifiers only; never persist exception text.
        self._op('delivery_unresolved',op,write=True)

    def finish(self, token, proof, owner, outcome):
        def op(s):
            p = self._permit(s,token,proof,owner)
            # No generic aborted/closed flag can prove that a buffer cannot flush.
            # The prototype deliberately has NO release API for unresolved output.
            if outcome != 'handed_off' or p['phase'] != 'emitting':
                raise Denied('Confirmed successful synthetic handoff required')
            del s['permits'][token]
        self._op('finish_handed_off',op,write=True)

    def reference(self, head, policy, scope):
        def op(s):
            self._current(s,head); self._grant(s,policy,scope)
            identifier = uuid4().hex
            s['references'][identifier] = {'policy':policy,'scope':scope,'epoch':s['epoch']}
            return identifier
        return self._op('reference_issued',op,write=True)

    def resolve(self, head, identifier):
        def op(s):
            self._current(s,head)
            r = s['references'].get(identifier)
            if not r or r['epoch'] != s['epoch']:
                raise Denied('Synthetic reference unavailable')
            self._grant(s,r['policy'],r['scope'])
            return r
        return self._op('reference_resolved',op)


class LocalLedger:
    def __init__(self, context, authority=None):
        self.context = context
        self.authority = authority or MockAuthority(context)

    def _aligned(self, db, records, enrolled=True):
        fault = Path(self.context.root)/'FAULT'
        if fault.exists() or fault.is_symlink():
            raise Denied('Synthetic realm requires fault review')
        head = verify(records,self.context.local_key)
        rows = db.execute('SELECT id,body FROM authority_checkpoint').fetchall()
        if len(rows) != 1 or rows[0][0] != 1:
            raise Denied('Synthetic authority checkpoint unavailable')
        saved = json.loads(rows[0][1]); checkpoint = saved['body']['head']
        if not hmac.compare_digest(saved['mac'],mac(self.context.local_key,saved['body'])):
            raise Denied('Synthetic authority checkpoint invalid')
        nonce = secrets.token_hex(32)
        response = self.authority.attest(nonce,checkpoint); body = response['body']
        if (not hmac.compare_digest(response['mac'],mac(self.context.external_key,body))
                or body['nonce'] != nonce or body['instance'] != self.context.instance
                or body['head'] != head or body['safe_head'] != head
                or body['ancestor'] != checkpoint or body['transport_head'][0] < checkpoint[0]
                or (enrolled and not body['enrolled'])):
            raise Denied('Synthetic latest checkpoint mismatch')
        new = {'head':body['transport_head']}
        db.execute('UPDATE authority_checkpoint SET body=? WHERE id=1',
                   (canonical({'body':new,'mac':mac(self.context.local_key,new)}),))
        return head,body

    def status(self):
        with database(self.context,self.context.local) as db:
            _,body = self._aligned(db,read_records(db,self.context.local_key))
            return {k:body[k] for k in ('head','epoch','mode')}

    def _change(self, representative, kind, **args):
        try:
            return self._change_locked(representative,kind,**args)
        except Unavailable:
            # Auxiliary durable deny latch, never an authorization/trust anchor.
            # No reset API: a failed prototype realm is retained for inspection.
            self.context.validate()
            try:
                with (Path(self.context.root)/'FAULT').open('x') as marker:
                    marker.flush(); os.fsync(marker.fileno())
            except FileExistsError:
                pass
            except OSError:
                raise Unavailable('Synthetic control failed; durable halt could not be recorded') from None
            raise

    def _change_locked(self, representative, kind, **args):
        with database(self.context,self.context.local) as db:
            records = read_records(db,self.context.local_key)
            head,_ = self._aligned(db,records,enrolled=kind != 'enroll')
            item = record(self.context.local_key,records,{'kind':kind,'args_version':1,**args})
            # External-first commits are deliberate: failure afterward => mismatch/deny.
            self.authority.apply(item,head,representative)
            insert_record(db,item)
            # Hold the local writer lock through acknowledgement. A failed local
            # COMMIT still leaves old-local/new-external mismatch and denies.
            self.authority.confirm(verify(records+[item],self.context.local_key))
            self._aligned(db,records+[item])

    def enroll(self, representative, legacy_ids=()):
        self._change(representative,'enroll',legacy_ids=list(legacy_ids))

    def open_after_review(self, representative, *, history_confirmed):
        self._change(representative,'open',history_confirmed=history_confirmed)

    def propose(self, representative, policy, scope, expires):
        self._change(representative,'propose',policy=policy,scope=scope.data(),expires=expires)

    def approve(self, representative, policy):
        self._change(representative,'approve',policy=policy)

    def revoke(self, representative, policy):
        self._change(representative,'revoke',policy=policy)

    def complete_revocation(self, representative, policy):
        self._change(representative,'complete_revoke',policy=policy)

    def request_stop(self, representative):
        self._change(representative,'stop')

    def complete_stop(self, representative):
        self._change(representative,'complete_stop')

    def new_recovery_generation(self, representative, *, history_confirmed):
        self._change(representative,'recover',history_confirmed=history_confirmed)

    def checked(self, operation):
        with database(self.context,self.context.local) as db:
            head,_ = self._aligned(db,read_records(db,self.context.local_key))
            result = operation(head)
            self._aligned(db,read_records(db,self.context.local_key))
            return result

    def authorize(self, guards):
        return self.checked(lambda h:self.authority.authorize(h,guards))

    def issue_reference(self, policy, scope):
        return self.checked(lambda h:self.authority.reference(h,policy,scope.data()))


class HttpSink:
    """Synthetic stream adapter; never opens a socket/server itself."""
    kind = 'http'

    def __init__(self, stream):
        self.stream = stream

    def send(self, payload):
        headers = (b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nCache-Control: no-store\r\n'
                   + b'Connection: close\r\nContent-Length: '+str(len(payload)).encode()+b'\r\n\r\n')
        write_all(self.stream,headers+payload)
        self.stream.flush()


class BufferSink:
    """Synthetic artifact/handoff destination, deliberately not a production store."""
    def __init__(self, stream, kind='artifact'):
        if kind not in ('artifact','handoff'):
            raise Denied('Known synthetic output required')
        self.kind,self.stream = kind,stream

    def send(self, payload):
        write_all(self.stream,payload); self.stream.flush()


def write_all(stream, payload):
    position = 0
    while position < len(payload):
        count = stream.write(payload[position:])
        if type(count) is not int or not 0 < count <= len(payload)-position:
            raise Denied('Synthetic output failed')
        position += count


def project(scope, data):
    rows = data if isinstance(data,list) else [data]
    for row in rows:
        if not isinstance(row,dict) or not row or not set(row) <= set(scope.fields):
            raise Denied('Synthetic data projection denied')
        for key,value in row.items():
            if key == 'day':
                if not isinstance(value,str) or not scope.start <= date.fromisoformat(value).isoformat() < scope.end:
                    raise Denied('Synthetic data period denied')
            elif key == 'kind':
                if value not in ('revenue','expense'):
                    raise Denied('Synthetic data classification denied')
            elif value is not None and (type(value) not in (int,float) or not math.isfinite(value) or abs(value) >= 10**12):
                raise Denied('Synthetic data field denied')
    payload = canonical(data).encode()
    if len(payload) > 65536:
        raise Denied('Synthetic output limit exceeded')
    return payload


class DeliveryBroker:
    """Isolated broker. Trusted callbacks/sinks ONLY; never return raw values."""
    def __init__(self, client, normal_authorize):
        self.client,self.normal_authorize = client,normal_authorize
        self.owner = str(os.getpid())+':'+uuid4().hex

    def _normal(self, guards):
        for policy, data in guards:
            scope = Scope.load(data); scope.validate()
            try:
                allowed = self.normal_authorize(policy,scope)
            except Exception:
                raise Denied('Ordinary synthetic authorization unavailable') from None
            if allowed is not True:
                raise Denied('Ordinary synthetic authorization denied')

    def deliver(self, policy, scope, fetch, sink, *, source_guard=None):
        scope.validate()
        guards = ([source_guard] if source_guard else [])+[(policy,scope.data())]
        self._normal(guards); self.client.authorize(guards)
        try:
            payload = project(scope,fetch(scope))
        except Exception:
            raise Denied('Synthetic data retrieval or projection denied') from None
        self._normal(guards)
        if not isinstance(sink,(HttpSink,BufferSink)):
            raise Denied('Trusted synthetic sink required')
        token,proof = self.client.checked(lambda h:self.client.authority.reserve(h,guards,self.owner,sink.kind))
        try:
            self.client.checked(lambda h:self.client.authority.begin(h,token,proof,self.owner))
            sink.send(payload)
            # Only a successful synchronous send AND flush may release a permit.
            self.client.checked(lambda h:self.client.authority.finish(token,proof,self.owner,'handed_off'))
        except BaseException as failure:
            try:
                self.client.checked(lambda h:self.client.authority.mark_unresolved(token,proof,self.owner))
            except Exception:
                # An unavailable audit/authority cannot prove cancellation. Keep
                # the already persisted reservation; never finish it in cleanup.
                pass
            if not isinstance(failure,Exception):
                raise
            raise Denied('Synthetic output unresolved; handoff not confirmed') from None
        return {'delivery_id':token,'outcome':'handed_off'}

    def issue_reference(self, policy, scope):
        self._normal([(policy,scope.data())])
        return self.client.issue_reference(policy,scope)

    def receive_reference(self, identifier, target_policy, target_scope, fetch, sink):
        source = self.client.checked(lambda h:self.client.authority.resolve(h,identifier))
        origin = Scope.load(source['scope']); target_scope.validate()
        if (origin.business != target_scope.business or origin.section != target_scope.section
                or origin.purpose_id != target_scope.purpose_id
                or not set(target_scope.fields) <= set(origin.fields)
                or not origin.start <= target_scope.start < target_scope.end <= origin.end):
            raise Denied('Synthetic reference scope denied')
        return self.deliver(target_policy,target_scope,fetch,sink,source_guard=(source['policy'],source['scope']))
