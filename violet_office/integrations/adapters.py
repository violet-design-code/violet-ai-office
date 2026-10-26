"""Explicit adapter contracts. Only in-memory mock operations are executable."""
from dataclasses import dataclass
from typing import Protocol
import base64
import hashlib
import hmac


@dataclass(frozen=True)
class Principal:
    role: str


class Authorizer:
    """Local entry-point token verification, never passed to providers or prompts."""
    def __init__(self, representative_token, viewer_token):
        if min(len(representative_token),len(viewer_token))<32 or representative_token==viewer_token:
            raise ValueError('Distinct tokens of at least 32 characters required')
        self._representative=hashlib.sha256(representative_token.encode()).digest()
        self._viewer=hashlib.sha256(viewer_token.encode()).digest()

    def authenticate(self, token):
        if not isinstance(token,str) or not 1<=len(token)<=4096:
            raise PermissionError('認証できません')
        candidate=hashlib.sha256(token.encode()).digest()
        if hmac.compare_digest(candidate,self._representative):
            return Principal('representative')
        if hmac.compare_digest(candidate,self._viewer):
            return Principal('viewer')
        raise PermissionError('認証できません')


def require(principal, write=False):
    if not isinstance(principal,Principal) or principal.role not in ('representative','viewer') or (write and principal.role!='representative'):
        raise PermissionError('権限がありません')


class RequestLimitError(ValueError):
    pass


def verify_line_signature(body, signature, channel_secret):
    if not isinstance(body,bytes) or len(body)>65536 or not channel_secret or not isinstance(signature,str):
        raise ValueError('Webhookを受理できません')
    expected=base64.b64encode(hmac.new(channel_secret.encode(),body,hashlib.sha256).digest()).decode()
    if not hmac.compare_digest(expected,signature):
        raise ValueError('Webhookを受理できません')


@dataclass(frozen=True)
class AdapterResult:
    outcome: str  # success / retryable (definitely not accepted) / unknown / permanent
    code: str


class IntegrationAdapter(Protocol):
    mode: str
    timeout_seconds: int
    def execute(self, operation: str, payload: dict, idempotency_key: str) -> AdapterResult: ...


class MockAdapter:
    """Does not use sockets. Calls are simulation records only, never real delivery."""
    mode='mock'
    timeout_seconds=5

    def __init__(self, outcomes=None):
        self.outcomes=list(outcomes or [])
        self.calls=[]
        self.accepted={}

    def execute(self, operation, payload, idempotency_key):
        if idempotency_key in self.accepted:
            return self.accepted[idempotency_key]
        self.calls.append({'operation':operation,'idempotency_key':idempotency_key})
        outcome=self.outcomes.pop(0) if self.outcomes else 'success'
        codes={'success':'mock_accepted','retryable':'mock_not_accepted','unknown':'mock_result_unknown','permanent':'mock_rejected'}
        if outcome not in codes:
            raise ValueError('Invalid mock outcome')
        result=AdapterResult(outcome,codes[outcome])
        if outcome=='success':
            self.accepted[idempotency_key]=result
        return result


class LegacyStepAdapter(Protocol):
    """Future existing-system bridge must check state at execution, not cached CRM.

    No implementation writes the other repository or its SQLite files here.
    Existing system remains authoritative for stop/reconsent/booked state.
    """
    def prepare_enrollment(self, subject_ref: str, purpose: str, approval_ref: str) -> dict: ...
    def pause(self, subject_ref: str, reason: str, idempotency_key: str) -> dict: ...


class BookingAdapter(Protocol):
    def available_slots(self) -> list[dict]: ...
    def confirm(self, slot_ref: str, idempotency_key: str) -> AdapterResult: ...
    def cancel(self, booking_ref: str, idempotency_key: str) -> AdapterResult: ...


class MockBookingAdapter(MockAdapter):
    def available_slots(self):
        return [{'slot_ref':'mock-slot-1','start':'2030-01-10T01:00:00+00:00','end':'2030-01-10T01:30:00+00:00','mode':'mock'}]


class ProductionDisabled:
    mode='disconnected'
    def execute(self,*args,**kwargs):
        raise PermissionError('本番接続は未実装です')


@dataclass(frozen=True)
class RequestSpec:
    method: str
    origin: str
    path: str
    json_body: dict
    credential_binding: str
    timeout_seconds: int = 5


class LineMessagingAdapter:
    """Builds an official Messaging API push request only; cannot transmit it.

    Recipient must come from a future protected mapping, never an LLM prompt.
    Official contract must be revalidated before implementing a live transport.
    """
    mode='disconnected'

    def build_text_push(self, recipient, text):
        import re
        if not re.fullmatch(r'U[0-9a-f]{32}',recipient) or not isinstance(text,str) or not 1<=len(text)<=5000:
            raise ValueError('Invalid Messaging API draft')
        return RequestSpec('POST','https://api.line.me','/v2/bot/message/push',
            {'to':recipient,'messages':[{'type':'text','text':text}]},'LINE_CHANNEL_ACCESS_TOKEN')

    def execute(self,*args,**kwargs):
        raise PermissionError('LINE送信は未接続です')


class SnsDraftAdapter:
    mode='disconnected'
    platforms=('Instagram','Facebook')

    def capabilities(self):
        return {'platforms':self.platforms,'drafts':True,'scheduling_metadata':True,
                'publish':False,'direct_messages':False,'reason':'公式API権限・アカウント種別・最新版仕様の検証と代表承認が必要'}

    def execute(self,*args,**kwargs):
        raise PermissionError('SNS公開は未接続です')


class WebhookEndpoint:
    """Framework-neutral handler, no socket listener or public deployment.

    A future gateway must preserve the exact raw body, add TLS/limits, and map
    header names case-insensitively. Does not expose exception messages.
    """
    def __init__(self, service):
        self.service=service

    def handle(self, raw_body, signature):
        try:
            result=self.service.webhook(raw_body,signature)
            return 200,result
        except RequestLimitError:
            return 429,{'error':'リクエスト上限を超えました','mode':'mock'}
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
            # No raw payload, token, user identifier or exception string in audit.
            self.service._audit('webhook_rejected','redacted')
            return 400,{'error':'Webhookを受理できません','mode':'mock'}
