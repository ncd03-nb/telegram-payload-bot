"""Bounded public IAS3 sandbox benchmark, compared with private Drive upload."""
import asyncio
import base64
import hashlib
import json
import os
import statistics
import sys
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.gdrive import Drive


def archive_url(value):
    p = urlsplit(value)
    if (p.scheme != 'https' or not p.hostname or
            not (p.hostname == 'archive.org' or p.hostname.endswith('.archive.org'))
            or p.username or p.password or p.port not in (None, 443)):
        raise ValueError('Refused non-Archive HTTPS destination')
    return value


def sample(path, size_mib):
    sha, md5 = hashlib.sha256(), hashlib.md5()
    with path.open('wb') as f:
        for _ in range(size_mib):
            block = os.urandom(1024**2)
            f.write(block); sha.update(block); md5.update(block)
    return {'path': str(path), 'name': path.name, 'size': path.stat().st_size,
            'sha256': sha.hexdigest(), 'md5': md5.hexdigest()}


async def body(path):
    with path.open('rb') as f:
        while chunk := await asyncio.to_thread(f.read, 4*1024**2):
            yield chunk


async def transfer(session, method, url, headers, path=None):
    for _ in range(5):
        archive_url(url)
        async with session.request(method, url, headers=headers,
                data=body(path) if path else None, allow_redirects=False,
                expect100=bool(path)) as r:
            if r.status in (307, 308):
                url = archive_url(urljoin(url, r.headers.get('Location', '')))
                continue
            await r.content.read(65536)
            if r.status not in (200, 201, 204):
                raise RuntimeError('IAS3 HTTP '+str(r.status))
            return r.status
    raise RuntimeError('IAS3 redirect limit')


async def ready(session, identifier, file, wait=600):
    started = time.monotonic()
    while time.monotonic()-started < wait:
        async with session.get('https://archive.org/metadata/'+identifier) as r:
            obj = await r.json(content_type=None) if r.status == 200 else {}
        found = next((x for x in obj.get('files', []) if x.get('name') == file['name']), None)
        if found and int(found.get('size', -1)) == file['size'] and found.get('md5') == file['md5']:
            url = 'https://archive.org/download/'+identifier+'/'+quote(file['name'])
            for _ in range(5):
                archive_url(url)
                async with session.get(url, headers={'Range': 'bytes=0-31'}, allow_redirects=False) as r:
                    if r.status in (301, 302, 303, 307, 308):
                        url = archive_url(urljoin(url, r.headers.get('Location', ''))); continue
                    data = await r.content.read(33)
                    with Path(file['path']).open('rb') as local:
                        ranged = r.status == 206 and r.headers.get('Content-Range', '').startswith('bytes 0-31/') and data == local.read(32)
                    return {'verified': True, 'range_ok': ranged,
                            'ready_seconds': round(time.monotonic()-started, 3)}
        await asyncio.sleep(10)
    return {'verified': False, 'range_ok': False, 'ready_seconds': round(time.monotonic()-started, 3)}


async def drive_baseline(file, token):
    marker = uuid.uuid4().hex[:12]
    async with Drive(token, chunk_mib=32) as drive:
        folder = await drive.folder(marker)
        uploaded = None
        started = time.monotonic()
        try:
            uploaded = await drive.upload(file, marker, 1, public=False)
            seconds = time.monotonic()-started
            return {'destination': 'drive', 'bytes': file['size'], 'upload_seconds': round(seconds, 3),
                    'mib_s': round(file['size']/1024**2/seconds, 2), 'verified': True}
        finally:
            if uploaded:
                await drive.api('PATCH', 'files/'+uploaded['id'], {'trashed': True})
            await drive.api('PATCH', 'files/'+folder, {'trashed': True})


async def main():
    access, secret = os.environ['IA_ACCESS_KEY'].strip(), os.environ['IA_SECRET_KEY'].strip()
    if not access or not secret or any(c in access+secret for c in '\r\n:'):
        raise ValueError('Missing or invalid IA credentials')
    report = {'samples': [], 'recommended': False, 'threshold_mib_s': 15}
    result_path = Path(os.getenv('RESULT_PATH', 'archive-benchmark.json'))
    timeout = aiohttp.ClientTimeout(total=1800, connect=30, sock_read=180)
    try:
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            token = folder/'google-token.json'
            token.write_bytes(base64.b64decode(os.environ['GOOGLE_TOKEN_B64'], validate=True)); token.chmod(0o600)
            file = await asyncio.to_thread(sample, folder/'network-test-512m.bin', 512)
            try:
                baseline = await drive_baseline(file, token)
                report['samples'].append(baseline); print(json.dumps(baseline), flush=True)
            except Exception as error:
                report['baseline_error'] = type(error).__name__
            async with aiohttp.ClientSession(timeout=timeout, trust_env=False, headers={'User-Agent':'TelegramBot-UploadBenchmark/1.0'}) as session:
                for index in range(2):
                    identifier = 'bot-upload-test-'+uuid.uuid4().hex
                    url = 'https://s3.us.archive.org/'+identifier+'/'+file['name']
                    auth = {'Authorization': 'LOW '+access+':'+secret}
                    headers = {**auth, 'Content-Length': str(file['size']),
                        'Content-Type':'application/octet-stream',
                        'Content-MD5':base64.b64encode(bytes.fromhex(file['md5'])).decode(),
                        'x-archive-auto-make-bucket':'1', 'x-archive-queue-derive':'0',
                        'x-archive-meta-collection':'test_collection',
                        'x-archive-meta-mediatype':'data',
                        'x-archive-meta-title':'Temporary network upload benchmark',
                        'x-archive-size-hint':str(file['size'])}
                    started = time.monotonic()
                    row = {'destination':'internet_archive', 'sample':index+1, 'identifier':identifier, 'bytes':file['size']}
                    report['samples'].append(row)
                    try:
                        await transfer(session, 'PUT', url, headers, Path(file['path']))
                        seconds = time.monotonic()-started
                        row.update(upload_seconds=round(seconds,3), mib_s=round(file['size']/1024**2/seconds,2))
                        print(json.dumps(row), flush=True)
                        row.update(await ready(session, identifier, file))
                        row['end_to_end_mib_s'] = round(file['size']/1024**2/(seconds+row['ready_seconds']),2)
                    except Exception as error:
                        row['error'] = str(error) if isinstance(error, RuntimeError) else type(error).__name__
                    finally:
                        # Only our fresh sandbox object; preserve a recoverable old version.
                        try:
                            await transfer(session, 'DELETE', url, {**auth,'x-archive-keep-old-version':'1','x-archive-queue-derive':'0'})
                            row['cleanup_requested'] = True
                        except Exception:
                            row['cleanup_requested'] = False
                        print(json.dumps(row), flush=True)
                    if not row.get('verified') or not row.get('range_ok') or row.get('mib_s',0)<5:
                        break
            rows = [r for r in report['samples'] if r['destination']=='internet_archive']
            baseline = next((r['mib_s'] for r in report['samples'] if r['destination']=='drive'), None)
            report['recommended'] = bool(len(rows)==2 and baseline and all(r.get('verified') and r.get('range_ok') and r.get('ready_seconds',999)<=120 for r in rows)
                and statistics.median(r.get('mib_s',0) for r in rows)>=max(15,baseline*1.2)
                and statistics.median(r.get('end_to_end_mib_s',0) for r in rows)>baseline)
    finally:
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(report,indent=2))
        print(json.dumps({'recommended':report['recommended']}), flush=True)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as error:
        print('Benchmark stopped: '+type(error).__name__, flush=True)
        raise SystemExit(1) from None
