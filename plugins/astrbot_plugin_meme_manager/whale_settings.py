"""Deployment routing; restart AstrBot after changing data/whale-settings.json."""
import json
from pathlib import Path
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

path = Path(get_astrbot_data_path()) / 'whale-settings.json'
data = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
GROUP = str(data.get('group_id', ''))
ADMIN = str(data.get('admin_qq', ''))

if not GROUP.isdecimal() or not ADMIN.isdecimal():
    raise ValueError("Configure data/whale-settings.json before enabling whale extensions.")
