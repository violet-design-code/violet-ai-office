"""Explicit private .ics export, not Google synchronization or confirmed phone delivery."""
from datetime import datetime,timedelta,time,timezone
from .seminars import JST
from .store import now
from .content import digest


def escaped(v):return v.replace('\\','\\\\').replace('\r','').replace('\n','\\n').replace(';','\\;').replace(',','\\,')
def utc(v):return datetime.fromisoformat(v).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
def folded(line):
    parts=[];part=''
    for char in line:
        if len((part+char).encode())>73:parts.append(part);part=' '+char
        else:part+=char
    return '\r\n'.join(parts+[part])

def export(service,p,checked,start,end):
    service.access(p)
    if checked is not True:raise ValueError('カレンダーへの個人予定の持ち出しを確認してください。')
    rows=service.listing(p,start,end);owner=digest(p.identity)[:20];lines=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//VIOLET//Private Schedule//JA','CALSCALE:GREGORIAN','METHOD:PUBLISH'];groups={}
    for r in rows:
        lines+=['BEGIN:VEVENT','UID:'+r['id']+'@violet.local','DTSTAMP:'+utc(now().isoformat()),'SEQUENCE:'+str(r['revision']),'DTSTART:'+utc(r['start_at']),'DTEND:'+utc(r['end_at']),'SUMMARY:'+escaped(r['title']),'LOCATION:'+escaped(r['location']),'CLASS:PRIVATE','BEGIN:VALARM','ACTION:DISPLAY','DESCRIPTION:'+escaped('【VIOLET AI OFFICE】あと1時間で予定があります。 '+r['title']),'TRIGGER:-PT60M','END:VALARM','END:VEVENT']
        day=datetime.fromisoformat(r['start_at']).astimezone(JST).date();groups.setdefault(day,[]).append(r)
    for day,events in sorted(groups.items()):
        at=datetime.combine(day-timedelta(days=1),time(21),JST);summary='【VIOLET AI OFFICE】明日の予定：'+' / '.join(datetime.fromisoformat(e['start_at']).astimezone(JST).strftime('%H:%M')+' '+e['title'] for e in events)
        lines+=['BEGIN:VEVENT','UID:'+owner+'-'+day.isoformat()+'-daily@violet.local','DTSTAMP:'+utc(now().isoformat()),'DTSTART:'+utc(at.isoformat()),'DTEND:'+utc((at+timedelta(minutes=1)).isoformat()),'SUMMARY:'+escaped(summary),'CLASS:PRIVATE','BEGIN:VALARM','ACTION:DISPLAY','DESCRIPTION:'+escaped(summary),'TRIGGER:PT0S','END:VALARM','END:VEVENT']
    lines+=['END:VCALENDAR'];service.event(p,'calendar_export','private_ics_exported');return ('\r\n'.join(folded(line) for line in lines)+'\r\n').encode()
