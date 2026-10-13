import asyncio
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp

from .security import SourceError


class Drive:
    def __init__(self, token_path):
        self.path = Path(token_path)
        self.access, self.expiry = '', 0
        self.lock = asyncio.Lock()

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=180), trust_env=False)
        return self

    async def __aexit__(self, *args):
        await self.session.close()

    async def token(self):
        async with self.lock:
            if self.expiry > time.time() + 60:
                return self.access
            obj = json.loads(self.path.read_text(encoding='utf-8'))
            async with self.session.post('https://oauth2.googleapis.com/token', data={
                'grant_type': 'refresh_token', 'client_id': obj['client_id'],
                'client_secret': obj['client_secret'], 'refresh_token': obj['refresh_token']}) as r:
                if r.status != 200:
                    raise SourceError('Google OAuth cần đăng nhập lại.')
                token = await r.json()
                self.access, self.expiry = token['access_token'], time.time() + token['expires_in']
            return self.access

    async def api(self, method, path, payload=None, params=None):
        for attempt in range(2):
            async with self.session.request(method, 'https://www.googleapis.com/drive/v3/' + path,
                    json=payload, params=params, headers={'Authorization': 'Bearer ' + await self.token()}) as r:
                if r.status == 401 and attempt == 0:
                    self.expiry = 0
                    continue
                if r.status not in {200, 201, 204}:
                    raise SourceError(f'Google Drive HTTP {r.status}; kiểm tra quyền ghi/quota.')
                return await r.json() if r.status != 204 else {}

    async def folder(self, task_id):
        marker = 'root' if task_id == 'root' else task_id
        found = await self.api('GET', 'files', params={'q': "trashed=false and mimeType='application/vnd.google-apps.folder' and appProperties has { key='payload_bot_folder' and value='" + marker + "' }", 'fields': 'files(id)', 'pageSize': 100})
        if found.get('files'):
            return found['files'][0]['id']
        body = {'name': 'PayloadBot' if marker == 'root' else marker,
                'mimeType': 'application/vnd.google-apps.folder', 'appProperties': {'payload_bot_folder': marker}}
        if marker != 'root':
            body['parents'] = [await self.folder('root')]
        return (await self.api('POST', 'files', body, {'fields': 'id'}))['id']

    async def upload(self, result, task_id, owner, progress=None, public=False):
        if not re.fullmatch(r'[a-f0-9]{12}', task_id):
            raise ValueError('Invalid task')
        path, total = Path(result['path']), result['size']
        if path.stat().st_size != total:
            raise SourceError('Kích thước ảnh thay đổi trước upload.')
        with path.open('rb') as f:
            md5 = await asyncio.to_thread(lambda: hashlib.file_digest(f, 'md5').hexdigest())
        body = {'name': result['name'], 'parents': [await self.folder(task_id)],
                'appProperties': {'payload_task': task_id, 'payload_owner': str(owner), 'sha256': result['sha256']}}
        fields = 'id,name,size,md5Checksum,webViewLink,webContentLink'
        async with self.session.post('https://www.googleapis.com/upload/drive/v3/files',
                params={'uploadType': 'resumable', 'fields': fields}, json=body,
                headers={'Authorization': 'Bearer ' + await self.token(),
                         'X-Upload-Content-Type': 'application/octet-stream', 'X-Upload-Content-Length': str(total)}) as r:
            if r.status != 200:
                raise SourceError(f'Không khởi tạo được upload Drive (HTTP {r.status}).')
            location = r.headers.get('Location', '')
        if urlsplit(location).scheme != 'https' or urlsplit(location).hostname != 'www.googleapis.com':
            raise SourceError('Địa chỉ resumable upload không hợp lệ.')
        offset, failures, query = 0, 0, False
        uploaded = None
        with path.open('rb') as f:
            while uploaded is None:
                f.seek(offset)
                chunk = b'' if query else f.read(min(8 * 1024**2, total-offset))
                content_range = f'bytes */{total}' if query else f'bytes {offset}-{offset+len(chunk)-1}/{total}'
                try:
                    async with self.session.put(location, data=chunk, allow_redirects=False,
                            headers={'Authorization': 'Bearer ' + await self.token(), 'Content-Range': content_range,
                                     'Content-Type': 'application/octet-stream'}) as r:
                        if r.status in {200, 201}:
                            uploaded = await r.json()
                            break
                        if r.status == 401:
                            self.expiry = 0
                            raise aiohttp.ClientConnectionError('refresh')
                        if r.status in {429, 500, 502, 503, 504}:
                            raise aiohttp.ClientConnectionError('retry')
                        if r.status != 308:
                            raise SourceError(f'Upload Drive thất bại (HTTP {r.status}); kiểm tra quota.')
                        match = re.fullmatch(r'bytes=0-(\d+)', r.headers.get('Range', ''))
                        new_offset = int(match.group(1))+1 if match else 0
                        if new_offset < offset or new_offset > total or (not query and new_offset > offset+len(chunk)):
                            raise SourceError('Drive xác nhận sai vùng upload.')
                        if new_offset == offset and not query:
                            raise aiohttp.ClientConnectionError('no progress')
                        if new_offset > offset:
                            failures = 0
                        offset, query = new_offset, False
                        if offset == total:
                            query = True
                        if progress:
                            await progress(offset, total)
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    failures += 1
                    if failures > 5:
                        raise SourceError('Upload bị ngắt; không xác nhận được kết quả trên Drive.') from None
                    query = True
                    await asyncio.sleep(min(2**failures, 20))
        if int(uploaded.get('size', -1)) != total or uploaded.get('md5Checksum') != md5:
            raise SourceError('Checksum/kích thước ảnh trên Google Drive không khớp.')
        if public:
            await self.api('POST', 'files/'+uploaded['id']+'/permissions',
                           {'type':'anyone','role':'reader','allowFileDiscovery':False})
        if progress:
            await progress(total, total)
        return {'id': uploaded['id'], 'name': result['name'], 'size': total, 'sha256': result['sha256'],
                'url': uploaded.get('webContentLink') or 'https://drive.google.com/uc?export=download&id='+uploaded['id'],
                'view_url': uploaded.get('webViewLink') or 'https://drive.google.com/file/d/'+uploaded['id']+'/view'}
