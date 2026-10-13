import asyncio
import json
import logging
import time
import uuid

import aiohttp
from aiohttp import web

from .actions import Actions
from .payload import Payload
from .remote import Remote
from .runner import selection, send_file
from .security import SourceError, validate_url
from .settings import Settings
from .store import Store
from .telegram import Telegram

HELP = '''🤖 Payload → IMG → Google Drive
Gửi link ZIP OTA/payload.bin hoặc /list <link> để xem phân vùng.
Nhiều phân vùng → 1 ZIP, 1 nút tải. Một phân vùng → .img.
/extract <mã ROM hoặc link> boot,init_boot,vbmeta
/extract <mã ROM> all — xuất mọi phân vùng (tối đa 40 GiB)
/status [mã tác vụ]
/cancel <mã tác vụ>
/files <mã tác vụ> — lấy lại nút tải
Nhận lệnh và link trực tiếp trong chat riêng/group, không cần @username.
ROM full OTA được hỗ trợ; delta cần ảnh gốc sẽ bị từ chối.'''
log = logging.getLogger('payloadbot')


class Bot:
    def __init__(self, settings):
        self.s = settings
        self.tg, self.actions = Telegram(settings.token), Actions(settings)
        self.store = Store(settings.data_dir / 'jobs.sqlite3', recover=False)
        self.store.db.executescript('''CREATE TABLE IF NOT EXISTS scans(id TEXT PRIMARY KEY, owner INTEGER, url TEXT, metadata TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS outputs(id TEXT PRIMARY KEY, owner INTEGER, files TEXT);''')
        self.tasks = []
        self.lock = None
        self.username = ''
        self.scan_times = {}
        self.job_tasks = {}

    async def start(self, app):
        if not self.s.github_token:
            raise ValueError('Thiếu ACTIONS_DISPATCH_TOKEN.')
        import fcntl
        self.lock = (self.s.data_dir / 'instance.lock').open('a+b')
        fcntl.flock(self.lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.store.recover()
        await self.tg.start(); await self.actions.start()
        try:
            identity = await self.tg.call('getMe')
            self.username = identity['username'].lower()
            await self.tg.call('deleteWebhook', drop_pending_updates=False)
            await self.tg.call('setMyCommands', commands=[
                {'command':'start','description':'Hướng dẫn dùng bot'},
                {'command':'list','description':'Xem phân vùng từ link ROM'},
                {'command':'extract','description':'Trích xuất .img sang Google Drive'},
                {'command':'status','description':'Trạng thái tác vụ'},
                {'command':'cancel','description':'Hủy tác vụ'},
                {'command':'files','description':'Lấy lại nút tải ZIP/.img'}])
        except BaseException:
            await self.tg.close(); await self.actions.close()
            raise
        self.tasks = [asyncio.create_task(self.poll()), asyncio.create_task(self.updates()), asyncio.create_task(self.worker())]

    async def close(self, app):
        for t in self.tasks: t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.tg.session: await self.tg.close()
        await self.actions.close(); self.store.close()
        if self.lock: self.lock.close()

    async def poll(self):
        offset = 0
        while True:
            try:
                for u in await self.tg.call('getUpdates', offset=offset, timeout=30, allowed_updates=['message']):
                    m = u.get('message', {})
                    if self.accepts(m):
                        self.store.ingest(u)
                    offset = u['update_id'] + 1
            except Exception as e:
                log.warning('Polling retry: %s', type(e).__name__)
                await asyncio.sleep(5)

    def accepts(self, message):
        sender, chat = message.get('from', {}), message.get('chat', {})
        owner = sender.get('id', 0)
        if owner <= 0 or sender.get('is_bot') or (not self.s.public and owner not in self.s.users):
            return False
        if chat.get('type') == 'private':
            return chat.get('id') == owner
        return self.s.groups and chat.get('type') in {'group', 'supergroup'} and chat.get('id', 0) < 0

    async def tell(self, chat, text, reply_to=None, thread_id=None):
        try: return await self.tg.send(chat, text, reply_to=reply_to, thread_id=thread_id)
        except Exception: return None

    async def reply(self, message, text):
        return await self.tell(message['chat']['id'], text, reply_to=message.get('message_id'),
            thread_id=message.get('message_thread_id'))

    async def updates(self):
        while True:
            update = self.store.next_update()
            if not update:
                await asyncio.sleep(.5); continue
            try:
                await self.handle(update)
            except (SourceError, ValueError) as e:
                await self.reply(update['message'], str(e))
            except Exception as e:
                log.warning('Command failed: %s', type(e).__name__)
                await self.reply(update['message'], 'Lệnh thất bại; kiểm tra /status rồi thử lại.')
            self.store.finish_update(update['update_id'])

    async def scan(self, owner, url):
        now = time.monotonic()
        recent = [t for t in self.scan_times.get(owner, []) if now-t < 60]
        if len(recent) >= 3:
            raise SourceError('Tối đa 3 lần đọc ROM mỗi phút cho mỗi người; đợi một chút rồi thử lại.')
        if len(self.scan_times) >= 1024:
            self.scan_times = {k:v for k,v in self.scan_times.items() if v and now-v[-1] < 60}
        self.scan_times[owner] = recent + [now]
        async with asyncio.timeout(180):
            async with Remote(url) as remote:
                payload = await Payload(remote).load()
                rows = payload.summary()
        sid = uuid.uuid4().hex[:12]
        with self.store.db:
            self.store.db.execute('DELETE FROM scans WHERE created<?', (time.time()-86400,))
            self.store.db.execute('INSERT INTO scans VALUES(?,?,?,?,?)', (sid, owner, url, json.dumps(rows), time.time()))
        return sid, rows

    async def handle(self, update):
        m = update['message']; owner, chat = m['from']['id'], m['chat']['id']
        if not self.accepts(m): return
        async def respond(text):
            return await self.reply(m, text)
        parts = m.get('text', '').strip().replace('https\\://', 'https://').split(maxsplit=1)
        if not parts: return
        head = parts[0]
        if head.startswith('/') and '@' in head and head.split('@', 1)[1].lower() != self.username:
            return
        command, args = head.split('@')[0].lower(), parts[1] if len(parts)>1 else ''
        if command in {'/start', '/help'}:
            await respond(HELP + ('\nFile kết quả: ai có link đều có thể tải.' if self.s.public_files else '\nFile kết quả chỉ tải được bằng tài khoản Drive đã kết nối.'))
        elif command in {'/list', '/partitions'} or command.startswith(('https://', 'http://')):
            url = validate_url(args.strip() if command.startswith('/') else m['text'].strip().replace('https\\://','https://'))
            await respond('🔎 Đang đọc danh sách phân vùng…')
            sid, rows = await self.scan(owner, url)
            listing = '\n'.join(f"{p['name']} — {p['size']/1024**2:.1f} MiB" + ('' if p['supported'] else ' [delta]') for p in rows)
            await respond(f'📦 ROM {sid} — {len(rows)} phân vùng\nDùng /extract {sid} boot,init_boot,vbmeta\nMã ROM có hiệu lực 24 giờ.')
            for start in range(0, len(listing), 3800):
                await respond(listing[start:start+3800])
        elif command == '/extract':
            values = args.split(maxsplit=1)
            if len(values) != 2:
                raise SourceError('Dùng /extract <mã ROM hoặc link> boot,init_boot,vbmeta')
            source, requested = values
            if source.startswith(('http://','https://')):
                sid, rows = await self.scan(owner, validate_url(source))
                url = source
            else:
                row = self.store.db.execute('SELECT * FROM scans WHERE id=? AND owner=? AND created>?', (source,owner,time.time()-86400)).fetchone()
                if not row: raise SourceError('Mã ROM không tồn tại/hết hạn; gửi lại link ROM.')
                url, rows = row['url'], json.loads(row['metadata'])
            chosen = selection(requested, {p['name']:p for p in rows})
            if sum(p['size'] for p in rows if p['name'] in chosen) > self.s.max_output:
                raise SourceError('Tổng ảnh vượt MAX_OUTPUT_GIB, hãy chọn ít phân vùng hơn.')
            job = self.store.add(update['update_id'],owner,chat,url,','.join(chosen),backend='actions')
            if not job['message_id']:
                self.store.change(job['id'],phase='creating_message',thread_id=m.get('message_thread_id',0),request_message_id=m.get('message_id',0))
                sent = await respond('⏳ '+job['id']+' — Chờ lượt xử lý trong bot\nTiến độ: 0%')
                self.store.change(job['id'], message_id=sent['message_id'] if sent else None,phase='queued')
        elif command == '/status':
            jobs = [self.store.get(args.strip(),owner)] if args else self.store.recent(owner)
            await respond('\n\n'.join(f"{j['id']} — {j['state']}\n{j.get('phase') or ''}\n{j.get('error') or ''}" for j in jobs if j) or 'Chưa có tác vụ.')
        elif command in {'/files','/link','/dl'}:
            row = self.store.db.execute('SELECT files FROM outputs WHERE id=? AND owner=?',(args.strip(),owner)).fetchone()
            if not row: raise SourceError('Chưa có kết quả. Dùng /status để lấy mã tác vụ.')
            for file in json.loads(row['files']):
                await send_file(self.tg,chat,file,reply_to=m.get('message_id'),thread_id=m.get('message_thread_id'))
        elif command == '/cancel':
            job = self.store.get(args.strip(),owner)
            if not job or job['state'] not in {'running','queued'}: raise SourceError('Không có tác vụ đang chạy thuộc bạn.')
            if job['dispatch_at']:
                run = {'id':job['run_id']} if job['run_id'] else await self.actions.find_run(job)
                if not run: raise SourceError('Runner chưa xuất hiện; đợi vài giây rồi hủy lại.')
                await self.actions.cancel(run['id'])
            self.store.change(job['id'],state='cancelled',url='')
            await respond('Đã yêu cầu hủy '+job['id']+'. File đã upload trước đó vẫn còn trên Drive.')
        elif m['chat']['type'] == 'private':
            await respond(HELP)

    async def worker(self):
        try:
            while True:
                for jid, task in list(self.job_tasks.items()):
                    if task.done():
                        if not task.cancelled() and task.exception():
                            log.warning('Job task ended: %s', type(task.exception()).__name__)
                        del self.job_tasks[jid]
                while not self.s.max_concurrent or len(self.job_tasks) < self.s.max_concurrent:
                    job = self.store.claim(exclude=self.job_tasks)
                    if not job:
                        break
                    self.job_tasks[job['id']] = asyncio.create_task(self.process_job(job))
                await asyncio.sleep(.5)
        finally:
            for task in self.job_tasks.values(): task.cancel()
            await asyncio.gather(*self.job_tasks.values(), return_exceptions=True)
            self.job_tasks.clear()

    async def process_job(self, job):
        try:
            job = self.store.get(job['id'],job['owner'])
            if job['state'] != 'running': return
            if not job['message_id']:
                sent = await self.tg.send(job['chat'],'⏳ '+job['id']+' — Chuẩn bị gửi GitHub Actions',thread_id=job.get('thread_id'),reply_to=job.get('request_message_id'))
                self.store.change(job['id'],message_id=sent['message_id'])
                job = self.store.get(job['id'],job['owner'])
                if job['state'] != 'running': return
            if not job['dispatch_at']:
                self.store.change(job['id'],dispatch_at=time.time(),phase='dispatching')
                try:
                    response = await self.actions.dispatch(job)
                    if response.get('workflow_run_id'): self.store.change(job['id'],run_id=response['workflow_run_id'])
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    pass
                # Never retry ambiguous dispatch: reconcile the unique run-name.
                self.store.change(job['id'],url='')
                try:
                    await self.tg.edit(job['chat'],job['message_id'],'⏳ '+job['id']+' — Đã gửi GitHub Actions; chờ cấp runner\nTiến độ: 0%')
                except Exception:
                    pass
            result = await self.actions.observe(self.store.get(job['id'],job['owner']),self.store)
            if result:
                if result.get('files'):
                    if result.get('owner') != job['owner']: raise SourceError('Kết quả không khớp chủ tác vụ.')
                    with self.store.db:
                        self.store.db.execute('INSERT OR REPLACE INTO outputs VALUES(?,?,?)',(job['id'],job['owner'],json.dumps(result['files'])))
                    if not result.get('notified'):
                        for file in result['files']:
                            await send_file(self.tg,job['chat'],file,reply_to=job.get('request_message_id') or job['message_id'],thread_id=job.get('thread_id'))
                self.store.change(job['id'],state=result['state'],completed=time.time(),url='',phase='completed',error=result.get('error',''))
                if result['state'] != 'done': await self.tell(job['chat'],'❌ '+job['id']+' — '+result.get('error','Workflow chưa hoàn tất.'),reply_to=job.get('request_message_id') or job['message_id'],thread_id=job.get('thread_id'))
        except Exception as e:
            error = str(e) if isinstance(e,SourceError) else 'Không đọc được kết quả Actions; kiểm tra workflow trước khi gửi lại.'
            self.store.change(job['id'],state='failed',url='',error=error)
            await self.tell(job['chat'],'❌ '+job['id']+' — '+error,reply_to=job.get('request_message_id') or job['message_id'],thread_id=job.get('thread_id'))

    async def health(self, request):
        ok = all(not task.done() for task in self.tasks)
        return web.json_response({'status':'ok' if ok else 'degraded','bot':'payload','storage':'gdrive',
            'public':self.s.public,'groups':self.s.groups,'public_files':self.s.public_files,
            'max_concurrent':self.s.max_concurrent,'active_jobs':len(self.job_tasks)},status=200 if ok else 503)


def main():
    logging.basicConfig(level=logging.INFO)
    s = Settings.load(); bot = Bot(s)
    app = web.Application(); app.router.add_get('/health',bot.health)
    app.on_startup.append(bot.start); app.on_cleanup.append(bot.close)
    web.run_app(app,host='0.0.0.0',port=s.port,access_log=None,print=None)


if __name__ == '__main__': main()
