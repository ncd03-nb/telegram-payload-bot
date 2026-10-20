"""Automatically select OTA payload, partition-image ZIP, or super image."""
import shutil
import time
import zlib
from pathlib import Path, PurePosixPath

from .payload import Payload, file_checksums
from .security import SourceError
from .super_image import SuperImage
from .threads import thread_call
from .zip_images import ZipImages, partition_name


async def load_source(remote):
    head = await remote.read(0, 24)
    if head[:4] == b'CrAU':
        return await Payload(remote).load()
    source = ImageSource(remote)
    if head[:4] == b'PK\x03\x04':
        archive = await ZipImages(remote).load()
        if any(e['name'] == 'payload.bin' for e in archive.entries):
            return await Payload(remote).load()
        images = [e for e in archive.entries if e['name'].lower().endswith('.img')]
        supers = [e for e in images if PurePosixPath(e['name']).name.lower() == 'super.img']
        if len(supers) > 1:
            raise SourceError('ZIP có nhiều super.img; cần một ảnh super đầy đủ.')
        for entry in images:
            if entry in supers:
                continue
            name = partition_name(entry['name'])
            if name in source.parts:
                raise SourceError('ZIP chứa nhiều ảnh cùng tên phân vùng; không tự chọn nhầm ảnh.')
            source.parts[name] = {'name':name,'size':entry['size'],'supported':entry['size']>0 and not entry['flags'] & (1|0x40) and entry['method'] in {0,8},
                                  'entry':entry,'archive':archive}
        if supers:
            reader = await archive.open(supers[0])
            logical = await SuperImage(reader).load()
            source.add(logical.parts)
    else:
        source.add((await SuperImage(remote).load()).parts)
    if not source.parts:
        raise SourceError('Không tìm thấy payload.bin, ảnh .img hoặc super.img trong nguồn.')
    return source


class ImageSource:
    trusted_hashes = False

    def __init__(self, remote):
        self.remote, self.parts = remote, {}

    def add(self, parts):
        if set(parts) & set(self.parts):
            raise SourceError('Tên ảnh trong ZIP trùng phân vùng super; không tự chọn nhầm nguồn.')
        self.parts.update(parts)

    def summary(self):
        return [{'name':p['name'],'size':p['size'],'supported':p['supported'],'sha256':''} for p in self.parts.values()]

    async def extract(self, name, directory, progress=None, workers=8, batch_bytes=8*1024**2, decode_workers=2):
        part = self.parts.get(name)
        if not part or not part['supported']:
            raise SourceError('Phân vùng không tồn tại hoặc ảnh ZIP bị mã hóa/không hỗ trợ.')
        directory = Path(directory); directory.mkdir(parents=True,exist_ok=True)
        if shutil.disk_usage(directory).free < part['size'] + 256*1024**2:
            raise SourceError('Runner không đủ chỗ cho ảnh phân vùng và cache super.')
        if not 1 <= workers <= 16 or not 1 <= batch_bytes <= 32*1024**2:
            raise ValueError('Invalid extraction pipeline limits')
        partial, path = directory/(name+'.img.partial'), directory/(name+'.img')
        started, before = time.monotonic(), self.remote.requests
        reader = part.get('reader')
        spans = part.get('spans')
        if 'entry' in part:
            reader = await part['archive'].open(part['entry'])
            spans = [(0,part['size'])]
        done = crc = batches = 0
        try:
            with partial.open('wb') as out:
                for start,length in spans:
                    offset = 0
                    while offset < length:
                        count = min(batch_bytes,length-offset)
                        if shutil.disk_usage(directory).free < count+128*1024**2:
                            raise SourceError('Runner không đủ dung lượng; không upload ảnh dở dang.')
                        raw = await reader.read(start+offset,count) if start is not None else bytes(count)
                        if len(raw) != count:
                            raise SourceError('Nguồn ảnh bị cắt ngắn.')
                        await thread_call(out.write,raw)
                        crc = zlib.crc32(raw,crc)
                        done += count; offset += count; batches += 1
                        if progress:
                            await progress(done,part['size'])
            if done != part['size'] or 'entry' in part and crc != part['entry']['crc']:
                raise SourceError('CRC32/kích thước ảnh không khớp ZIP; không upload ảnh lỗi.')
            extracted = time.monotonic()
            checksums = await thread_call(file_checksums,partial)
            partial.replace(path)
            return {'path':str(path),'name':name+'.img','size':done,**checksums,
                    'metrics':{'extract_seconds':round(extracted-started,3),'verify_seconds':round(time.monotonic()-extracted,3),
                               'range_requests':self.remote.requests-before,'operations':len(spans),'batches':batches,'workers':1,
                               'format':'zip-image' if 'entry' in part else 'super'}}
        finally:
            partial.unlink(missing_ok=True)
            if 'entry' in part:
                reader.close()
