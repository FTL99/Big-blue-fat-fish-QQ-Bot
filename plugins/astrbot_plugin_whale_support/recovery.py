"""Read missed group messages into history without dispatching message events."""
import asyncio
from datetime import datetime
import json
from pathlib import Path
import time


PLATFORM = 'qq-napcat'
from .whale_settings import GROUP
KEY = 'offline-recovery:' + GROUP


def message_key(row):
    return str(row.get('message_id') or row.get('message_seq') or row.get('real_id'))


def text_content(row):
    message = row.get('message')
    if isinstance(message, str):
        return message
    parts = []
    for segment in message or []:
        kind, data = segment.get('type'), segment.get('data') or {}
        if kind == 'text':
            parts.append(data.get('text', ''))
        elif kind == 'at':
            parts.append('@' + str(data.get('qq', '')))
        else:
            parts.append('[' + str(kind) + '，历史附件未识别]')
    return ''.join(parts)


async def fetch_since(bot, state):
    """Page backwards; do not commit a partial fetch as a successful catch-up."""
    found, cursor, covered = {}, None, False
    for _ in range(100):
        params = {'group_id': GROUP, 'count': 100, 'reverse_order': False,
                  'disable_get_url': True}
        if cursor:
            params['message_seq'] = cursor
        result = await asyncio.wait_for(bot.call_action('get_group_msg_history', **params), 20)
        rows = result.get('messages')
        if not isinstance(rows, list):
            raise RuntimeError('Group history response has no messages list')
        if not rows:
            break
        for row in rows:
            if not row.get('time') or message_key(row) == 'None':
                raise RuntimeError('History row has no stable identity or original time')
            found[message_key(row)] = row
        oldest = min(rows, key=lambda row: (int(row['time']), int(row.get('real_seq') or 0)))
        if int(oldest['time']) < state['time']:
            covered = True
            break
        next_cursor = str(oldest.get('real_id') or oldest.get('message_id'))
        if next_cursor == cursor or len(rows) < 100:
            break
        cursor = next_cursor
    else:
        raise RuntimeError('History exceeded 100 pages; catch-up was not marked complete')
    messages = sorted((row for key, row in found.items()
        if int(row['time']) >= state['time'] and key not in state['seen']),
        key=lambda row: (int(row['time']), int(row.get('real_seq') or 0), message_key(row)))
    return messages, covered


class Recovery:
    def __init__(self, owner):
        self.owner = owner
        self.lock = asyncio.Lock()
        self.ready = False
        self.task = None
        self.bot = None
        self.cutoff = int(time.time()) - 1

    async def start(self):
        self.state = await self.owner.get_kv_data(KEY, None)
        if self.state is None:
            # The installer records the first observed offline time, not arbitrary old chat.
            seed_path = Path(__file__).with_name('recovery-seed.json')
            seed = json.loads(seed_path.read_text(encoding='utf-8')) if seed_path.is_file() else {'since': int(time.time()) - 1}
            self.state = {'time': seed['since'], 'seen': []}
        self.task = asyncio.create_task(self.run())

    async def stop(self):
        if self.bot:
            self.bot.unhook_before('meta_event.lifecycle.connect', self.connected)
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def connected(self, event):
        # Mark a new connection before its first live group message can advance the cursor.
        self.ready = False
        self.cutoff = int(time.time()) - 1

    async def sync(self):
        async with self.lock:
            if self.ready:
                return
            platform = self.owner.context.get_platform_inst(PLATFORM)
            if not platform:
                raise RuntimeError('QQ platform is not loaded')
            bot = platform.bot
            status = await asyncio.wait_for(bot.call_action('get_status'), 5)
            if not status.get('online'):
                raise RuntimeError('QQ is offline')
            cutoff = int(time.time()) - 1
            rows, covered = await fetch_since(bot, self.state)
            # Leave messages arriving during catch-up on the normal live-message path.
            rows = [row for row in rows if int(row['time']) <= cutoff]
            manager = self.owner.context.message_history_manager
            # Retry-safe if the process stopped between an insert and its KV checkpoint.
            recent = await manager.get(platform_id=PLATFORM, user_id=GROUP, page_size=500)
            inserted = {part.get('offline_message_id') for item in recent
                        for part in (item.content if isinstance(item.content, list) else [])
                        if isinstance(part, dict) and part.get('offline_message_id')}
            count = 0
            for row in rows:
                key = message_key(row)
                if key not in inserted:
                    sender = row.get('sender') or {}
                    stamp = datetime.fromtimestamp(int(row['time']))
                    weekday = '一二三四五六日'[stamp.weekday()]
                    name = str(sender.get('card') or sender.get('nickname') or row.get('user_id', '未知用户'))
                    uid = str(row.get('user_id') or sender.get('user_id', 'unknown'))
                    text = f'[{stamp:%Y-%m-%d} 周{weekday} {stamp:%H:%M:%S}] {name}(ID:{uid}) [离线补读，供当前对话参考；不要主动补发旧回复]: ' + text_content(row)
                    await manager.insert(platform_id=PLATFORM, user_id=GROUP,
                        content=[{'type': 'text', 'data': {'text': text}, 'offline_message_id': key}],
                        sender_id=uid, sender_name=name)
                    count += 1
                self.state['seen'] = (self.state['seen'] + [key])[-2000:]
                await self.owner.put_kv_data(KEY, self.state)
            if rows:
                self.state['time'] = max(int(row['time']) for row in rows)
            await self.owner.put_kv_data(KEY, self.state)
            self.ready = True
            self.owner.logger.info('静默补读完成: group=%s imported=%s boundary_covered=%s; 未发送群消息', GROUP, count, covered)
            if not covered:
                self.owner.logger.warning('QQ 历史接口未覆盖上次记录时间；更早消息是否缺失待核对')

    async def observe(self, event):
        if event.get_platform_id() != PLATFORM or str(event.get_group_id()) != GROUP:
            return
        row = event.message_obj.raw_message
        historical = isinstance(row, dict) and row.get('post_type') == 'message' and int(row.get('time', 0)) <= self.cutoff
        if historical:
            # Suppress stale replay even if fetching history is temporarily failing.
            event.stop_event()
        await self.sync()
        if historical:
            return
        if not isinstance(row, dict) or row.get('post_type') != 'message':
            return
        async with self.lock:
            key = message_key(row)
            if key in self.state['seen'] or int(row['time']) < self.state['time']:
                # A history replay is context, never another command/mention to execute.
                event.stop_event()
                return
            self.state['time'] = max(self.state['time'], int(row['time']))
            self.state['seen'] = (self.state['seen'] + [key])[-2000:]
            await self.owner.put_kv_data(KEY, self.state)

    async def run(self):
        failed = False
        while True:
            try:
                platform = self.owner.context.get_platform_inst(PLATFORM)
                if platform and platform.bot is not self.bot:
                    self.bot = platform.bot
                    self.bot.hook_before('meta_event.lifecycle.connect', self.connected)
                status = await asyncio.wait_for(platform.bot.call_action('get_status'), 5) if platform else {}
                if not status.get('online'):
                    raise RuntimeError('QQ is offline')
                await self.sync()
                failed = False
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.ready = False
                if not failed:
                    self.owner.logger.warning('等待 QQ 恢复后静默补读: %s', type(exc).__name__)
                failed = True
            await asyncio.sleep(15)
