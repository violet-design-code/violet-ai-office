"""Parse only the bounded authenticated photo form; never trusts file paths."""
import base64
from email.parser import BytesParser
from email import policy


def parse(content_type,body):
    message=BytesParser(policy=policy.default).parsebytes(('Content-Type: '+content_type+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+body)
    if not message.is_multipart(): raise ValueError('Multipart form required')
    data={}
    for part in message.iter_parts():
        name=part.get_param('name',header='content-disposition')
        if name not in {'csrf','nonce','business','case','stage','photo'} or name in data or len(data)>=6: raise ValueError('Invalid photo form')
        payload=part.get_payload(decode=True)
        if not isinstance(payload,bytes): raise ValueError('Invalid form content')
        if name=='photo':
            if len(payload)>2*1024*1024: raise ValueError('Photo too large')
            data[name]=base64.b64encode(payload).decode('ascii')
        else:
            if len(payload)>256: raise ValueError('Field too large')
            data[name]=payload.decode('utf-8')
    if set(data)!={'csrf','nonce','business','case','stage','photo'}: raise ValueError('Complete photo form required')
    data['image']=data.pop('photo')
    return data
