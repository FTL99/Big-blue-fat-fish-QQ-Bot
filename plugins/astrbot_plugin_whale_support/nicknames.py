"""Resolve explicit naming requests against recent, identified conversation members."""
import re
import time

from astrbot.api.message_components import At, Plain, Reply


def correction(event, messages, preferences):
    text=''.join(c.text or '' for c in event.message_obj.message if isinstance(c,Plain)).strip()
    intent=re.sub(r'[“"‘\'][^”"’\']+[”"’\']','',text)
    if re.search(r'[?？]|什么|怎么|如何|是否|能否|(?:吗|呢|么)$',intent):
        return None
    if not (text.startswith(('以后','今后','往后','称呼')) or re.search(r'就行|就好|即可|好了',text)):
        return None
    match=re.fullmatch(r'(?:以后|今后|往后)?(?:就)?(?:叫|称呼)(.+?)(?:就行|就好|即可|好了|吧)?[。！!]*',text)
    if not match:return None
    body=match[1].strip()
    own,actor=str(event.get_self_id()),str(event.get_sender_id())
    people={actor:event.get_sender_name() or actor}
    for msg in messages:
        if msg.role=='user' and str(msg.sender_id) not in {'',own}:
            people[str(msg.sender_id)]=msg.sender_name or str(msg.sender_id)
    known={uid:{name,preferences.get(uid,{}).get('name','')} for uid,name in people.items()}
    targets={str(c.qq) for c in event.message_obj.message if isinstance(c,At) and str(c.qq) not in {own,'0','all'}}
    quotes=[c for c in event.message_obj.message if isinstance(c,Reply)]
    if not targets and quotes:
        targets={str(c.sender_id) for c in quotes if str(c.sender_id) in people and str(c.sender_id)!=own}
    if targets:
        rest=re.sub(r'^(?:他|她|TA|ta)(?:为)?','',body)
    elif body.startswith('我'):
        targets={actor};rest=body[1:]
    elif body.startswith(('他','她','TA','ta')):
        rest=re.sub(r'^(?:他|她|TA|ta)(?:为)?','',body)
        now=getattr(event,'created_at',time.time())
        last=next((m for m in reversed(messages) if m.role=='assistant' and 0<=now-m.timestamp<300),None)
        reference=last.content if last else ''
        if quotes:
            reference=' '.join(c.message_str or ''.join(x.text or '' for x in (c.chain or []) if isinstance(x,Plain)) for c in quotes)
        targets={uid for uid,names in known.items() if uid!=actor and any(n and n in reference for n in names)}
    else:
        named=[(uid,n) for uid,names in known.items() for n in names if n and body.startswith(n)]
        if len({uid for uid,_ in named})!=1:return None
        uid,name=max(named,key=lambda row:len(row[1]));targets={uid};rest=body[len(name):]
    nickname=rest.removeprefix('为').strip(' “”“\"\'')
    if nickname in {'来','过来','回去','出去','进来','看看','听听','想想','帮忙'}:
        return None
    if not re.fullmatch(r'[A-Za-z0-9_\u4e00-\u9fff]{1,16}',nickname):return None
    if len(targets)!=1:return {'clarify':True}
    target=next(iter(targets))
    if target not in people:return {'clarify':True}
    return {'target':target,'name':nickname,'original':people[target]}
