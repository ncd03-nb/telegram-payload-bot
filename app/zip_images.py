"""Bounded ZIP64 image reads, including lazy disk-backed DEFLATE prefixes."""
import asyncio
import re
import shutil
import struct
import zlib
from pathlib import PurePosixPath

from .security import SourceError
from .threads import thread_call

LIMIT = 100 * 1024**3


class ZipImages:
    def __init__(self, remote):
        self.remote = remote
        self.entries = []

    async def load(self):
        r = self.remote
        start = max(0, r.size - 65557)
        tail = await r.read(start, r.size - start)
        pos = tail.rfind(b'PK\x05\x06')
        if pos < 0 or pos + 22 > len(tail):
            raise SourceError('ZIP thiếu central directory.')
        end = struct.unpack_from('<4s4H2IH', tail, pos)
        if end[1] or end[2] or end[3] != end[4] or pos + 22 + end[7] != len(tail):
            raise SourceError('ZIP nhiều volume hoặc EOCD không hợp lệ.')
        count, size, offset = end[4:7]
        if count == 65535 or size == 0xffffffff or offset == 0xffffffff:
            locator = struct.unpack('<4sIQI', await r.read(start + pos - 20, 20))
            if locator[0] != b'PK\x06\x07' or locator[1] or locator[3] != 1:
                raise SourceError('ZIP64 locator không hợp lệ.')
            z = struct.unpack('<4sQ2H2I4Q', await r.read(locator[2], 56))
            if z[0] != b'PK\x06\x06' or z[4] or z[5] or z[6] != z[7]:
                raise SourceError('ZIP64 nhiều volume không được hỗ trợ.')
            count, size, offset = z[7], z[8], z[9]
        if not 0 < count <= 50000 or not 46 <= size <= 8 * 1024**2 or offset + size > start + pos:
            raise SourceError('ZIP directory vượt giới hạn.')
        self.cd_offset = offset
        directory = await r.read(offset, size)
        cursor, names = 0, set()
        for _ in range(count):
            if cursor + 46 > len(directory):
                raise SourceError('ZIP directory bị cắt ngắn.')
            a = struct.unpack_from('<4s6H3I5H2I', directory, cursor)
            if a[0] != b'PK\x01\x02':
                raise SourceError('ZIP entry không hợp lệ.')
            n, x, c = a[10:13]
            end_entry = cursor + 46 + n + x + c
            if end_entry > len(directory) or a[13]:
                raise SourceError('ZIP entry bị cắt ngắn/nhiều volume.')
            raw_name = directory[cursor+46:cursor+46+n]
            try:
                name = raw_name.decode('utf-8' if a[3] & 0x800 else 'cp437')
            except UnicodeDecodeError:
                raise SourceError('Tên ZIP không hợp lệ.') from None
            if name in names or '\\' in name or name.startswith('/') or any(p in {'.', '..'} for p in name.split('/')) or '\x00' in name:
                raise SourceError('ZIP chứa tên trùng lặp hoặc đường dẫn không an toàn.')
            names.add(name)
            values = [a[9], a[8], a[16]]
            extra = directory[cursor+46+n:cursor+46+n+x]
            p = 0
            while p + 4 <= len(extra):
                tag, length = struct.unpack_from('<HH', extra, p)
                data = extra[p+4:p+4+length]; p += 4 + length
                if len(data) != length:
                    raise SourceError('ZIP extra field bị cắt ngắn.')
                if tag == 1:
                    q = 0
                    for i, value in enumerate(values):
                        if value == 0xffffffff:
                            if q + 8 > len(data):
                                raise SourceError('ZIP64 thiếu kích thước/offset.')
                            values[i] = struct.unpack_from('<Q', data, q)[0]; q += 8
            if any(v == 0xffffffff for v in values):
                raise SourceError('ZIP64 thiếu extra field.')
            usize, csize, at = values
            if usize > LIMIT or csize > LIMIT or at >= offset:
                raise SourceError('ZIP entry vượt giới hạn.')
            self.entries.append({'name': name, 'raw_name': raw_name, 'size': usize, 'compressed': csize,
                                 'offset': at, 'method': a[4], 'flags': a[3], 'crc': a[7]})
            cursor = end_entry
        if cursor != len(directory):
            raise SourceError('ZIP directory có dữ liệu dư.')
        return self

    async def open(self, entry):
        if entry['flags'] & (1 | 0x40) or entry['method'] not in {0, 8} or not entry['size']:
            raise SourceError('Chỉ hỗ trợ ảnh ZIP_STORED/DEFLATE không mã hóa.')
        raw = await self.remote.read(entry['offset'], 30)
        a = struct.unpack('<4s5H3I2H', raw)
        if a[0] != b'PK\x03\x04' or a[2] != entry['flags'] or a[3] != entry['method']:
            raise SourceError('ZIP local header không khớp directory.')
        name = await self.remote.read(entry['offset']+30, a[9]) if a[9] else b''
        if name != entry['raw_name']:
            raise SourceError('ZIP local filename không khớp.')
        base = entry['offset'] + 30 + a[9] + a[10]
        if base + entry['compressed'] > self.cd_offset:
            raise SourceError('ZIP entry vượt central directory.')
        if entry['method'] == 0 and entry['size'] != entry['compressed']:
            raise SourceError('ZIP_STORED có kích thước không khớp.')
        return ZipReader(self.remote, entry, base)


class ZipReader:
    def __init__(self, remote, entry, base):
        self.remote, self.entry, self.base = remote, entry, base
        self.size = entry['size']
        self.lock = asyncio.Lock()
        self.cache = None
        self.have = self.compressed = self.crc = 0
        self.pending = b''
        self.decoder = zlib.decompressobj(-15)
        self.verified = False

    def close(self):
        if self.cache:
            self.cache.unlink(missing_ok=True)

    async def read(self, offset, length):
        if offset < 0 or length <= 0 or length > 32*1024**2 or offset + length > self.size:
            raise SourceError('Đọc ảnh ngoài giới hạn ZIP.')
        if self.entry['method'] == 0:
            return await self.remote.read(self.base + offset, length)
        async with self.lock:
            if self.cache is None:
                self.cache = self.remote.cache_directory() / ('entry-' + str(self.base))
                self.cache.touch()
            await self.inflate_until(offset + length)
            def read_cache():
                with self.cache.open('rb') as f:
                    f.seek(offset)
                    return f.read(length)
            data = await thread_call(read_cache)
            if len(data) != length:
                raise SourceError('Ảnh cache bị cắt ngắn.')
            return data

    async def inflate_until(self, end):
        while self.have < end or end == self.size and not self.verified:
            if shutil.disk_usage(self.cache.parent).free < min(self.size-self.have, 2*1024**2) + 256*1024**2:
                raise SourceError('Runner không đủ chỗ giải nén ảnh; chọn phân vùng nhỏ hơn.')
            if not self.pending and self.compressed < self.entry['compressed']:
                count = min(max(64*1024, min(32*1024**2, end-self.have)), self.entry['compressed'] - self.compressed)
                if count > 8*1024**2:
                    tasks = []
                    for at in range(0,count,8*1024**2):
                        tasks.append(asyncio.create_task(self.remote.read(self.base+self.compressed+at,min(8*1024**2,count-at))))
                    try:
                        await asyncio.gather(*tasks)
                    finally:
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks,return_exceptions=True)
                    self.pending = b''.join(task.result() for task in tasks)
                else:
                    self.pending = await self.remote.read(self.base + self.compressed, count)
                self.compressed += count
            try:
                data = await thread_call(self.decoder.decompress, self.pending, min(1024**2, self.size-self.have+1))
            except zlib.error:
                raise SourceError('Dữ liệu DEFLATE của ảnh bị lỗi.') from None
            self.pending = self.decoder.unconsumed_tail
            if self.have + len(data) > self.size:
                raise SourceError('Ảnh giải nén vượt kích thước ZIP.')
            if data:
                def append():
                    with self.cache.open('ab') as out:
                        out.write(data)
                await thread_call(append)
                self.have += len(data); self.crc = zlib.crc32(data, self.crc)
                if getattr(self.remote,'prepare_progress',None):
                    await self.remote.prepare_progress(self.have,end)
            if self.decoder.eof:
                if (self.have != self.size or self.compressed != self.entry['compressed']
                        or self.decoder.unused_data or self.pending or self.crc != self.entry['crc']):
                    raise SourceError('CRC32/kích thước ZIP không khớp; không upload ảnh lỗi.')
                self.verified = True
            elif not data and not self.pending and self.compressed == self.entry['compressed']:
                raise SourceError('Ảnh DEFLATE bị cắt ngắn.')
            if self.verified and self.have < end:
                raise SourceError('ZIP thiếu dữ liệu ảnh.')


def partition_name(path):
    name = PurePosixPath(path).name[:-4]
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_-]{0,79}', name):
        raise SourceError('Tên ảnh phân vùng không hợp lệ.')
    return name
