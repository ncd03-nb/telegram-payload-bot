"""Bounded HTTP ranges and ZIP64 directory parsing; no full-ROM download."""
import asyncio
import json
import re
import struct
import zlib
import os
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import aiohttp

from .security import PublicResolver, SourceError, validate_url
from .rom import version_from_url, version_from_metadata
from .drive_source import drive_file, resolve_drive


class Remote:
    def __init__(self, url, concurrency=8, cache_dir=None):
        if not 1 <= concurrency <= 16:
            raise ValueError('Invalid range concurrency')
        self.url = validate_url(url)
        self.size = None
        self.etag = None
        self.downloaded = 0
        self.session = None
        self.rom_version = version_from_url(url)
        self.concurrency = concurrency
        self.requests = 0
        self.cache = None
        self.cache_base = Path(cache_dir if cache_dir is not None else os.getenv('DATA_DIR', 'data')).resolve()

    def cache_directory(self):
        if self.cache is None:
            base = self.cache_base
            base.mkdir(parents=True, exist_ok=True)
            self.cache = tempfile.TemporaryDirectory(prefix='rom-cache-', dir=base)
            if Path(self.cache.name).resolve().parent != base:
                raise SourceError('Thư mục cache không hợp lệ.')
        return Path(self.cache.name)

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(resolver=PublicResolver(), limit=self.concurrency),
            timeout=aiohttp.ClientTimeout(total=120, sock_connect=25, sock_read=60),
            auto_decompress=False, trust_env=False)
        try:
            if drive_file(self.url):
                self.url, name = await resolve_drive(self.session, self.url)
                self.rom_version = self.rom_version or version_from_url('https://drive.google.com/' + name)
            parsed = urlsplit(self.url)
            if parsed.path.rstrip('/').endswith('/downloadCheck'):
                if not (parsed.hostname or '').endswith(('.allawnos.com', '.allawntech.com', '.coloros.com', '.oplusota.com')):
                    raise SourceError('Host downloadCheck chưa được hỗ trợ.')
                async with self.session.get(self.url, allow_redirects=False, headers={
                    'User-Agent': 'okhttp/3.12.12', 'Accept': '*/*', 'Accept-Encoding': 'identity',
                    'Cache-Control': 'no-cache', 'userId': 'oplus-ota|16002018'}) as r:
                    if r.status not in {301, 302, 303, 307, 308} or not r.headers.get('Location'):
                        data = await r.content.read(4096)
                        try:
                            code = int(json.loads(data).get('responseCode', r.status))
                        except (ValueError, TypeError, AttributeError):
                            code = r.status
                        raise SourceError(f'OPlus downloadCheck trả mã {code}; chưa lấy được link CDN.')
                    self.url = validate_url(urljoin(self.url, r.headers['Location']))
            await self.read(0, 32)
            return self
        except BaseException:
            await self.session.close()
            raise

    async def __aexit__(self, *args):
        await self.session.close()
        if self.cache:
            self.cache.cleanup()

    async def read(self, offset, length):
        if offset < 0 or length <= 0 or length > 32 * 1024**2 or (self.size is not None and offset + length > self.size):
            raise SourceError('Vùng dữ liệu nằm ngoài file hoặc quá lớn.')
        for attempt in range(3):
            try:
                current = self.url
                for _ in range(8):
                    validate_url(current)
                    headers = {'Range': f'bytes={offset}-{offset+length-1}', 'Accept-Encoding': 'identity',
                               'User-Agent': 'PayloadPartitionBot/1.0'}
                    if self.etag and not self.etag.startswith('W/'):
                        headers['If-Range'] = self.etag
                    self.requests += 1
                    async with self.session.get(current, headers=headers, allow_redirects=False) as r:
                        if r.status in {301, 302, 303, 307, 308}:
                            current = urljoin(current, r.headers.get('Location', ''))
                            continue
                        if r.status in {429, 500, 502, 503, 504}:
                            raise aiohttp.ClientConnectionError('Retry range')
                        match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', r.headers.get('Content-Range', ''))
                        if r.status != 206:
                            raise SourceError(f'Nguồn trả HTTP {r.status}, cần hỗ trợ HTTP Range (206).')
                        if not match:
                            raise SourceError('Nguồn trả HTTP 206 nhưng Content-Range thiếu hoặc không hợp lệ.')
                        # Some OPlus AliyunOSS objects label raw ZIP bytes as UTF-8.
                        # This is a charset, not a content coding. Keep decompression
                        # disabled and enforce the exact range length and image hashes.
                        encoding = r.headers.get('Content-Encoding', 'identity').strip().lower()
                        if encoding not in {'identity', 'utf-8', 'utf8'}:
                            raise SourceError('Nguồn trả dữ liệu mã hóa/nén HTTP; cần Range trên byte gốc.')
                        start, end, total = map(int, match.groups())
                        if start != offset or end != offset + length - 1 or total < offset + length or total > 100 * 1024**3:
                            raise SourceError('Content-Range không khớp vùng yêu cầu.')
                        if self.size is not None and self.size != total:
                            raise SourceError('File nguồn đã thay đổi dung lượng.')
                        if self.etag and r.headers.get('ETag') != self.etag:
                            raise SourceError('File nguồn đã thay đổi ETag.')
                        raw = await r.content.readexactly(length)
                        if await r.content.read(1):
                            raise SourceError('Máy chủ trả thừa byte Range.')
                        self.size, self.url = total, current
                        self.rom_version = self.rom_version or version_from_url(current)
                        self.etag = self.etag or r.headers.get('ETag')
                        self.downloaded += len(raw)
                        return raw
                raise SourceError('Nguồn chuyển hướng quá nhiều lần.')
            except (aiohttp.ClientError, asyncio.TimeoutError, asyncio.IncompleteReadError):
                if attempt == 2:
                    raise SourceError('Đọc nguồn thất bại sau 3 lần thử; kiểm tra kết nối/link hết hạn.') from None
                await asyncio.sleep(2 ** attempt)

    async def payload_region(self):
        head = await self.read(0, 24)
        if head[:4] == b'CrAU':
            return 0, self.size
        if head[:4] != b'PK\x03\x04':
            raise SourceError('Nguồn không phải payload.bin hoặc ZIP OTA.')
        tail_start = max(0, self.size - 65557)
        tail = await self.read(tail_start, self.size - tail_start)
        pos = tail.rfind(b'PK\x05\x06')
        if pos < 0 or pos + 22 > len(tail):
            raise SourceError('ZIP thiếu central directory.')
        end = struct.unpack_from('<4s4H2IH', tail, pos)
        if end[1] or end[2] or pos + 22 + end[7] != len(tail):
            raise SourceError('ZIP nhiều volume hoặc EOCD không hợp lệ.')
        count, cd_size, cd_offset = end[4], end[5], end[6]
        if count == 65535 or cd_size == 0xffffffff or cd_offset == 0xffffffff:
            locator = await self.read(tail_start + pos - 20, 20)
            magic, disk, zoff, disks = struct.unpack('<4sIQI', locator)
            if magic != b'PK\x06\x07' or disk or disks != 1:
                raise SourceError('ZIP64 locator không hợp lệ.')
            z = struct.unpack('<4sQ2H2I4Q', await self.read(zoff, 56))
            if z[0] != b'PK\x06\x06' or z[4] or z[5]:
                raise SourceError('ZIP64 nhiều volume không được hỗ trợ.')
            count, cd_size, cd_offset = z[7], z[8], z[9]
        if count > 50000 or cd_size > 8 * 1024**2 or cd_size < 46:
            raise SourceError('ZIP directory vượt giới hạn.')
        directory = await self.read(cd_offset, cd_size)
        cursor, found, metadata = 0, [], []
        for _ in range(count):
            if cursor + 46 > len(directory):
                raise SourceError('ZIP directory bị cắt ngắn.')
            entry = struct.unpack_from('<4s6H3I5H2I', directory, cursor)
            if entry[0] != b'PK\x01\x02':
                raise SourceError('ZIP entry không hợp lệ.')
            name_len, extra_len, comment_len = entry[10:13]
            next_cursor = cursor + 46 + name_len + extra_len + comment_len
            if next_cursor > len(directory):
                raise SourceError('ZIP entry bị cắt ngắn.')
            name = directory[cursor+46:cursor+46+name_len]
            extra = directory[cursor+46+name_len:cursor+46+name_len+extra_len]
            cursor = next_cursor
            if name == b'META-INF/com/android/metadata':
                metadata.append(entry)
            if name != b'payload.bin':
                continue
            if entry[3] & 1 or entry[4] != 0:
                raise SourceError('payload.bin phải là ZIP_STORED, không mã hóa/nén ở lớp ZIP.')
            usize, csize, offset = entry[9], entry[8], entry[16]
            ep = 0
            while ep + 4 <= len(extra):
                tag, size = struct.unpack_from('<HH', extra, ep)
                data = extra[ep+4:ep+4+size]
                if len(data) != size:
                    raise SourceError('ZIP extra field bị cắt ngắn.')
                ep += 4 + size
                if tag == 1:
                    vals = [usize, csize, offset]
                    dp = 0
                    for i in range(3):
                        if vals[i] == 0xffffffff:
                            if dp + 8 > len(data):
                                raise SourceError('ZIP64 thiếu kích thước.')
                            vals[i] = struct.unpack_from('<Q', data, dp)[0]
                            dp += 8
                    usize, csize, offset = vals
            if usize != csize or usize <= 24:
                raise SourceError('Kích thước payload trong ZIP không hợp lệ.')
            local = await self.read(offset, 30)
            fields = struct.unpack('<4s5H3I2H', local)
            if fields[0] != b'PK\x03\x04' or fields[2] & 1 or fields[3] != 0:
                raise SourceError('ZIP local header không khớp.')
            start = offset + 30 + fields[9] + fields[10]
            if start + usize > cd_offset:
                raise SourceError('Payload vượt giới hạn ZIP.')
            found.append((start, usize))
        if len(found) != 1:
            raise SourceError('ZIP cần chứa đúng một file payload.bin ở thư mục gốc.')
        if len(metadata) == 1:
            try:
                version = await self.metadata_version(metadata[0], cd_offset)
                if version and (not version.startswith('Build ') or not self.rom_version):
                    self.rom_version = version
            except (SourceError, zlib.error):
                # Optional metadata must not prevent extraction of a valid payload.
                pass
        return found[0]

    async def metadata_version(self, entry, cd_offset):
        flags, method, crc, csize, usize, offset = entry[3], entry[4], entry[7], entry[8], entry[9], entry[16]
        if flags & 1 or method not in {0, 8} or not 0 < usize <= 65536 or not 0 < csize <= 131072 or offset == 0xffffffff:
            return ''
        local = struct.unpack('<4s5H3I2H', await self.read(offset, 30))
        if local[0] != b'PK\x03\x04' or local[2] & 1 or local[3] != method:
            return ''
        start = offset + 30 + local[9] + local[10]
        if start + csize > cd_offset:
            return ''
        raw = await self.read(start, csize)
        if method == 8:
            decoder = zlib.decompressobj(-15)
            raw = decoder.decompress(raw, usize + 1)
            if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                return ''
        if len(raw) != usize or zlib.crc32(raw) != crc:
            return ''
        return version_from_metadata(raw)
