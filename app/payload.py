"""Full Android A/B payload extraction with per-operation and final SHA-256 checks.

Wire field numbers follow AOSP update_engine/update_metadata.proto.
"""
import asyncio
import bz2
import hashlib
import lzma
import re
import shutil
import struct
from pathlib import Path

from .security import SourceError


def varint(data, pos):
    value = 0
    for shift in range(0, 70, 7):
        if pos >= len(data):
            raise SourceError('Protobuf bị cắt ngắn.')
        b = data[pos]; pos += 1
        value |= (b & 127) << shift
        if not b & 128:
            return value, pos
    raise SourceError('Protobuf varint quá dài.')


def fields(data):
    pos = 0
    while pos < len(data):
        key, pos = varint(data, pos)
        num, wire = key >> 3, key & 7
        if not num:
            raise SourceError('Protobuf field không hợp lệ.')
        if wire == 0:
            value, pos = varint(data, pos)
        elif wire in {1, 2, 5}:
            if wire == 2:
                size, pos = varint(data, pos)
            else:
                size = 8 if wire == 1 else 4
            if pos + size > len(data):
                raise SourceError('Protobuf field bị cắt ngắn.')
            value = data[pos:pos+size]; pos += size
        else:
            raise SourceError('Protobuf wire type chưa hỗ trợ.')
        yield num, value


class Payload:
    def __init__(self, remote):
        self.remote = remote
        self.parts = {}

    async def load(self):
        start, self.length = await self.remote.payload_region()
        header = await self.remote.read(start, 24)
        magic, version, size, sig = struct.unpack('>4sQQI', header)
        if magic != b'CrAU' or version != 2 or size > 16 * 1024**2 or sig > 1024**2 or 24 + size + sig > self.length:
            raise SourceError('Chỉ hỗ trợ payload v2 với manifest hợp lệ.')
        manifest = await self.remote.read(start + 24, size)
        self.base = start + 24 + size + sig
        self.end = start + self.length
        self.block = 4096
        raw_parts = []
        for num, value in fields(manifest):
            if num == 3:
                self.block = value
            elif num == 13:
                raw_parts.append(value)
        if self.block not in {4096, 512} or not raw_parts or len(raw_parts) > 512:
            raise SourceError('Manifest có block size/số phân vùng không hợp lệ.')
        for raw in raw_parts:
            part = {'ops': [], 'name': '', 'size': 0, 'hash': b''}
            for num, value in fields(raw):
                if num == 1:
                    part['name'] = value.decode('ascii')
                elif num == 7:
                    info = dict(fields(value))
                    part['size'], part['hash'] = info.get(1, 0), info.get(2, b'')
                elif num == 8:
                    op = {'type': -1, 'offset': 0, 'length': 0, 'extents': [], 'hash': b''}
                    for f, v in fields(value):
                        if f in {1, 2, 3, 8}:
                            op[{1: 'type', 2: 'offset', 3: 'length', 8: 'hash'}[f]] = v
                        elif f == 6:
                            ext = dict(fields(v))
                            op['extents'].append((ext.get(1, -1), ext.get(2, -1)))
                    part['ops'].append(op)
            if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_-]{0,79}', part['name']) or part['name'] in self.parts:
                raise SourceError('Tên phân vùng không hợp lệ/trùng lặp.')
            if not 0 < part['size'] <= 32 * 1024**3 or len(part['hash']) != 32 or len(part['ops']) > 100000:
                raise SourceError('Thiếu kích thước/hash SHA-256 của phân vùng hoặc vượt giới hạn.')
            part['supported'] = bool(part['ops']) and all(op['type'] in {0, 1, 6, 7, 8} for op in part['ops'])
            self.parts[part['name']] = part
        return self

    def summary(self):
        return [{'name': p['name'], 'size': p['size'], 'supported': p['supported'], 'sha256': p['hash'].hex()}
                for p in self.parts.values()]

    async def extract(self, name, directory, progress=None):
        part = self.parts.get(name)
        if not part or not part['supported']:
            raise SourceError(f'{name}: không tồn tại hoặc dùng delta operation cần ROM gốc.')
        directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(directory).free < part['size'] + 256 * 1024**2:
            raise SourceError('Runner không đủ dung lượng trống cho phân vùng này.')
        path = directory / (name + '.img')
        partial = directory / (name + '.img.partial')
        done = 0
        try:
            with partial.open('w+b') as out:
                out.truncate(part['size'])
                for op in part['ops']:
                    extents = [(start * self.block, count * self.block) for start, count in op['extents']]
                    if not extents or any(a < 0 or n <= 0 or a+n > part['size'] for a,n in extents):
                        raise SourceError('Extent vượt kích thước phân vùng.')
                    expected = sum(n for _, n in extents)
                    if expected > 64 * 1024**2 or op['length'] > 32 * 1024**2 or op['offset'] < 0 or self.base+op['offset']+op['length'] > self.end:
                        raise SourceError('Operation vượt giới hạn an toàn.')
                    if op['type'] in {6, 7}:
                        data = bytes(expected)
                    else:
                        raw = await self.remote.read(self.base + op['offset'], op['length'])
                        if op['hash'] and hashlib.sha256(raw).digest() != op['hash']:
                            raise SourceError('SHA-256 dữ liệu operation không khớp.')
                        data = await asyncio.to_thread(decompress, raw, op['type'], expected)
                    consumed = 0
                    for offset, size in extents:
                        out.seek(offset)
                        out.write(data[consumed:consumed+size]); consumed += size
                    done += expected
                    if progress:
                        await progress(min(done, part['size']), part['size'])
            digest = await asyncio.to_thread(hash_file, partial)
            if digest != part['hash'].hex():
                raise SourceError(f'{name}: SHA-256 ảnh cuối không khớp manifest; không upload ảnh lỗi.')
            partial.replace(path)
            return {'path': str(path), 'name': name + '.img', 'size': part['size'], 'sha256': digest}
        finally:
            partial.unlink(missing_ok=True)


def decompress(raw, kind, expected):
    if kind == 0:
        data = raw
    else:
        decoder = bz2.BZ2Decompressor() if kind == 1 else lzma.LZMADecompressor(memlimit=128 * 1024**2)
        try:
            data = decoder.decompress(raw, max_length=expected+1)
        except (OSError, EOFError, lzma.LZMAError):
            raise SourceError('Dữ liệu nén operation bị lỗi.') from None
        if not decoder.eof or decoder.unused_data:
            raise SourceError('Operation nén vượt giới hạn hoặc không kết thúc đúng.')
    if len(data) > expected:
        raise SourceError('Operation giải nén vượt extents.')
    return data + bytes(expected - len(data))


def hash_file(path):
    with open(path, 'rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()
