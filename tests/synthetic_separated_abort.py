"""Separated abort SANDBOX ONLY. No Office hooks, Windows ACL/TPM or network.

Ed25519 signatures are real. Non-exportability/OS identities/resource inventory
are MODEL assumptions: arbitrary same-user Python or host administrators are
not isolated. Private keys exist only in the issuer child and have no export API.
Requires cryptography in the development environment; never silently skips it.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import multiprocessing
import os
import secrets
from uuid import uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from violet_office.synthetic_recovery import Denied, Unavailable, canonical, digest, verify, timestamp, project
from violet_office.synthetic_abort import VerifiedAuthority, VerifiedLedger, OwnedOutputSupervisor, OwnedJob, _worker, FACTS

MODEL = 'separated-ed25519-synthetic-v1'
PROTOCOL = 'violet-synthetic-abort-signature-v1'


def _send(connection, value):
    data=canonical(value).encode()
    if len(data)>16384: raise Denied('Synthetic issuer frame limit exceeded')
    connection.send_bytes(data)


def _receive(connection):
    try:
        value=json.loads(connection.recv_bytes(16384).decode())
        if not isinstance(value,dict): raise ValueError()
        return value
    except (ValueError,TypeError,UnicodeError,EOFError,OSError):
        raise Unavailable('Synthetic issuer frame unavailable') from None


def _public(key):
    return key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex()


def _issuer_process(connection, capability, boot):
    # No private key crosses this process boundary or is written to a file.
    key=Ed25519PrivateKey.generate(); key_id=uuid4().hex
    try:
        _send(connection,{'public':_public(key),'key_id':key_id,'boot':boot})
        sequence=0
        while True:
            message=_receive(connection)
            if (message.get('capability')!=capability or message.get('boot')!=boot
                    or message.get('sequence')!=sequence+1):
                _send(connection,{'error':'unauthenticated'}); continue
            sequence+=1
            command=message.get('command')
            if command=='shutdown': return
            if command=='crash': os._exit(43)
            if command=='lose_key':
                key=None; _send(connection,{'lost':True}); continue
            if command=='rotate':
                key=Ed25519PrivateKey.generate(); key_id=uuid4().hex
                _send(connection,{'public':_public(key),'key_id':key_id,'boot':boot}); continue
            if command!='issue' or key is None:
                _send(connection,{'error':'unavailable'}); continue
            body=message.get('body')
            # The channel's authenticated supervisor is trusted to inspect the
            # fixed job. This is NOT a kernel observer or arbitrary signing API.
            if (not isinstance(body,dict) or set(body)!={'protocol','intent','facts','observation_hash','prior_output'}
                    or body.get('protocol')!=PROTOCOL or set(body.get('facts',[]))!=FACTS
                    or body.get('prior_output')!='possible_or_unknown'
                    or body.get('intent',{}).get('managed',{}).get('issuer_boot')!=boot
                    or timestamp(body['intent']['expires'])<=datetime.now(timezone.utc)):
                _send(connection,{'error':'inspection_required'}); continue
            signed={'algorithm':'Ed25519','key_id':key_id,'issuer_boot':boot,'body':body}
            _send(connection,{**signed,'signature':key.sign(canonical(signed).encode()).hex()})
    except (Denied,OSError,KeyError,TypeError,ValueError):
        return
    finally:
        key=None
        connection.close()


@dataclass(frozen=True)
class MockAuthenticatedPeer:
    """Trusted bootstrap fixture, not a caller's username or OS authentication."""
    sid: str
    server: str = 'SYNTHETIC-ISSUER'
    authenticated: bool = True
    remote: bool = False


class IsolatedIssuer:
    def __init__(self, peer, timeout=2):
        if (peer.sid!='SUPERVISOR-SID' or not peer.authenticated or peer.remote
                or peer.server!='SYNTHETIC-ISSUER'):
            raise Denied('Authenticated synthetic supervisor required')
        self._peer=peer; self._owner_pid=os.getpid(); self.timeout=timeout
        self.boot=uuid4().hex; self._capability=secrets.token_hex(32); self._sequence=0
        ctx=multiprocessing.get_context('spawn'); self._channel,child=ctx.Pipe()
        self.process=ctx.Process(target=_issuer_process,args=(child,self._capability,self.boot))
        self.process.start(); child.close()
        self.registration=self._reply()

    def _reply(self):
        if not self._channel.poll(self.timeout): raise Unavailable('Synthetic issuer unavailable')
        result=_receive(self._channel)
        if 'error' in result: raise Denied('Synthetic issuer refused')
        return result

    def request(self, peer, command, **args):
        # Object identity + provisioned channel model an authenticated IPC binding.
        # PID is only an extra fixture guard, NOT a claim of OS peer authentication.
        if peer is not self._peer or os.getpid()!=self._owner_pid:
            raise Denied('Bound synthetic IPC endpoint required')
        if not self.process.is_alive(): raise Unavailable('Synthetic issuer unavailable')
        self._sequence+=1
        try:
            _send(self._channel,{'command':command,'capability':self._capability,
                                'boot':self.boot,'sequence':self._sequence,**args})
            result=self._reply()
            if command=='rotate': self.registration=result
            return result
        except (BrokenPipeError,EOFError,OSError):
            raise Unavailable('Synthetic issuer unavailable') from None

    def close(self):
        self._channel.close()
        self.process.join(0.1)
        if self.process.is_alive(): self.process.terminate(); self.process.join(2)


class SeparatedAuthority(VerifiedAuthority):
    def apply(self, item, expected, representative):
        kind=item['event']['kind']
        if kind in ('register_abort_supervisor','release_verified_abort'):
            raise Denied('Legacy synthetic HMAC release forbidden')
        if kind not in ('register_separated_issuer','rotate_separated_issuer','release_separated_abort'):
            return super().apply(item,expected,representative)
        def operation(state):
            if verify(state['records'],self.context.local_key)!=expected or state['safe_head']!=expected or not state['enrolled']:
                raise Denied('Synthetic histories are not aligned')
            verify(state['records']+[item],self.context.local_key)
            event=item['event']; result=None
            if kind=='release_separated_abort':
                result=self._release_separated(state,event['evidence'])
            else:
                self._admin(state,representative)
                reg=event['registration']
                if set(reg)!={'public','key_id','boot'} or len(bytes.fromhex(reg['public']))!=32:
                    raise Denied('Synthetic public key registration required')
                Ed25519PublicKey.from_public_bytes(bytes.fromhex(reg['public']))
                registry=state.setdefault('abort_supervisors',{})
                old=registry.get(event['identity'])
                if kind=='register_separated_issuer':
                    if old or state['mode'] not in ('active','closed'):
                        raise Denied('New synthetic issuer required')
                    registry[event['identity']]={'boot':event['supervisor_boot'],'active':True,
                                                 'registration':reg,'signature_model':MODEL}
                else:
                    if not old or old.get('signature_model')!=MODEL or not old['active']:
                        raise Denied('Active synthetic issuer required')
                    if old['registration']['boot']!=reg['boot'] or old['registration']['key_id']==reg['key_id']:
                        raise Denied('Fresh same-issuer synthetic key required')
                    old['registration']=reg
            state['records'].append(item); state['safe_head']=None
            return result
        return self._op('domain_update',operation,write=True)

    def reserve_owned(self, head, guards, owner, managed):
        # Reuse strict role/scope/registration checks, but bind extra issuer boot.
        original=json.loads(canonical(managed))
        issuer_boot=original.pop('issuer_boot',None)
        if original.get('model')!=MODEL: raise Denied('Separated synthetic worker required')
        original['model']='owned-synthetic-file-v1'
        # Inline original reservation transaction: no transient legacy permit.
        self._guards(guards)
        def operation(state):
            self._current(state,head)
            for policy,scope in guards: self._grant(state,policy,scope)
            expected={'supervisor','supervisor_boot','job','worker_nonce','worker_pid','inventory_hash','scope_hash','model'}
            if set(original)!=expected or original['scope_hash']!=digest(guards):
                raise Denied('Separated synthetic scope binding required')
            reg=state.get('abort_supervisors',{}).get(original['supervisor'])
            if (not reg or not reg['active'] or reg.get('signature_model')!=MODEL
                    or reg['boot']!=original['supervisor_boot'] or reg['registration']['boot']!=issuer_boot):
                raise Denied('Registered synthetic issuer required')
            token,capability=uuid4().hex,secrets.token_hex(32)
            state['permits'][token]={'owner':owner,'proof_hash':hashlib.sha256(capability.encode()).hexdigest(),
                'policies':sorted({p for p,_ in guards}),'epoch':state['epoch'],'kind':'http',
                'phase':'reserved','managed':json.loads(canonical(managed))}
            return token,capability
        return self._op('reserve_separated_delivery',operation,write=True)

    def request_abort(self, head, token, capability, owner, operation):
        # Existing intent semantics, implemented without accepting the HMAC model.
        from datetime import timedelta
        def change(state):
            self._current(state,head)
            permit=self._permit(state,token,capability,owner); managed=permit.get('managed',{})
            reg=state.get('abort_supervisors',{}).get(managed.get('supervisor'))
            if (managed.get('model')!=MODEL or permit['phase'] not in ('reserved','emitting','unresolved','abort_requested')
                    or not reg or not reg['active'] or reg.get('signature_model')!=MODEL
                    or reg['registration']['boot']!=managed['issuer_boot']):
                raise Denied('Separated synthetic reservation required')
            prior=permit.get('abort')
            if prior and timestamp(prior['expires'])>datetime.now(timezone.utc):
                if prior['operation']!=operation: raise Denied('Synthetic abort already pending')
                return prior
            intent={'version':1,'instance':state['instance'],'delivery':token,'epoch':state['epoch'],
                    'managed':managed,'operation':operation,'challenge':secrets.token_hex(32),
                    'revision':1 if not prior else prior['revision']+1,'intent_head':head,
                    'expires':(datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat()}
            permit['abort']=intent; permit['phase']='abort_requested'; state['mode']='draining'
            return intent
        return self._op('separated_abort_intent',change,write=True)

    def _release_separated(self, state, evidence):
        try:
            if len(canonical(evidence))>16384 or set(evidence)!={'algorithm','key_id','issuer_boot','body','signature'}:
                raise ValueError()
            body=evidence['body']; intent=body['intent']; managed=intent['managed']
            reg=state.get('abort_supervisors',{}).get(managed['supervisor'])
            if (not reg or not reg['active'] or reg.get('signature_model')!=MODEL
                    or evidence['algorithm']!='Ed25519' or evidence['key_id']!=reg['registration']['key_id']
                    or evidence['issuer_boot']!=reg['registration']['boot']
                    or reg['boot']!=managed['supervisor_boot']):
                raise ValueError()
            signed={key:value for key,value in evidence.items() if key!='signature'}
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(reg['registration']['public'])).verify(
                bytes.fromhex(evidence['signature']),canonical(signed).encode())
            token=intent['delivery']; permit=state['permits'].get(token)
            if (set(body)!={'protocol','intent','facts','observation_hash','prior_output'}
                    or body['protocol']!=PROTOCOL or intent['epoch']!=state['epoch'] or intent['version']!=1
                    or intent['instance']!=state['instance'] or timestamp(intent['expires'])<=datetime.now(timezone.utc)
                    or managed['model']!=MODEL or managed['issuer_boot']!=evidence['issuer_boot']
                    or not isinstance(body['facts'],list) or len(body['facts'])!=len(FACTS) or set(body['facts'])!=FACTS
                    or body['prior_output']!='possible_or_unknown' or len(body['observation_hash'])!=64
                    or any(c not in '0123456789abcdef' for c in body['observation_hash'])
                    or not permit or permit['phase']!='abort_requested' or permit['epoch']!=state['epoch']
                    or permit.get('managed')!=managed or permit.get('abort')!=intent):
                raise ValueError()
            receipt={'receipt_id':uuid4().hex,'delivery':token,'epoch':state['epoch'],
                     'evidence_hash':digest(evidence),'outcome':'separated_abort_verified',
                     'prior_output':'possible_or_unknown'}
            state.setdefault('abort_receipts',{})[token]=receipt
            del state['permits'][token]
            return {'receipt':receipt}
        except (InvalidSignature,ValueError,TypeError,KeyError,AttributeError):
            raise Denied('Current authentic separated abort evidence required') from None


class SeparatedLedger(VerifiedLedger):
    def __init__(self, context):
        super().__init__(context,SeparatedAuthority(context))

    def release_abort(self, evidence):
        return self._change(None,'release_separated_abort',evidence=json.loads(canonical(evidence)))


class SeparatedSupervisor(OwnedOutputSupervisor):
    def __init__(self, client, representative, normal_authorize, timeout=2):
        self.client=client; self.normal=normal_authorize; self.timeout=timeout
        self.identity=uuid4().hex; self.boot=uuid4().hex; self.jobs={}; self.available=True
        self.owner=self.identity+':'+self.boot
        self.peer=MockAuthenticatedPeer('SUPERVISOR-SID')
        self.issuer=IsolatedIssuer(self.peer,timeout)
        self.unknown_resources=set()  # Trusted test supervisor's model inventory.
        try:
            client._change(representative,'register_separated_issuer',identity=self.identity,
                           supervisor_boot=self.boot,registration=self.issuer.registration)
        except BaseException:
            self.issuer.close(); raise

    def start(self, policy, scope, fetch, *, mode='normal'):
        self._active(); scope.validate()
        if mode not in ('normal','flush_fail','partial_fail','pause_flush','crash_write',
                        'abort_fence_fail','abort_ack_missing','hang_abort'):
            raise Denied('Known synthetic fault mode required')
        guards=[(policy,scope.data())]
        self._normal(guards); self.client.authorize(guards)
        try: payload=project(scope,fetch(scope)).decode()
        except Exception: raise Denied('Synthetic projection unavailable') from None
        self._normal(guards)
        identifier=uuid4().hex; nonce=uuid4().hex; control=secrets.token_hex(32)
        ctx=multiprocessing.get_context('spawn'); parent,child=ctx.Pipe()
        managed={'supervisor':self.identity,'supervisor_boot':self.boot,'job':identifier,
                 'worker_nonce':nonce,'worker_pid':0,'inventory_hash':digest(['buffer:'+identifier,'raw-file:'+identifier]),
                 'scope_hash':digest(guards),'model':MODEL,'issuer_boot':self.issuer.boot}
        process=ctx.Process(target=_worker,args=(child,self.client.context.root,self.client.context.instance,
                                                 identifier,nonce,self.boot,control,mode))
        job=OwnedJob(managed,policy,scope,process=process,connection=parent,control_key=control)
        self.jobs[identifier]=job
        try:
            process.start(); child.close()
            ready=self._receive(job,'ready')
            if ready['pid']!=process.pid or digest(ready['resources'])!=managed['inventory_hash']:
                raise Denied('Owned synthetic job inventory mismatch')
            managed['worker_pid']=process.pid
            self._normal(guards)
            job.token,job.capability=self.client.checked(lambda head:self.client.authority.reserve_owned(head,guards,self.owner,managed))
            self.client.checked(lambda head:self.client.authority.begin(head,job.token,job.capability,self.owner))
            result=self._command(job,'start',payload=payload)
            if result['event']=='handed_off':
                process.join(self.timeout)
                if process.is_alive() or process.exitcode!=0:
                    raise Unavailable('Synthetic writer exit unconfirmed')
                self.client.checked(lambda head:self.client.authority.finish(job.token,job.capability,self.owner,'handed_off'))
                job.status='handed_off'
            elif result['event']=='buffered': job.status='emitting'
            else: raise Unavailable('Synthetic output unresolved')
        except Exception:
            if not job.token:
                self._dispose(job); raise Denied('Synthetic owned output unavailable') from None
            job.status='unresolved'
            try: self.client.checked(lambda head:self.client.authority.mark_unresolved(job.token,job.capability,self.owner))
            except Exception: pass
        return job

    def abort_and_prove(self, job, *, crash_before_proof=False):
        self._active(); self._owned(job)
        if self.unknown_resources: raise Denied('Unobserved synthetic output resources')
        intent=self.client.checked(lambda head:self.client.authority.request_abort(
            head,job.token,job.capability,self.owner,job.operation))
        observed=self._command(job,'abort',challenge=intent['challenge'])
        job.process.join(self.timeout)
        # Independently query the actual spawned Process handle; do not accept a
        # caller's worker_exited flag. No OS-wide handle/job enumeration is claimed.
        if (job.process.is_alive() or job.process.exitcode!=0 or self.unknown_resources
                or observed.get('event')!='abort_observed' or observed.get('challenge')!=intent['challenge']
                or observed.get('closed') is not True or observed.get('pending_sync_io')!=0
                or observed.get('retry_blocked') is not True
                or digest(observed.get('resources'))!=job.managed['inventory_hash']):
            raise Denied('Independent fixed-job synthetic observation required')
        body={'protocol':PROTOCOL,'intent':intent,'facts':sorted(FACTS),
              'observation_hash':digest(observed),'prior_output':'possible_or_unknown'}
        if crash_before_proof:
            try: self.issuer.request(self.peer,'crash')
            finally: self.available=False
            raise Unavailable('Synthetic issuer crashed')
        proof=self.issuer.request(self.peer,'issue',body=body)
        job.status='abort_observed'; job.observation=observed
        return proof

    def rotate_key(self, representative):
        registration=self.issuer.request(self.peer,'rotate')
        self.client._change(representative,'rotate_separated_issuer',identity=self.identity,
                            registration=registration)

    def close(self):
        super().close()
        self.issuer.close()
