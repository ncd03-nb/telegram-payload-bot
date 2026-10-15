import base64
import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Settings:
    token: str
    users: set
    github_token: str = ''
    github_repo: str = 'ncd03-nb/telegram-payload-bot'
    github_ref: str = 'main'
    data_dir: Path = Path('/data')
    google_token: Path = Path('/config/google-token.json')
    port: int = 8080
    max_output: int = 40 * 1024**3
    public: bool = False
    groups: bool = False
    public_files: bool = False
    max_concurrent: int = 0
    extract_workers: int = 8
    range_batch_mib: int = 8
    decode_workers: int = 2
    drive_chunk_mib: int = 32
    hub_url: str = ''
    hub_role: str = 'docker'
    hub_bot: str = 'payload'

    @classmethod
    def load(cls):
        s = cls(token=os.getenv('TELEGRAM_BOT_TOKEN', ''),
            users={int(x) for x in os.getenv('ALLOWED_USER_IDS', '').split(',') if x.strip()},
            github_token=os.getenv('ACTIONS_DISPATCH_TOKEN', ''),
            github_repo=os.getenv('ACTIONS_REPO', 'ncd03-nb/telegram-payload-bot'),
            github_ref=os.getenv('ACTIONS_REF', 'main'),
            data_dir=Path(os.getenv('DATA_DIR', '/data')),
            google_token=Path(os.getenv('GOOGLE_TOKEN_PATH', '/config/google-token.json')),
            port=int(os.getenv('PORT', '8080')),
            max_output=int(os.getenv('MAX_OUTPUT_GIB', '40')) * 1024**3,
            public=os.getenv('PUBLIC_BOT','false').lower() in {'1','true','yes'},
            groups=os.getenv('ALLOW_GROUPS','false').lower() in {'1','true','yes'},
            public_files=os.getenv('PUBLIC_FILES','false').lower() in {'1','true','yes'},
            max_concurrent=int(os.getenv('MAX_CONCURRENT_JOBS','0')),
            extract_workers=int(os.getenv('EXTRACT_WORKERS','8')),
            range_batch_mib=int(os.getenv('RANGE_BATCH_MIB','8')),
            decode_workers=int(os.getenv('DECODE_WORKERS','2')),
            drive_chunk_mib=int(os.getenv('DRIVE_CHUNK_MIB','32')),
            hub_url=os.getenv('HUB_URL','').rstrip('/'),
            hub_role=os.getenv('HUB_ROLE','docker'),
            hub_bot=os.getenv('HUB_BOT_ID','payload'))
        if not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', s.token) or (not s.public and not s.users):
            raise ValueError('Thiếu token bot hoặc ALLOWED_USER_IDS.')
        if not re.fullmatch(r'[\w.-]+/[\w.-]+', s.github_repo) or not 0 < s.max_output <= 100 * 1024**3:
            raise ValueError('Repo hoặc giới hạn dung lượng không hợp lệ.')
        if not 0 <= s.max_concurrent <= 20:
            raise ValueError('MAX_CONCURRENT_JOBS phải nằm trong khoảng 0–20; 0 là không giới hạn chạy song song tại bot.')
        if not (1 <= s.extract_workers <= 16 and 1 <= s.range_batch_mib <= 32
                and 1 <= s.decode_workers <= 4 and 1 <= s.drive_chunk_mib <= 256):
            raise ValueError('Cấu hình pipeline trích xuất/upload không hợp lệ.')
        from .failover import validate_hub
        validate_hub(s)
        s.data_dir.mkdir(parents=True, exist_ok=True)
        if os.getenv('GOOGLE_TOKEN_B64') and not s.google_token.exists():
            s.google_token.parent.mkdir(parents=True, exist_ok=True)
            s.google_token.write_bytes(base64.b64decode(os.environ['GOOGLE_TOKEN_B64'], validate=True))
            s.google_token.chmod(0o600)
        return s
