"""Install into a NEW directory. Does not start services or touch an existing bot."""
import argparse
import asyncio
import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import urllib.request

REPO = Path(__file__).resolve().parents[1]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def render(value, install_root, group_id, admin_qq, bot_qq):
    if isinstance(value, dict):
        return {k: render(v, install_root, group_id, admin_qq, bot_qq) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, install_root, group_id, admin_qq, bot_qq) for v in value]
    if isinstance(value, str):
        for old, new in [('<INSTALL_ROOT>', install_root.as_posix()),
                         ('10001', group_id), ('10002', admin_qq), ('10003', bot_qq)]:
            value = value.replace(old, new)
    return value


def download(url, target, expected):
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + '.part')
    request = urllib.request.Request(url, headers={'User-Agent': 'whale-qq-bot-installer/0.1'})
    try:
        with urllib.request.urlopen(request, timeout=90) as response, partial.open('wb') as output:
            shutil.copyfileobj(response, output)
        if hashlib.sha256(partial.read_bytes()).hexdigest() != expected:
            raise ValueError('Downloaded model checksum mismatch: ' + target.name)
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)


def install_models(root):
    model_root = root / 'runtime/models'
    manifest = json.loads((REPO / 'provenance/embedding-model.json').read_text(encoding='utf-8'))
    for row in manifest['files']:
        name = row['file']
        upstream_path = 'onnx/' + name if name.endswith('.onnx') else name
        url = f"https://huggingface.co/{manifest['repository']}/resolve/{manifest['commit']}/{upstream_path}"
        download(url, model_root / 'bge-small-zh-v1.5' / name, row['sha256'])
    rows = json.loads((REPO / 'provenance/speech-model.json').read_text(encoding='utf-8'))
    for row in rows:
        if row['file'].endswith('.mp3'):
            continue  # The public demonstration recording is not needed to run the bot.
        url = f"https://modelscope.cn/models/{row['repository']}/resolve/master/{row['file']}"
        download(url, model_root / 'SenseVoiceSmall-onnx' / Path(row['file']).name, row['sha256'])


async def add_persona(data):
    # Fresh database built through the framework API; no production DB is ever copied.
    from astrbot.core.db.sqlite import SQLiteDatabase
    db = SQLiteDatabase(str(data / 'data_v4.db'))
    try:
        await db.initialize()
        await db.insert_persona('whale-v2', (REPO / 'persona/whale-v2.md').read_text(encoding='utf-8'))
    finally:
        await db.engine.dispose()


def prepare(root, group_id, admin_qq, bot_qq, python, api_key, napcat_dir='', references=()):
    # Refuse both existing deployments and accidental runtime data inside the source repository.
    root = root.resolve()
    if root == REPO or root.is_relative_to(REPO):
        raise ValueError('Install outside the source repository.')
    if root.exists() and any(root.iterdir()):
        raise ValueError('Install directory must be empty; existing bots are never overwritten.')
    if not all(x.isdecimal() and 5 <= len(x) <= 12 for x in [group_id, admin_qq, bot_qq]):
        raise ValueError('Group/admin/bot identifiers must be 5-12 decimal digits.')
    if not api_key or api_key.startswith('<'):
        raise ValueError('Set WHALE_API_KEY or enter a key at the hidden prompt.')
    if len(references) not in {0, 2}:
        raise ValueError('Supply both chibi and maid PNG references, or omit both.')
    for source in references:
        source = Path(source)
        if not source.is_file() or source.read_bytes()[:8] != b'\x89PNG\r\n\x1a\n':
            raise ValueError('Both reference images must be valid PNG files.')
    if napcat_dir:
        shell = Path(napcat_dir).resolve()
        if not all((shell / n).is_file() for n in ['napcat.bat', 'napcat.quick.bat']):
            raise ValueError('NapCat directory must contain napcat.bat and napcat.quick.bat.')
    data = root / 'astrbot/data'
    data.mkdir(parents=True, exist_ok=True)
    shutil.copytree(REPO / 'plugins', data / 'plugins', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for file in (REPO / 'templates').glob('*_config.json'):
        config = render(json.loads(file.read_text(encoding='utf-8')), root, group_id, admin_qq, bot_qq)
        if file.name == 'astrbot_plugin_meme_manager_config.json' and not references:
            config['auto_collect']['enabled'] = False
        write_json(data / 'config' / file.name, config)
    config = render(json.loads((REPO / 'templates/cmd_config.json').read_text(encoding='utf-8')), root, group_id, admin_qq, bot_qq)
    config['admins_id'] = [admin_qq]
    config['platform_settings']['id_whitelist'] = [group_id, 'qq-napcat:FriendMessage:' + admin_qq]
    config['platform_settings']['wl_ignore_admin_on_group'] = False
    config['provider_sources'][0]['key'] = [api_key]
    config['provider_settings']['websearch_deepseek_key'] = [api_key]
    config['platform'][0]['ws_reverse_token'] = secrets.token_urlsafe(32)
    dashboard_password = secrets.token_urlsafe(24)
    config['dashboard']['password'] = hashlib.md5(dashboard_password.encode()).hexdigest()
    config['dashboard']['jwt_secret'] = secrets.token_urlsafe(32)
    config['agent_runner']['config']['persona']['persona_id'] = 'whale-v2'
    write_json(data / 'cmd_config.json', config)
    write_json(root / 'dashboard-login.local.json', {'url': 'http://127.0.0.1:6185',
                                                    'username': 'admin', 'password': dashboard_password})
    write_json(data / 'whale-settings.json', {'group_id': group_id, 'admin_qq': admin_qq})
    write_json(root / 'deploy_state.json', {
        'astrbot_root': str(root / 'astrbot'), 'astrbot_python': str(python),
        'astrbot_exe': str(Path(python).parent / 'astrbot.exe'), 'qq': bot_qq,
        'napcat_shell_dir': str(napcat_dir), 'napcat_root': str(napcat_dir)})
    runtime = root / '.bot_runtime'
    runtime.mkdir()
    shutil.copyfile(REPO / 'scripts/bot_manager.py', runtime / 'bot_manager.py')
    shutil.copyfile(REPO / 'scripts/qq_health.py', runtime / 'qq_health.py')
    identity = data / 'plugin_data/astrbot_plugin_group_chat_plus/identity'
    identity.mkdir(parents=True)
    names = []
    for index, source in enumerate(references):
        source = Path(source).resolve()
        name = ['whale-chibi.png', 'whale-maid.png'][index]
        shutil.copyfile(source, identity / name)
        names.append(name)
    write_json(identity / 'references.json', {'images': names, 'source': 'Locally supplied references; not uploaded.'})
    # Make empty, isolated collection packs; no group pictures or learned captions are exported.
    for pack in ['whale-collected', 'whale-private-' + admin_qq]:
        directory = data / 'plugin_data/meme_manager/packs' / pack
        (directory / 'memes').mkdir(parents=True)
        categories = {'happy': '开心', 'angry': '生气', 'sad': '难过',
                      'shy': '害羞', 'confused': '困惑', 'daily': '日常'}
        write_json(directory / 'manifest.json', {'id': pack, 'name': pack, 'version': '1.0.0',
                                                 'categories': categories,
                                                 'description': 'Local user collection; not licensed for redistribution.'})
        write_json(directory / 'whale-approved.json', {})
    pythonw = Path(python).with_name('pythonw.exe')
    # Resolve relative to each launcher, so a shortcut's working directory does not matter.
    for action, name in [('start', '后台启动.vbs'), ('stop', '停止机器人.vbs'), ('status', '查看状态.vbs')]:
        source = ('Option Explicit\r\nDim shell, fso, base, command\r\n'
                  'Set shell = CreateObject("WScript.Shell")\r\n'
                  'Set fso = CreateObject("Scripting.FileSystemObject")\r\n'
                  'base = fso.GetParentFolderName(WScript.ScriptFullName)\r\n'
                  f'command = Chr(34) & "{pythonw}" & Chr(34) & " " & Chr(34) & base & "\\.bot_runtime\\bot_manager.py" & Chr(34) & " {action} --desktop"\r\n'
                  'shell.Run command, 0, False\r\n')
        (root / name).write_text(source, encoding='utf-16')
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-root', type=Path, required=True)
    parser.add_argument('--group-id', required=True)
    parser.add_argument('--admin-qq', required=True)
    parser.add_argument('--bot-qq', required=True)
    parser.add_argument('--napcat-dir', default='')
    parser.add_argument('--reference', action='append', default=[])
    parser.add_argument('--prepare-only', action='store_true', help='Use the current preinstalled Python; no dependency/model download.')
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('This release installer targets Windows.')
    if args.install_root.exists() and any(args.install_root.iterdir()):
        parser.error('Install into a new, empty directory.')
    key = os.environ.get('WHALE_API_KEY') or getpass.getpass('Model API key (hidden): ')
    python = Path(sys.executable)
    if not args.prepare_only:
        # Build outside the final root, because prepare() deliberately accepts empty targets only.
        venv = args.install_root.resolve().parent / (args.install_root.name + '-python')
        if venv.exists():
            parser.error('Sibling Python directory already exists; choose a new install root.')
        subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True)
        python = venv / 'Scripts/python.exe'
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(REPO / 'requirements.txt')], check=True)
    data = prepare(args.install_root, args.group_id, args.admin_qq, args.bot_qq, python, key, args.napcat_dir, args.reference)
    if args.prepare_only:
        os.environ['ASTRBOT_ROOT'] = str(data.parent)
        asyncio.run(add_persona(data))
    else:
        install_models(args.install_root)
        # An installed interpreter supplies framework schema creation and the FFmpeg binary.
        code = ('import os,sys,asyncio;from pathlib import Path;'
                'os.environ["ASTRBOT_ROOT"]=sys.argv[1];'
                'sys.path.insert(0,sys.argv[2]);from install import add_persona;'
                'asyncio.run(add_persona(Path(sys.argv[1])/"data"))')
        subprocess.run([str(python), '-c', code, str(data.parent), str(REPO / 'scripts')], check=True)
        subprocess.run([str(python), '-c',
                        'import imageio_ffmpeg,shutil,sys;from pathlib import Path;'
                        'shutil.copyfile(imageio_ffmpeg.get_ffmpeg_exe(),Path(sys.executable).parent/"ffmpeg.exe")'], check=True)
    # Credentials are deliberately absent from console output.
    print('Prepared a new deployment. No services started. Follow docs/INSTALL.md for login and verification.')


if __name__ == '__main__':
    main()
