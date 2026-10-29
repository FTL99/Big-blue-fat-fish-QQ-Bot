"""Offline regression with fictional IDs. No model calls, QQ client or service launch."""
import ast
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace as NS

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def check_files():
    count = 0
    for file in REPO.rglob('*.py'):
        ast.parse(file.read_text(encoding='utf-8-sig'), filename=str(file.relative_to(REPO)))
        count += 1
    for file in REPO.rglob('*.json'):
        json.loads(file.read_text(encoding='utf-8-sig'))
    catalog = json.loads((REPO / 'plugins/astrbot_plugin_whale_stickers/catalog.json').read_text(encoding='utf-8'))
    assert len(catalog) == 66
    for row in catalog:
        file = (REPO / 'plugins/astrbot_plugin_whale_stickers' / row['file']).resolve()
        assert file.is_relative_to((REPO / 'plugins/astrbot_plugin_whale_stickers/assets').resolve())
        assert hashlib.sha256(file.read_bytes()).hexdigest() == row['sha256']
    return count


async def check_runtime(temporary):
    os.environ['ASTRBOT_ROOT'] = str(temporary / 'framework-store')
    from astrbot.core import sp
    from astrbot.api.star import Star
    from astrbot.api.event import filter
    from astrbot.api.message_components import At, Plain, Image
    from astrbot.core.provider.entities import ProviderRequest

    install = load('release_install_test', REPO / 'scripts/install.py')
    root = temporary / 'fresh-install'
    data = install.prepare(root, '20001', '20002', '20003', Path(sys.executable), 'fixture-key-not-real')
    await install.add_persona(data)
    import sqlite3
    with sqlite3.connect(data / 'data_v4.db') as db:
        assert db.execute('select system_prompt from personas where persona_id=?', ('whale-v2',)).fetchone()[0] == (REPO / 'persona/whale-v2.md').read_text(encoding='utf-8')
        assert db.execute('select count(*) from conversations').fetchone()[0] == 0
    db.close()
    config = json.loads((data / 'cmd_config.json').read_text(encoding='utf-8'))
    assert all(isinstance(p.get('max_context_tokens'), int) for p in config['provider'] if p.get('provider_source_id'))
    group_config = json.loads((data / 'config/astrbot_plugin_group_chat_plus_config.json').read_text(encoding='utf-8'))
    assert isinstance(group_config['web_panel_reset_password'], bool)
    assert config['provider_sources'][0]['key'] == ['fixture-key-not-real']
    assert config['admins_id'] == ['20002'] and config['platform'][0]['ws_reverse_token']
    assert config['agent_runner']['config']['persona']['persona_id'] == 'whale-v2'
    assert config['platform_settings']['id_whitelist'] == ['20001', 'qq-napcat:FriendMessage:20002']
    assert json.loads((data / 'whale-settings.json').read_text()) == {'group_id': '20001', 'admin_qq': '20002'}
    saved_root = os.environ['ASTRBOT_ROOT']
    try:
        os.environ['ASTRBOT_ROOT'] = str(data.parent)
        for index, path in enumerate((data / 'plugins').glob('*/whale_settings.py')):
            settings = load(f'release_settings_{index}', path)
            assert (settings.GROUP, settings.ADMIN) == ('20001', '20002')
        os.environ['ASTRBOT_ROOT'] = str(temporary / 'unconfigured')
        try:
            load('release_settings_missing', REPO / 'plugins/astrbot_plugin_whale_support/whale_settings.py')
        except ValueError:
            pass
        else:
            raise AssertionError('Missing routing config did not fail closed.')
    finally:
        os.environ['ASTRBOT_ROOT'] = saved_root
    for p in (data / 'config').glob('*.json'):
        assert '10001' not in p.read_text(encoding='utf-8') and '10002' not in p.read_text(encoding='utf-8')
    try:
        install.prepare(root, '20001', '20002', '20003', Path(sys.executable), 'fixture-key-not-real')
    except ValueError:
        pass
    else:
        raise AssertionError('Existing installation was not protected.')
    for bad in ['../../escape', '123', '20001\n']:
        try:
            install.prepare(temporary / 'bad-input', bad, '20002', '20003', Path(sys.executable), 'fixture-key-not-real')
        except ValueError:
            pass
        else:
            raise AssertionError('Invalid group identifier accepted.')
    assert not (temporary / 'bad-input').exists()
    # Validate collection metadata with the actual source validator, not only its JSON shape.
    protocol = load('release_pack_protocol_test', REPO / 'plugins/astrbot_plugin_meme_manager/backend/packs/protocol.py')
    for pack in ['whale-collected', 'whale-private-20002']:
        protocol.validate_pack_manifest(json.loads((data / 'plugin_data/meme_manager/packs' / pack / 'manifest.json').read_text(encoding='utf-8')))
    assert not json.loads((data / 'config/astrbot_plugin_meme_manager_config.json').read_text(encoding='utf-8'))['auto_collect']['enabled']
    refs = [REPO / 'plugins/astrbot_plugin_whale_stickers/assets/w001.png', REPO / 'plugins/astrbot_plugin_whale_stickers/assets/w066.png']
    with_refs = install.prepare(temporary / 'reference-install', '20001', '20002', '20003', Path(sys.executable), 'fixture-key-not-real', references=refs)
    identity = with_refs / 'plugin_data/astrbot_plugin_group_chat_plus/identity'
    assert json.loads((identity / 'references.json').read_text())['images'] == ['whale-chibi.png', 'whale-maid.png']
    assert (identity / 'whale-chibi.png').read_bytes() == refs[0].read_bytes()
    assert (identity / 'whale-maid.png').read_bytes() == refs[1].read_bytes()
    assert json.loads((with_refs / 'config/astrbot_plugin_meme_manager_config.json').read_text(encoding='utf-8'))['auto_collect']['enabled']

    names = load('release_names_test', REPO / 'plugins/astrbot_plugin_whale_support/nicknames.py')
    battle = load('release_battle_test', REPO / 'plugins/astrbot_plugin_whale_stickers/battle.py')
    tree = ast.parse((REPO / 'plugins/astrbot_plugin_whale_support/main.py').read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'WhaleSupport')
    cls.decorator_list = []
    for method in cls.body:
        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            method.decorator_list = []
    serena = load('release_serena_voice_test', REPO / 'plugins/astrbot_plugin_whale_support/voice.py')
    ns = {'SerenaVoice': serena.SerenaVoice, 'Star': Star, 'filter': filter, 'asyncio': asyncio, 'time': time, 'json': json,
          'correction': names.correction, 'GROUP': '20001', 'ADMIN': '20002'}
    unit = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), cls], type_ignores=[])
    exec(compile(ast.fix_missing_locations(unit), 'actual-release-support', 'exec'), ns)
    Support = ns['WhaleSupport']
    Support.plugin_id = 'local/release-preferences-test'

    class Event:
        def __init__(self, chain, private=False, user='20002', meme=True):
            self.private, self.user = private, user
            self.message_obj = NS(message=chain, raw_message={'message': [
                {'type': 'image', 'data': {'sub_type': 1 if meme else 0}} for c in chain if isinstance(c, Image)]})
            self.unified_msg_origin = 'fixture:' + ('private' if private else 'group')
            self.extra, self.sent, self.stopped = {}, [], False
        def get_extra(self, k, d=None): return self.extra.get(k, d)
        def set_extra(self, k, v): self.extra[k] = v
        def get_sender_id(self): return self.user
        def get_self_id(self): return '20003'
        def get_sender_name(self): return '阿甲'
        def get_group_id(self): return '' if self.private else '20001'
        def is_private_chat(self): return self.private
        def plain_result(self, text): return text
        async def send(self, result): self.sent.append(result)
        def stop_event(self): self.stopped = True

    history = [NS(role='user', sender_id='20004', sender_name='阿乙', content='我去买菜', timestamp=time.time()),
               NS(role='assistant', sender_id='20003', sender_name='鱼', content='阿乙还没回来', timestamp=time.time())]
    async def messages(*_, **__): return history
    context = NS(get_registered_star=lambda _: NS(activated=True, star_cls=NS(event_handler=NS(conversation_manager=NS(get_messages=messages)))))
    support = Support(context)
    rename = Event([Plain('叫他小乙就行')])
    await support.rename(rename)
    assert rename.sent == ['记下了，以后叫小乙']
    req = ProviderRequest(system_prompt='旧摘要：阿乙去买菜')
    await Support(context).identity(Event([Plain('他回来了吗')]), req)
    assert '小乙' in req.prompt
    private = ProviderRequest()
    await support.identity(Event([Plain('他回来了吗')], private=True), private)
    assert '当前称呼偏好' not in (private.prompt or '')
    denied = Event([At(qq='20004'), Plain('以后叫他小丙')], user='20005')
    await support.rename(denied)
    assert '管理员' in denied.sent[0]
    for text in ['叫我看看', '叫他过来就行', '以后叫他什么', '以后叫他小乙就行吗']:
        event = Event([Plain(text)])
        await support.rename(event)
        assert not event.sent
    child = 'import asyncio;from astrbot.core import sp;assert asyncio.run(sp.get_async("plugin","local/release-preferences-test","nicknames:fixture:group",{}))["20004"]["name"]=="小乙"'
    result = subprocess.run([sys.executable, '-c', child], env=os.environ.copy(), capture_output=True, timeout=45)
    assert result.returncode == 0, 'Fresh-process preference read failed.'

    now = [0.0]
    image = Image.fromFileSystem(str(REPO / 'plugins/astrbot_plugin_whale_stickers/assets/w066.png'))
    for private_scope in [False, True]:
        for limit in range(2, 6):
            b = battle.Battle(clock=lambda: now[0], pick_limit=lambda: limit)
            b.observe(Event([At(qq='20003')], private=private_scope))
            for turn in range(1, limit + 1):
                event = Event([image], private=private_scope)
                assert b.observe(event)
                assert bool(event.get_extra('_whale_battle_final')) == (turn == limit)
                assert not b.states if turn == 1 else b.states[event.user]['rounds'] == turn - 1
                b.sent(event, f'w{turn:03d}')
                now[0] += 1
            assert not b.states and event.user in b.rest
            assert not b.observe(Event([image], private=private_scope))
    b = battle.Battle(clock=lambda: now[0])
    b.observe(Event([At(qq='20003')]))
    assert not b.observe(Event([image], meme=False))
    b.observe(Event([At(qq='20003')]))
    first = Event([image]); b.observe(first); b.sent(first, 'w001')
    question = Event([Plain('不斗了，认真解释一下Python字典')])
    assert not b.observe(question) and question.get_extra('_whale_battle_question')
    assert not b.states
    # Real GCP routing methods: first reply and topic switch bypass probabilistic suppression.
    tree = ast.parse((REPO / 'plugins/astrbot_plugin_group_chat_plus/main.py').read_text(encoding='utf-8'))
    methods = []
    for name in ['_check_ai_decision', '_check_probability_before_processing']:
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
        node.decorator_list = []; methods.append(node)
    ns = {'logger': NS(info=lambda *_: None)}
    unit = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *methods], type_ignores=[])
    exec(compile(ast.fix_missing_locations(unit), 'actual-release-routing', 'exec'), ns)
    assert await ns['_check_ai_decision'](None, question, '认真问题', False, False)
    assert await ns['_check_probability_before_processing'](None, question, 'aiocqhttp', False, '20001', False, False)
    return {'fresh_install_and_persona': True, 'existing_install_refused': True,
            'input_validation': True, 'collection_pack_schema': True,
            'nickname_persisted_and_fresh_process_read': True, 'group_private_isolation': True,
            'nickname_permissions_and_non_naming_negatives': True,
            'battle_group_and_private_limits_2_to_5': True, 'serious_question_real_route': True}


def main():
    count = check_files()
    async def run_checks(path):
        try:
            return await check_runtime(path)
        finally:
            from astrbot.core import sp
            await sp.close()
            await sp.db_helper.engine.dispose()
    with tempfile.TemporaryDirectory(prefix='whale-release-test-') as tmp:
        checks = asyncio.run(run_checks(Path(tmp)))
    result = {'passed': True, 'python_files_parsed': count, 'sticker_hashes': 66,
              'checks': checks, 'model_api_calls': 0, 'qq_messages_sent': 0,
              'scope': 'Fresh data directory using preinstalled AstrBot dependencies; routing handlers partly extracted from actual source. Not a clean-machine network install or QQ delivery test.'}
    (REPO / 'docs/offline-acceptance.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
