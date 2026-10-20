"""Android sparse maps and liblp logical partitions, following AOSP formats."""
import asyncio
import bisect
import hashlib
import re
import struct

from .security import SourceError


class SparseImage:
    def __init__(self, reader):
        self.reader = reader
        self.lock = asyncio.Lock()
        self.chunks, self.starts = [], []
        self.logical = self.indexed = 0

    @classmethod
    async def open(cls, reader):
        raw = await reader.read(0, 28)
        if raw[:4] != b'\x3a\xff\x26\xed':
            return reader
        self = cls(reader)
        magic, major, minor, header, chunk_header, block, blocks, chunks, checksum = struct.unpack('<I4H4I', raw)
        if (major != 1 or not 28 <= header <= 4096 or not 12 <= chunk_header <= 4096
                or not 4 <= block <= 1024**2 or block % 4 or not 0 < blocks*block <= 100*1024**3
                or not 0 < chunks <= 100000 or header > reader.size):
            raise SourceError('Android sparse header vượt giới hạn/không hợp lệ.')
        self.size, self.pos, self.block, self.count, self.header = blocks*block, header, block, chunks, chunk_header
        return self

    async def index_until(self, end):
        async with self.lock:
            while self.logical < end:
                if self.indexed >= self.count or self.pos + self.header > self.reader.size:
                    raise SourceError('Android sparse thiếu chunk/dữ liệu.')
                kind, reserved, blocks, total = struct.unpack('<2H2I', await self.reader.read(self.pos, 12))
                length, data = blocks*self.block, self.pos+self.header
                expected = {0xcac1: length, 0xcac2: 4, 0xcac3: 0, 0xcac4: 4}.get(kind)
                if (expected is None or reserved or total != self.header+expected
                        or self.pos+total > self.reader.size or self.logical+length > self.size
                        or (kind == 0xcac4 and blocks != 0) or (kind != 0xcac4 and not blocks)):
                    raise SourceError('Android sparse chunk không hợp lệ/vượt ảnh.')
                if kind == 0xcac4:
                    await self.reader.read(data, 4)
                else:
                    fill = await self.reader.read(data, 4) if kind == 0xcac2 else b''
                    self.starts.append(self.logical)
                    self.chunks.append((self.logical, self.logical+length, kind, data, fill))
                    self.logical += length
                self.pos += total; self.indexed += 1
                if self.indexed == self.count and (self.logical != self.size or self.pos != self.reader.size):
                    raise SourceError('Android sparse có kích thước/số chunk không khớp.')

    async def read(self, offset, length):
        if offset < 0 or not 0 < length <= 32*1024**2 or offset+length > self.size:
            raise SourceError('Đọc ngoài giới hạn ảnh sparse.')
        await self.index_until(offset+length)
        output = bytearray()
        index = max(0, bisect.bisect_right(self.starts, offset)-1)
        while length:
            start, end, kind, data, fill = self.chunks[index]
            size = min(length, end-offset)
            if size <= 0:
                raise SourceError('Sparse map thiếu vùng dữ liệu.')
            if kind == 0xcac1:
                output.extend(await self.reader.read(data+offset-start, size))
            elif kind == 0xcac2:
                shift = (offset-start) % 4
                output.extend((fill*((size+shift+3)//4))[shift:shift+size])
            else:
                output.extend(bytes(size))
            offset += size; length -= size; index += 1
        return bytes(output)


def checked_hash(raw, start, expected):
    data = bytearray(raw)
    data[start:start+32] = bytes(32)
    return hashlib.sha256(data).digest() == expected


class SuperImage:
    def __init__(self, reader):
        self.reader = reader
        self.parts = {}

    async def load(self):
        self.reader = await SparseImage.open(self.reader)
        geometry = None
        for offset in (4096, 8192):
            try:
                raw = await self.reader.read(offset, 52)
                magic, size = struct.unpack_from('<II', raw)
                if magic != 0x616c4467 or not 52 <= size <= 4096:
                    continue
                raw = await self.reader.read(offset, size)
                maximum, slots, block = struct.unpack_from('<III', raw, 40)
                if (checked_hash(raw, 8, raw[8:40]) and 512 <= maximum <= 4*1024**2
                        and maximum % 512 == 0 and 1 <= slots <= 4 and 512 <= block <= 1024**2 and block % 512 == 0):
                    geometry = (maximum, slots); break
            except SourceError:
                continue
        if geometry is None:
            raise SourceError('Không tìm thấy geometry super hợp lệ (raw hoặc Android sparse).')
        maximum, slots = geometry
        failure = None
        for offset in (12288, 12288+maximum*slots):
            try:
                await self.metadata(offset, maximum, 12288+maximum*slots*2)
                return self
            except SourceError as error:
                failure = error
        raise SourceError('Cả metadata chính và dự phòng super đều không hợp lệ: '+str(failure))

    async def metadata(self, offset, maximum, reserved):
        head = await self.reader.read(offset, 128)
        magic, major, minor, size = struct.unpack_from('<IHHI', head)
        if magic != 0x414c5030 or major != 10 or minor > 2 or size not in {128, 256} or minor >= 2 and size != 256:
            raise SourceError('Phiên bản/header liblp chưa hỗ trợ.')
        tables_size = struct.unpack_from('<I', head, 44)[0]
        if tables_size > maximum-size:
            raise SourceError('Bảng liblp vượt metadata_max_size.')
        header = await self.reader.read(offset, size)
        if not checked_hash(header, 12, header[12:44]):
            raise SourceError('SHA-256 header liblp không khớp.')
        tables = await self.reader.read(offset+size, tables_size) if tables_size else b''
        if hashlib.sha256(tables).digest() != header[48:80]:
            raise SourceError('SHA-256 bảng liblp không khớp.')
        ranges = []
        def table(at, expected, limit):
            start, count, entry_size = struct.unpack_from('<III', header, at)
            end = start+count*entry_size
            if entry_size != expected or count > limit or end > len(tables):
                raise SourceError('Bảng liblp bị cắt ngắn/vượt giới hạn.')
            if count:
                ranges.append((start, end))
            return [tables[p:p+entry_size] for p in range(start, end, entry_size)]
        parts, extents, groups, devices = table(80,52,512), table(92,24,100000), table(104,48,512), table(116,64,16)
        ranges.sort()
        if any(a[1] > b[0] for a,b in zip(ranges, ranges[1:])) or len(devices) != 1 or not groups:
            raise SourceError('Super cần bảng không chồng lấn và một block device đầy đủ.')
        first = struct.unpack_from('<Q', devices[0])[0]*512
        device_size = struct.unpack_from('<Q', devices[0],16)[0]
        if first < reserved or not first <= device_size <= self.reader.size:
            raise SourceError('Block device super vượt ảnh hoặc chồng metadata.')
        parsed, allocations = {}, []
        for row in parts:
            try:
                name = row[:36].split(b'\0',1)[0].decode('ascii')
            except UnicodeDecodeError:
                raise SourceError('Tên phân vùng liblp không hợp lệ.') from None
            attrs, start, count, group = struct.unpack_from('<4I',row,36)
            if not re.fullmatch(r'[A-Za-z0-9_]{1,36}',name) or name in parsed or attrs & ~15 or group >= len(groups) or start+count > len(extents):
                raise SourceError('Phân vùng liblp không hợp lệ/trùng tên.')
            if attrs & 2:
                name += '_a'
            if name in parsed:
                raise SourceError('Phân vùng liblp trùng tên slot.')
            spans, total = [], 0
            for raw in extents[start:start+count]:
                sectors, kind, data, source = struct.unpack('<QIQI',raw)
                length = sectors*512
                if not length or kind not in {0,1} or source or kind == 1 and data:
                    raise SourceError('Extent liblp cần block device khác hoặc không hợp lệ.')
                if kind == 0:
                    at = data*512
                    if at < first or at+length > device_size:
                        raise SourceError('Extent liblp vượt block device.')
                    allocations.append((at,at+length))
                    spans.append((at,length))
                else:
                    spans.append((None,length))
                total += length
            if total > 32*1024**3:
                raise SourceError('Phân vùng super vượt giới hạn 32 GiB.')
            parsed[name] = {'name':name,'size':total,'supported':bool(total) and not attrs & 8,'spans':spans,'reader':self.reader}
        allocations.sort()
        if any(a[1] > b[0] for a,b in zip(allocations,allocations[1:])):
            raise SourceError('Extents super chồng lấn; không xuất ảnh không rõ nguồn.')
        self.parts = {name:part for name,part in parsed.items() if part['supported']}
        if not self.parts:
            raise SourceError('Super không có phân vùng chứa dữ liệu (có thể là super_empty).')
        # A single populated slot gets the familiar unsuffixed names.
        for name in list(self.parts):
            if name.endswith(('_a','_b')):
                base = name[:-2]
                other = base + ('_b' if name.endswith('_a') else '_a')
                if base not in self.parts and other not in self.parts:
                    self.parts[base] = self.parts.pop(name)
                    self.parts[base]['name'] = base
