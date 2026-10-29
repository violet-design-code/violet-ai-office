"""Owned-worker abort extension for the TEMPORARY D sandbox ONLY.

Real spawned processes, synchronous temporary-file I/O and authenticated mock
frames; NOT Windows jobs, OS handle enumeration, socket/network cancellation,
IPC authentication or a production signing service. Never imported by Office.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import io
import json
import multiprocessing
from multiprocessing.connection import wait
import os
from pathlib import Path
import secrets
import tempfile
from uuid import uuid4

from .synthetic_recovery import (Denied, Unavailable, LocalLedger, MockAuthority,
    DeliveryBroker, database, read_records, verify, record, insert_record, canonical,
    digest, mac, timestamp, project)

MODEL = 'owned-synthetic-file-v1'
FACTS = {'worker_exited','registered_job_empty','owned_resources_closed',
         'synchronous_io_settled','buffer_unreachable','post_fence_retry_blocked'}


def wait_for_owned_reply(connection, process, timeout, message):
    """Bounded readiness wait; process death is failure, never an abort proof."""
    try:
        ready=wait([connection,process.sentinel],timeout=timeout)
        if connection not in ready:
            raise Unavailable(message)
    except (OSError,ValueError):
        raise Unavailable(message) from None


def key_path(ctx, identity):
    if not isinstance(identity,str) or len(identity)!=32 or any(c not in '0123456789abcdef' for c in identity):
        raise Denied('Registered synthetic supervisor required')
    return Path(ctx.root)/'mock-external'/('supervisor-'+identity+'.key')


def verify_envelope(envelope, secret, limit=16384):
    # Copy before checking, so later mutations cannot change validated contents.
    try:
        text=canonical(envelope)
        if len(text)>limit:
            raise Denied('Synthetic evidence limit exceeded')
        copy=json.loads(text)
        if (not isinstance(copy,dict) or set(copy)!={'body','mac'}
                or not isinstance(copy['body'],dict) or not isinstance(copy['mac'],str)
                or not hmac.compare_digest(copy['mac'],mac(secret,copy['body']))):
            raise Denied('Synthetic evidence authentication failed')
        return copy['body']
    except (TypeError,ValueError,KeyError,RecursionError):
        raise Denied('Synthetic evidence format unavailable') from None


class VerifiedAuthority(MockAuthority):
    def _op(self, kind, fn, write=False):
        with database(self.context,self.context.external) as db:
            history=read_records(db,self.context.external_key)
            if not history:
                raise Denied('Synthetic authority registration unavailable')
            state=json.loads(canonical(history[-1]['event']['state']))
            if state['instance']!=self.context.instance:
                raise Denied('Synthetic authority instance mismatch')
            result=fn(state)
            repeated=isinstance(result,dict) and result.get('_repeat_abort_receipt') is True
            if write and not repeated:
                insert_record(db,record(self.context.external_key,history,{'kind':kind,'state':state}))
            return result

    def apply(self, item, expected, representative):
        kind=item['event']['kind']
        if kind not in ('register_abort_supervisor','disable_abort_supervisor','release_verified_abort'):
            return super().apply(item,expected,representative)
        def op(s):
            if verify(s['records'],self.context.local_key)!=expected or s['safe_head']!=expected or not s['enrolled']:
                raise Denied('Synthetic histories are not aligned')
            verify(s['records']+[item],self.context.local_key)
            e=item['event']; result=None
            if kind=='release_verified_abort':
                result=self._release(s,e['evidence'])
                if result.get('_repeat_abort_receipt') is True:
                    return result
            else:
                self._admin(s,representative)
                registry=s.setdefault('abort_supervisors',{})
                if kind=='register_abort_supervisor':
                    if s['mode'] not in ('closed','active') or e['identity'] in registry:
                        raise Denied('New synthetic supervisor registration required')
                    path=key_path(self.context,e['identity'])
                    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=e['key_hash']:
                        raise Denied('Synthetic supervisor key registration unavailable')
                    registry[e['identity']]={'boot':e['boot'],'key_hash':e['key_hash'],'active':True}
                else:
                    if e['identity'] not in registry:
                        raise Denied('Synthetic supervisor registration unavailable')
                    registry[e['identity']]['active']=False
            s['records'].append(item); s['safe_head']=None
            return result
        return self._op('domain_update',op,write=True)

    def reserve_owned(self, head, guards, owner, managed):
        self._guards(guards)
        def op(s):
            self._current(s,head)
            for policy,scope in guards: self._grant(s,policy,scope)
            expected={'supervisor','supervisor_boot','job','worker_nonce','worker_pid','inventory_hash','scope_hash','model'}
            if set(managed)!=expected or managed['model']!=MODEL or managed['scope_hash']!=digest(guards):
                raise Denied('Owned synthetic output binding required')
            registered=s.get('abort_supervisors',{}).get(managed['supervisor'])
            if not registered or not registered['active'] or registered['boot']!=managed['supervisor_boot']:
                raise Denied('Registered synthetic supervisor required')
            token,proof=uuid4().hex,secrets.token_hex(32)
            s['permits'][token]={'owner':owner,'proof_hash':hashlib.sha256(proof.encode()).hexdigest(),
                'policies':sorted({p for p,_ in guards}),'epoch':s['epoch'],'kind':'http',
                'phase':'reserved','managed':json.loads(canonical(managed))}
            return token,proof
        return self._op('reserve_owned_delivery',op,write=True)

    def request_abort(self, head, token, capability, owner, operation):
        def op(s):
            self._current(s,head)
            p=self._permit(s,token,capability,owner)
            managed=p.get('managed')
            if not managed or managed['model']!=MODEL or p['phase'] not in ('reserved','emitting','unresolved','abort_requested'):
                raise Denied('Managed synthetic reservation required')
            reg=s.get('abort_supervisors',{}).get(managed['supervisor'])
            if not reg or not reg['active'] or reg['boot']!=managed['supervisor_boot']:
                raise Denied('Registered synthetic supervisor required')
            prior=p.get('abort')
            if prior and timestamp(prior['expires'])>datetime.now(timezone.utc):
                if prior['operation']!=operation:
                    raise Denied('Synthetic abort challenge already pending')
                return prior
            intent={'version':1,'instance':s['instance'],'delivery':token,'epoch':s['epoch'],
                'managed':managed,'operation':operation,'challenge':secrets.token_hex(32),
                'revision':1 if not prior else prior['revision']+1,'intent_head':head,
                'expires':(datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat()}
            p['abort']=intent; p['phase']='abort_requested'; s['mode']='draining'
            return intent
        return self._op('abort_intent',op,write=True)

    def _release(self, s, envelope):
        # Authenticate first; possession of a caller-supplied facts dictionary is insufficient.
        copy=json.loads(canonical(envelope)); proposed=copy.get('body',{})
        identity=proposed.get('intent',{}).get('managed',{}).get('supervisor')
        reg=s.get('abort_supervisors',{}).get(identity)
        if not reg or not reg['active']:
            raise Denied('Registered synthetic supervisor required')
        path=key_path(self.context,identity)
        if path.is_symlink(): raise Denied('Synthetic supervisor key unavailable')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=reg['key_hash']:
            raise Denied('Synthetic supervisor key unavailable')
        body=verify_envelope(copy,raw.decode())
        if set(body)!={'intent','facts','observation_hash','prior_output'}:
            raise Denied('Complete synthetic evidence required')
        intent=body['intent']; managed=intent['managed']
        if (intent['version']!=1 or intent['instance']!=s['instance'] or intent['epoch']!=s['epoch']
                or managed['model']!=MODEL or reg['boot']!=managed['supervisor_boot']
                or timestamp(intent['expires'])<=datetime.now(timezone.utc)
                or not isinstance(body['facts'],list) or len(body['facts'])!=len(FACTS)
                or set(body['facts'])!=FACTS
                or body['prior_output']!='possible_or_unknown'
                or not isinstance(body['observation_hash'],str) or len(body['observation_hash'])!=64
                or any(c not in '0123456789abcdef' for c in body['observation_hash'])):
            raise Denied('Current complete synthetic abort evidence required')
        token=intent['delivery']; evidence_hash=digest(copy)
        receipts=s.setdefault('abort_receipts',{})
        if token in receipts:
            old=receipts[token]
            if old['evidence_hash']!=evidence_hash:
                raise Denied('Synthetic abort receipt conflict')
            return {'_repeat_abort_receipt':True,'receipt':old}
        p=s['permits'].get(token)
        if (not p or p['phase']!='abort_requested' or p['epoch']!=s['epoch']
                or p.get('managed')!=managed or p.get('abort')!=intent):
            raise Denied('Matching unresolved synthetic abort intent required')
        receipt={'receipt_id':uuid4().hex,'delivery':token,'epoch':s['epoch'],
                 'operation':intent['operation'],'evidence_hash':evidence_hash,'outcome':'abort_verified',
                 'prior_output':'possible_or_unknown'}
        receipts[token]=receipt
        del s['permits'][token]
        return {'receipt':receipt}


class VerifiedLedger(LocalLedger):
    def __init__(self, context, authority=None):
        super().__init__(context,authority or VerifiedAuthority(context))

    def _change_locked(self, representative, kind, **args):
        with database(self.context,self.context.local) as db:
            history=read_records(db,self.context.local_key)
            head,_=self._aligned(db,history,enrolled=kind!='enroll')
            item=record(self.context.local_key,history,{'kind':kind,'args_version':1,**args})
            result=self.authority.apply(item,head,representative)
            if isinstance(result,dict) and result.get('_repeat_abort_receipt') is True:
                self._aligned(db,history)
                return result['receipt']
            insert_record(db,item)
            self.authority.confirm(verify(history+[item],self.context.local_key))
            self._aligned(db,history+[item])
            return result['receipt'] if isinstance(result,dict) and 'receipt' in result else result

    def release_abort(self, evidence):
        # No representative bypass: this path requires registered supervisor evidence.
        return self._change(None,'release_verified_abort',evidence=json.loads(canonical(evidence)))

    def disable_supervisor(self, representative, identity):
        self._change(representative,'disable_abort_supervisor',identity=identity)


class _FileRaw(io.RawIOBase):
    """A fixed synchronous synthetic sink; owns exactly one new temporary file."""
    def __init__(self,path,mode):
        super().__init__(); self.file=path.open('xb',buffering=0); self.mode=mode; self.calls=0

    def writable(self): return True

    def write(self,data):
        if self.closed or self.file.closed: raise ValueError('Fenced synthetic raw output')
        self.calls+=1
        if self.mode=='crash_write': os._exit(23)
        if self.mode=='partial_fail' and self.calls==1: return self.file.write(data[:7])
        if self.mode=='partial_fail' and self.calls==2: raise OSError('Synthetic partial failure')
        if self.mode in ('flush_fail','abort_fence_fail','abort_ack_missing','hang_abort') and self.calls==1:
            raise OSError('Synthetic flush failure')
        return self.file.write(data)

    def close(self):
        if not self.closed:
            self.file.close()  # Unbuffered file: no hidden user-space flush here.
            super().close()


def _frame(key, body): return {'body':body,'mac':mac(key,body)}


def _send_frame(connection, key, body):
    data=canonical(_frame(key,body)).encode()
    if len(data)>262144: raise Denied('Synthetic control frame limit exceeded')
    connection.send_bytes(data)


def _read_frame(connection, key, limit=262144):
    try:
        data=connection.recv_bytes(limit)
        return verify_envelope(json.loads(data.decode()),key,limit=limit)
    except (ValueError,TypeError,UnicodeError,RecursionError):
        raise Denied('Synthetic control frame format unavailable') from None


def _worker(connection, root, instance, job, nonce, boot, control_key, mode):
    """Only this fixed function may run. No callbacks, child jobs, sockets or keys."""
    raw=buffer=None; sequence=0
    resources=['buffer:'+job,'raw-file:'+job]
    base={'job':job,'worker_nonce':nonce,'supervisor_boot':boot}
    def send(event, **extra):
        _send_frame(connection,control_key,{**base,'event':event,'sequence':sequence,**extra})
    try:
        directory=Path(root)
        if (directory.is_symlink() or directory.resolve().parent!=Path(tempfile.gettempdir()).resolve()
                or not directory.name.startswith('violet-d-synthetic-')
                or (directory/'SYNTHETIC_ONLY').is_symlink()
                or (directory/'SYNTHETIC_ONLY').read_text()!=instance
                or (directory/'outputs').is_symlink()):
            return
        send('ready',pid=os.getpid(),resources=resources)
        started=False
        while True:
            try: message=_read_frame(connection,control_key)
            except EOFError: return  # Cleanup may fence, but NEVER releases a reservation.
            if (message.get('job')!=job or message.get('worker_nonce')!=nonce
                    or message.get('supervisor_boot')!=boot or message.get('sequence')!=sequence+1):
                return
            sequence+=1; command=message.get('command')
            if command=='start' and not started:
                started=True
                payload=message['payload'].encode()
                if len(payload)>65536: return
                raw=_FileRaw(directory/'outputs'/(job+'.http'),mode)
                buffer=io.BufferedWriter(raw,buffer_size=131072)
                headers=(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nCache-Control: no-store\r\n'
                         b'Connection: close\r\nContent-Length: '+str(len(payload)).encode()+b'\r\n\r\n')
                buffer.write(headers+payload)
                if mode=='pause_flush':
                    send('buffered'); continue
                try:
                    buffer.flush()
                except Exception:
                    send('failed'); continue
                buffer.close(); raw.close(); send('handed_off'); return
            elif command in ('retry','close') and started and buffer is not None:
                try:
                    if command=='retry': buffer.flush()
                    else: buffer.close()
                    send('retry_returned')
                except Exception: send('retry_failed')
            elif command=='abort':
                if mode=='hang_abort':
                    # Fault injection: no reply; wait for owner teardown/EOF.
                    try: connection.recv_bytes(262144)
                    except EOFError: pass
                    return
                if raw is None or buffer is None or mode=='abort_fence_fail':
                    send('abort_failed'); return
                raw.close()  # Fence FIRST. A later buffer.close is not itself evidence.
                try: buffer.close()
                except (OSError,ValueError): pass
                checks=[]
                for operation in (buffer.flush,lambda:buffer.write(b'retry'),lambda:raw.write(b'retry')):
                    try: operation(); checks.append(False)
                    except (OSError,ValueError): checks.append(True)
                closed=raw.closed and raw.file.closed and buffer.closed
                if mode=='abort_ack_missing': return
                send('abort_observed',challenge=message['challenge'],resources=resources,
                     closed=closed,pending_sync_io=0,retry_blocked=all(checks))
                return
            else: return
    except BaseException:
        # Fixed worker never serializes exception text, payload, keys or source data.
        return
    finally:
        if raw is not None:
            try: raw.close()
            except Exception: pass
        if buffer is not None:
            try: buffer.close()
            except (OSError,ValueError): pass
        connection.close()


def _proof_issuer(connection, secret, body, crash):
    """Synthetic supervisor signing subprocess; trusted parent supplies observations.

    This is not an independent kernel observer or Windows signing service.
    """
    try:
        if crash: os._exit(31)
        _send_frame(connection,secret,body)
    finally: connection.close()


@dataclass
class OwnedJob:
    managed: dict
    policy: str
    scope: object
    token: str = ''
    capability: str = field(default='',repr=False)
    operation: str = field(default_factory=lambda:uuid4().hex)
    process: object = field(default=None,repr=False)
    connection: object = field(default=None,repr=False)
    control_key: str = field(default='',repr=False)
    sequence: int = 0
    status: str = 'starting'
    observation: dict = field(default=None,repr=False)


class OwnedOutputSupervisor:
    """Owner of fixed synthetic jobs, not a Windows supervisor or security boundary."""
    def __init__(self, client, representative, normal_authorize, timeout=2, startup_timeout=15):
        self.client=client; self.normal=normal_authorize; self.timeout=timeout
        self.startup_timeout=startup_timeout
        self.identity=uuid4().hex; self.boot=uuid4().hex
        self._secret=secrets.token_hex(32); self.jobs={}; self.available=True
        self.owner=self.identity+':'+self.boot
        path=key_path(client.context,self.identity)
        client.context.validate()
        with path.open('xb') as output:
            output.write(self._secret.encode()); output.flush(); os.fsync(output.fileno())
        client._change(representative,'register_abort_supervisor',identity=self.identity,boot=self.boot,
                       key_hash=hashlib.sha256(self._secret.encode()).hexdigest())

    def _active(self):
        if not self.available: raise Unavailable('Synthetic supervisor unavailable')

    def _normal(self, guards):
        DeliveryBroker(self.client,self.normal)._normal(guards)

    def _receive(self, job, event=None, timeout=None):
        budget=self.timeout if timeout is None else timeout
        wait_for_owned_reply(job.connection,job.process,budget,'Synthetic worker observation unavailable')
        try: body=_read_frame(job.connection,job.control_key)
        except (EOFError,OSError): raise Unavailable('Synthetic worker observation unavailable') from None
        if (any(body.get(k)!=job.managed[k] for k in ('job','worker_nonce','supervisor_boot'))
                or body.get('sequence')!=job.sequence or (event is not None and body.get('event')!=event)):
            raise Denied('Matching synthetic worker observation required')
        return body

    def _command(self, job, command, **extra):
        job.sequence+=1
        message={k:job.managed[k] for k in ('job','worker_nonce','supervisor_boot')}
        message.update(sequence=job.sequence,command=command,**extra)
        try: _send_frame(job.connection,job.control_key,message)
        except (BrokenPipeError,EOFError,OSError): raise Unavailable('Synthetic worker control unavailable') from None
        return self._receive(job)

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
                 'scope_hash':digest(guards),'model':MODEL}
        process=ctx.Process(target=_worker,args=(child,self.client.context.root,self.client.context.instance,
                                                 identifier,nonce,self.boot,control,mode))
        job=OwnedJob(managed,policy,scope,process=process,connection=parent,control_key=control)
        self.jobs[identifier]=job
        try:
            process.start(); child.close()
            ready=self._receive(job,'ready',timeout=self.startup_timeout)
            if ready['pid']!=process.pid or digest(ready['resources'])!=managed['inventory_hash']:
                raise Denied('Owned synthetic job inventory mismatch')
            managed['worker_pid']=process.pid
            self._normal(guards)
            job.token,job.capability=self.client.checked(lambda h:self.client.authority.reserve_owned(h,guards,self.owner,managed))
            self.client.checked(lambda h:self.client.authority.begin(h,job.token,job.capability,self.owner))
            result=self._command(job,'start',payload=payload)
            if result['event']=='handed_off':
                process.join(self.timeout)
                if process.is_alive() or process.exitcode!=0:
                    raise Unavailable('Synthetic writer exit unconfirmed')
                self.client.checked(lambda h:self.client.authority.finish(job.token,job.capability,self.owner,'handed_off'))
                job.status='handed_off'
            elif result['event']=='buffered': job.status='emitting'
            else: raise Unavailable('Synthetic output unresolved')
        except Exception:
            if not job.token:
                self._dispose(job); raise Denied('Synthetic owned output unavailable') from None
            job.status='unresolved'
            try: self.client.checked(lambda h:self.client.authority.mark_unresolved(job.token,job.capability,self.owner))
            except Exception: pass
        return job

    def retry_for_test(self, job, *, close=False):
        self._active(); self._owned(job)
        return self._command(job,'close' if close else 'retry')['event']

    def _owned(self, job):
        if self.jobs.get(job.managed['job']) is not job or job.managed['supervisor_boot']!=self.boot:
            raise Denied('Owned synthetic job required')

    def abort_and_prove(self, job, *, crash_before_proof=False):
        self._active(); self._owned(job)
        intent=self.client.checked(lambda h:self.client.authority.request_abort(h,job.token,job.capability,self.owner,job.operation))
        observed=self._command(job,'abort',challenge=intent['challenge'])
        process=job.process; process.join(self.timeout)
        if (observed.get('event')!='abort_observed' or observed.get('challenge')!=intent['challenge']
                or observed.get('closed') is not True or observed.get('pending_sync_io')!=0
                or observed.get('retry_blocked') is not True
                or digest(observed.get('resources'))!=job.managed['inventory_hash']
                or process.is_alive() or process.exitcode!=0):
            raise Denied('Complete synthetic abort observation required')
        # This is a closed fixed-code synthetic job. No claim of OS-wide child or
        # handle enumeration is made: only our one process/two resources are in scope.
        body={'intent':intent,'facts':sorted(FACTS),'observation_hash':digest(observed),
              'prior_output':'possible_or_unknown'}
        ctx=multiprocessing.get_context('spawn'); receiver,sender=ctx.Pipe(duplex=False)
        issuer=ctx.Process(target=_proof_issuer,args=(sender,self._secret,body,crash_before_proof))
        try:
            issuer.start(); sender.close()
            wait_for_owned_reply(receiver,issuer,self.startup_timeout,'Synthetic supervisor evidence unavailable')
            try:
                issued_body=_read_frame(receiver,self._secret,limit=16384)
                envelope=_frame(self._secret,issued_body)
            except (EOFError,OSError):
                raise Unavailable('Synthetic supervisor crashed before evidence') from None
            issuer.join(self.timeout)
            if issuer.is_alive() or issuer.exitcode!=0 or verify_envelope(envelope,self._secret)!=body:
                raise Unavailable('Synthetic supervisor evidence unavailable')
        except Exception:
            self.available=False
            raise Unavailable('Synthetic supervisor evidence unavailable') from None
        finally:
            receiver.close(); sender.close()
            if issuer.pid is not None:
                issuer.join(0.1)
                if issuer.is_alive(): issuer.terminate(); issuer.join(2)
        job.status='abort_observed'; job.observation=observed
        return json.loads(canonical(envelope))

    def release(self, evidence):
        self._active()
        return self.client.release_abort(evidence)

    @staticmethod
    def _dispose(job):
        try: job.connection.close()
        except OSError: pass
        if job.process.pid is not None:
            job.process.join(0.2)
            if job.process.is_alive(): job.process.terminate(); job.process.join(2)
        # Teardown never releases a reservation or manufactures an abort proof.

    def close(self):
        self.available=False
        for job in self.jobs.values(): self._dispose(job)
