"""Short, per-sender sticker exchanges. State advances only after a sent image."""
import time
import random
import re

from astrbot.api.message_components import At, Image, Plain, Reply


class Battle:
    def __init__(self, clock=time.monotonic, pick_limit=None):
        self.clock = clock
        self.pick_limit = pick_limit or (lambda: random.randint(2, 5))
        self.states = {}
        self.rest = {}
        self.recent = []
        self.pending = {}

    @staticmethod
    def marked_meme(event):
        # Same OneBot metadata used by Group Chat Plus; collection/character approval is unrelated.
        raw = getattr(event.message_obj, 'raw_message', None)
        segments = raw.get('message', []) if isinstance(raw, dict) else []
        return any(s.get('type') == 'image' and (
            str(s.get('data', {}).get('sub_type', s.get('data', {}).get('subType', ''))) == '1'
            or any(word in str(s.get('data', {}).get('summary', '')).lower() for word in ['表情', 'emoji', 'sticker'])
            or s.get('data', {}).get('emoji_id')) for s in segments if isinstance(s, dict))

    def observe(self, event):
        now = self.clock()
        self.states = {k: v for k, v in self.states.items() if now - v['time'] < 120}
        self.rest = {k: v for k, v in self.rest.items() if v > now}
        self.pending = {k: v for k, v in self.pending.items() if v > now}
        sender = str(event.get_sender_id())
        own = str(event.get_self_id())
        chain = event.message_obj.message
        text = ''.join(c.text or '' for c in chain if isinstance(c, Plain)).strip()
        images = any(isinstance(c, Image) for c in chain)
        if sender == own:
            return False
        other = any((isinstance(c, At) and str(c.qq) not in {own, '0', 'all'})
                    or (isinstance(c, Reply) and str(c.sender_id) not in {own, '0', '', 'None'}) for c in chain)
        engaged = sender in self.states or sender in self.rest or sender in self.pending
        question = bool(re.search(r'认真|请问|帮我|解释|为什么|怎么|如何|是什么|[?？]', text))
        if any(word in text for word in ['不斗了', '不玩了', '别发了', '停止斗图', '别斗了', '停一下', '换个话题', '别发图']):
            self.states.pop(sender, None)
            self.pending.pop(sender, None)
            self.rest[sender] = now + 60
            if not question:
                event.set_extra('_whale_battle_stop', True)
            elif engaged and not other:
                event.set_extra('_whale_battle_question', True)
            return False
        directed = any((isinstance(c, At) and str(c.qq) == own)
                       or (isinstance(c, Reply) and str(c.sender_id) == own) for c in chain)
        invitation = any(word in text for word in ['斗图', '接招', '再来'])
        if other or question:
            self.states.pop(sender, None)
            self.pending.pop(sender, None)
            if question and engaged and not other:
                event.set_extra('_whale_battle_question', True)
            return False
        if not images:
            if directed or invitation:
                self.pending[sender] = now + 30
            else:
                self.states.pop(sender, None)
                self.pending.pop(sender, None)
            return False
        directed = directed or any(word in text for word in ['斗图', '接招', '小鲸鱼', '大肥鱼'])
        directed = directed or getattr(event, 'is_private_chat', lambda: False)()
        if sender in self.rest:
            event.set_extra('_whale_battle_rest', True)
            return False
        previous = self.states.get(sender)
        meme = self.marked_meme(event)
        if not meme and not invitation:
            self.states.pop(sender, None)
            self.pending.pop(sender, None)
            return False
        start = bool(meme and (directed or sender in self.pending) or invitation)
        candidate = bool(start or previous)
        event.set_extra('_whale_battle_eligible', meme or invitation)
        event.set_extra('_whale_battle_candidate', candidate)
        event.set_extra('_whale_battle_start', start and not previous)
        if candidate:
            limit = previous['limit'] if previous else self.pick_limit()
            turn = previous['rounds'] + 1 if previous else 1
            event.set_extra('_whale_battle_continue', bool(previous))
            event.set_extra('_whale_battle_limit', limit)
            event.set_extra('_whale_battle_final', turn >= limit)
        return candidate

    def sent(self, event, sticker_id):
        sender = str(event.get_sender_id())
        previous = self.states.get(sender)
        if sticker_id:
            self.recent = (self.recent + [sticker_id])[-5:]
            self.pending[sender] = self.clock() + 30
        elif not ((previous and event.get_extra('_whale_battle_continue', False)) or event.get_extra('_whale_battle_start', False)):
            return
        if not (event.get_extra('_whale_battle_candidate', False) or event.get_extra('_whale_battle_eligible', False)):
            return
        rounds = previous['rounds'] + 1 if previous else 1
        limit = previous['limit'] if previous else event.get_extra('_whale_battle_limit')
        if limit is None:
            limit = self.pick_limit()
        if rounds >= limit:
            self.states.pop(sender, None)
            self.rest[sender] = self.clock() + 60
        else:
            self.states[sender] = {'time': self.clock(), 'rounds': rounds, 'limit': limit, 'sticker': sticker_id}
