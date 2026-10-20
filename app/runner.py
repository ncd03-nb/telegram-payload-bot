import asyncio
from .failover import notify_result
import json
import os
import re
import signal
import time
import traceback
from contextlib import nullcontext
from pathlib import Path

from .gdrive import Drive
from .image_source import load_source
from .remote import Remote
from .security import SourceError
from .settings import Settings
from .telegram import Telegram
from .bundle import Bundle
from .exchange import load_job_env, write_result


def selection(text, parts):
    names = list(parts) if text.strip().lower() == 'all' else list(dict.fromkeys(re.split(r'[,\s]+', text.strip())))
    if not names or any(n not in parts for n in names):
        raise SourceError('Tên phân vùng không có trong ROM; dùng /list để xem.')
    if any(not parts[n]['supported'] for n in names):
        raise SourceError('Lựa chọn chứa phân vùng chưa hỗ trợ: delta cần ảnh ROM gốc hoặc ảnh ZIP mã hóa/không có dữ liệu.')
    return names


async def send_file(telegram, chat, file, reply_to=None, thread_id=None, message_id=None):
    is_zip = file['name'].endswith('.zip')
    detail = (f"\n📦 {len(file['members'])} phân vùng\n" + ', '.join(member['name'] for member in file['members'])) if file.get('members') else ''
    version = file.get('rom_version') or 'Không xác định'
    text = f"✅ Hoàn thành\n📱 ROM: {version}\n📄 {file['name']}\n💾 {file['size']/1024**2:.2f} MiB" + detail
    markup = {'inline_keyboard': [[{'text': '⬇ Tải ZIP từ Drive' if is_zip else '⬇ Tải .img từ Drive', 'url': file['url']}]]}
    if message_id:
        await telegram.edit(chat, message_id, text, reply_markup=markup)
    else:
        await telegram.send(chat, text, reply_markup=markup,
            **({'reply_to':reply_to} if reply_to else {}), **({'thread_id':thread_id} if thread_id else {}))


class Progress:
    def __init__(self, tg, chat, message, task_id):
        self.tg, self.chat, self.message, self.id = tg, chat, message, task_id
        self.phase, self.name, self.percent = 'Đọc payload', '', 0
        self.started = time.monotonic()
        self.detail = ''
        self.last_signature = None
        self.last_render = 0

    async def render(self, final=None):
        signature=(round(self.percent,1),self.phase,self.name,self.detail,final)
        if signature==self.last_signature and time.monotonic()-self.last_render<10:return
        text = final or f"⚙️ {self.id} — {self.phase}\n📄 {self.name}\nTiến độ: {self.percent:.1f}%\nThời gian: {int(time.monotonic()-self.started)} giây" + ('\n'+self.detail if self.detail else '')
        try:
            await self.tg.edit(self.chat, self.message, text)
            self.last_signature=signature;self.last_render=time.monotonic()
        except Exception:
            pass
        print(f'Progress {self.percent:.1f}% | {self.phase}', flush=True)

    async def loop(self):
        while True:
            await self.render()
            await asyncio.sleep(2)


def validate_identity(s, task_id, owner, chat, message, thread_id=0, reply_to=0):
    if (not re.fullmatch(r'[a-f0-9]{12}', task_id) or owner <= 0 or (not s.public and owner not in s.users)
            or not chat or (chat != owner and not (s.groups and chat < 0)) or message <= 0 or thread_id < 0 or reply_to < 0):
        raise ValueError('Invalid workflow identity')


async def run():
    load_job_env()
    s = Settings.load()
    task_id = os.environ['JOB_ID']
    owner, chat, message = (int(os.environ[k]) for k in ('JOB_OWNER', 'JOB_CHAT', 'JOB_MESSAGE_ID'))
    thread_id = int(os.getenv('JOB_THREAD_ID','0'))
    reply_to = int(os.getenv('JOB_REPLY_TO','0'))
    validate_identity(s, task_id, owner, chat, message, thread_id, reply_to)
    tg = Telegram(s.token); await tg.start()
    progress = Progress(tg, chat, message, task_id)
    ticker = asyncio.create_task(progress.loop())
    result = {'id': task_id, 'owner': owner, 'state': 'failed', 'files': [], 'notified': False}
    directory = s.data_dir / task_id
    try:
        async with asyncio.timeout(19800):
            async with Remote(os.environ['JOB_SOURCE_URL'],concurrency=s.extract_workers) as remote, Drive(s.google_token,chunk_mib=s.drive_chunk_mib) as drive:
                async def preparing(done, end):
                    progress.phase = 'Giải nén ảnh ZIP để đọc phân vùng'
                    progress.detail = f'Cache đã giải nén: {done/1024**2:.0f}/{end/1024**2:.0f} MiB cần đọc'
                remote.prepare_progress = preparing
                payload = await load_source(remote)
                names = selection(os.environ['JOB_PARTITIONS'], payload.parts)
                total = sum(payload.parts[n]['size'] for n in names)
                if total > s.max_output:
                    raise SourceError('Tổng dung lượng ảnh vượt MAX_OUTPUT_GIB; chọn ít phân vùng hơn.')
                completed = 0
                multiple = len(names) > 1
                result['extraction_metrics'] = []
                with Bundle(directory, task_id) if multiple else nullcontext() as bundle:
                    for name in names:
                        progress.name, progress.phase = name + '.img', ('Trích xuất và kiểm tra SHA-256' if getattr(payload,'trusted_hashes',True) else 'Trích xuất ảnh và tính SHA-256')
                        part_started = time.monotonic()
                        async def extracted(count, size):
                            progress.phase = ('Trích xuất và kiểm tra SHA-256' if getattr(payload,'trusted_hashes',True) else 'Trích xuất ảnh và tính SHA-256')
                            progress.percent = (70 * completed + 60 * count) / total if multiple else 60 * count / total
                            rate=count/1024**2/max(.001,time.monotonic()-part_started)
                            progress.detail=f'Trích: {count/1024**2:.0f}/{size/1024**2:.0f} MiB · {rate:.1f} MiB/s'
                        file = await payload.extract(name,directory,extracted,workers=s.extract_workers,
                            batch_bytes=s.range_batch_mib*1024**2,decode_workers=s.decode_workers)
                        result['extraction_metrics'].append({'partition':name,**file['metrics']})
                        print('Extraction metrics: '+json.dumps(result['extraction_metrics'][-1]),flush=True)
                        if multiple:
                            progress.phase = 'Nén phân vùng vào ZIP'
                            progress.detail = ''
                            await bundle.add(file)
                            completed += file['size']
                            progress.percent = 70 * completed / total
                    if multiple:
                        progress.phase = 'Hoàn tất ZIP và kiểm tra SHA-256'
                        file = await bundle.finish()
                progress.name, progress.phase = file['name'], 'Upload Google Drive'
                upload_started = time.monotonic(); progress.detail = ''
                async def uploaded(count, size):
                    progress.percent = min(99, (70 if multiple else 60) + (30 if multiple else 40) * count / size)
                    rate=count/1024**2/max(.001,time.monotonic()-upload_started)
                    progress.detail=f'Upload: {count/1024**2:.0f}/{size/1024**2:.0f} MiB · {rate:.1f} MiB/s'
                record = await drive.upload(file, task_id, owner, uploaded, public=s.public_files)
                result['upload_metrics'] = {'seconds':round(time.monotonic()-upload_started,3),
                    'chunk_mib':s.drive_chunk_mib,'bytes':file['size']}
                record['rom_version'] = remote.rom_version
                if file.get('members'):
                    record['members'] = file['members']
                result['files'].append(record)
                result['partition_count'] = len(names)
                Path(file['path']).unlink()
                result['downloaded_bytes'] = remote.downloaded
                result['source_bytes'] = remote.size
                result['state'] = 'done'
                progress.percent = 100
    except asyncio.CancelledError:
        result.update(state='cancelled', error='Đã hủy; các file hoàn tất trước đó vẫn nằm trên Drive.')
        raise
    except Exception as e:
        # Frame locations identify programming errors without logging source URLs/tokens.
        print('Runner failure: '+type(e).__name__+' at '+','.join(
            Path(frame.filename).name+':'+str(frame.lineno) for frame in traceback.extract_tb(e.__traceback__)),flush=True)
        result['error'] = str(e) if isinstance(e, SourceError) else 'Trích xuất/upload thất bại; kiểm tra nguồn, OAuth hoặc quota.'
    finally:
        ticker.cancel(); await asyncio.gather(ticker, return_exceptions=True)
        if result['state'] == 'done':
            await progress.render(f'✅ {task_id} — 100%\nĐã trích xuất {result["partition_count"]} phân vùng và upload 1 file lên Drive.')
        else:
            await progress.render('❌ ' + task_id + ' — ' + result.get('error', 'Runner bị ngắt.'))
        try:
            if not s.hub_url:
                for file in result['files']:
                    await send_file(tg, chat, file, message_id=message)
                result['notified'] = True
        except Exception:
            pass
        output = Path(os.getenv('RESULT_PATH', str(s.data_dir / 'result.json')))
        output.parent.mkdir(parents=True, exist_ok=True)
        write_result(output,result,s.token)
        # Directory is fixed by validated task ID, never by source filename.
        if directory.exists():
            for file in directory.iterdir():
                if file.is_file() and file.suffix in {'.img', '.zip', '.partial'}:
                    file.unlink()
        await tg.close()
        await notify_result(s, task_id)
    return 0 if result['state'] == 'done' else 1


async def main():
    task = asyncio.create_task(run())
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, task.cancel)
    return await task


if __name__ == '__main__':
    try:
        raise SystemExit(asyncio.run(main()))
    except asyncio.CancelledError:
        raise SystemExit(130)
