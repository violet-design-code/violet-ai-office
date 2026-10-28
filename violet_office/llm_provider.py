"""Bounded OpenAI Chat Completions transport; zero network in default/mock mode."""
import json,os,re,socket,time
from dataclasses import dataclass
from decimal import Decimal,ROUND_CEILING
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.error import HTTPError,URLError

MAX_BYTES=128000

class ModelError(Exception):
    def __init__(self,code,unknown=False,retryable=False):self.code=code;self.unknown=unknown;self.retryable=retryable

@dataclass(frozen=True)
class Result:
    data:dict
    input_tokens:int
    output_tokens:int


def money(tokens,rate):return int((Decimal(tokens)*Decimal(rate)).to_integral_value(rounding=ROUND_CEILING))

def config(mode):
    if mode=='mock':return {'model':'synthetic-mock','input_rate':'0','output_rate':'0','price_ref':'mock-no-charge','timeout':5,'max_output':1500}
    if mode!='openai':raise ModelError('unknown_provider')
    try:
        model=os.environ.get('VIOLET_LLM_MODEL','');ir=os.environ.get('VIOLET_LLM_INPUT_USD_PER_MILLION','');orr=os.environ.get('VIOLET_LLM_OUTPUT_USD_PER_MILLION','');ref=os.environ.get('VIOLET_LLM_PRICE_REFERENCE','')
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}',model) or not ref or len(ref)>300:raise ValueError()
        from .content import safe_input
        safe_input(ref)
        for value in (ir,orr):
            d=Decimal(value)
            if not d.is_finite() or not 0<d<=100000:raise ValueError()
        timeout=int(os.environ.get('VIOLET_LLM_TIMEOUT_SECONDS','20'));limit=int(os.environ.get('VIOLET_LLM_MAX_OUTPUT_TOKENS','1500'))
        if not 1<=timeout<=30 or not 128<=limit<=3000:raise ValueError()
        return {'model':model,'input_rate':ir,'output_rate':orr,'price_ref':ref,'timeout':timeout,'max_output':limit}
    except Exception:raise ModelError('model_or_price_configuration_required') from None

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ModelError('redirect_rejected',unknown=True)

class OpenAIProvider:
    mode='openai'
    def invoke(self,messages,cfg,request_id):
        if os.environ.get('VIOLET_LLM_LIVE_ENABLED')!='1':raise ModelError('live_disabled')
        key=os.environ.get('OPENAI_API_KEY','')
        if not key or any(c.isspace() for c in key):raise ModelError('api_key_required')
        raw=json.dumps({'model':cfg['model'],'messages':messages,'response_format':{'type':'json_object'},'max_completion_tokens':cfg['max_output']},ensure_ascii=False).encode()
        req=Request('https://api.openai.com/v1/chat/completions',data=raw,method='POST',headers={'Authorization':'Bearer '+key,'Content-Type':'application/json','X-Client-Request-Id':request_id})
        try:
            with build_opener(NoRedirect()).open(req,timeout=cfg['timeout']) as response:
                deadline=time.monotonic()+cfg['timeout'];chunks=[];size=0
                while True:
                    if time.monotonic()>deadline:raise ModelError('network_result_unknown',unknown=True)
                    chunk=response.read1(min(4096,MAX_BYTES+1-size))
                    if not chunk:break
                    chunks.append(chunk);size+=len(chunk)
                    if size>MAX_BYTES:raise ModelError('response_limit',unknown=True)
                data=b''.join(chunks)
            if len(data)>MAX_BYTES:raise ModelError('response_limit',unknown=True)
            value=json.loads(data);usage=value['usage'];message=value['choices'][0]['message']
            a,z=usage['prompt_tokens'],usage['completion_tokens']
            if type(a) is not int or type(z) is not int or not 0<=a<=200000 or not 0<=z<=cfg['max_output']:raise ModelError('invalid_usage',unknown=True)
            if value['choices'][0].get('finish_reason')!='stop' or message.get('refusal'):return Result({},a,z)
            try:body=json.loads(message['content'])
            except (ValueError,TypeError):body={}
            return Result(body,a,z)
        except HTTPError as exc:
            # Never record provider bodies/headers: they may echo sensitive inputs.
            code=exc.code;exc.close()
            raise ModelError('rate_limited' if code==429 else 'provider_rejected' if code in (400,401,403,404,422) else 'provider_uncertain',unknown=code not in (400,401,403,404,422,429),retryable=code==429) from None
        except ModelError:raise
        except (URLError,TimeoutError,socket.timeout,OSError):raise ModelError('network_result_unknown',unknown=True) from None
        except Exception:raise ModelError('invalid_provider_response',unknown=True) from None

class MockProvider:
    mode='mock'
    def invoke(self,messages,cfg,request_id):
        payload=json.loads(messages[-1]['content']);stage=payload['stage'];count=payload['count']
        if stage=='plan':data={'purpose':'合成のInstagram下書き作成','assignments':['AI07','AI09'],'notes':'モック・事実確認は代表へ'}
        elif stage=='marketing':data={'theme':'合成の任意参加のご案内','audience':'本人が関心を示した成人','cta':'ご希望の場合のみお問い合わせください。','unverified':['提供内容・料金・公式条件は未確認']}
        elif stage=='draft':data={'posts':[{'title':'合成の投稿案 '+str(i+1),'body':'これはモック検証用の下書きです。ご希望に合う情報を確認しながらご案内します。','hashtags':['#バイオレット'],'cta':'ご関心があればお問い合わせください。'} for i in range(count)],'unverified':['事実・権利・法令・公式条件の人間による確認が必要']}
        else:data={'summary':'企画と原稿の参照を確認した合成レポート。実API・調査・公開は未実施。','verdict':'needs_human_review','issues':['未確認の事実・法令・権利を代表が確認してください。']}
        return Result(data,100,100)
