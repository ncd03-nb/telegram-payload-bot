import asyncio
import hashlib
import io
import struct
import tempfile
import unittest
import zipfile
import zlib
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path

from app.image_source import load_source
from app.remote import Remote
from app.security import SourceError
from app.super_image import SparseImage, SuperImage
from test_payload import fixture


class Memory:
    payload_region = Remote.payload_region
    metadata_version = Remote.metadata_version

    def __init__(self, data, directory):
        self.data, self.size, self.directory = data, len(data), Path(directory)
        self.requests = self.downloaded = 0
        self.rom_version = ''

    def cache_directory(self):
        return self.directory

    async def read(self, offset, length):
        if offset < 0 or length <= 0 or offset + length > self.size:
            raise SourceError('bounds')
        self.requests += 1; self.downloaded += length
        return self.data[offset:offset+length]


def super_fixture(bad_extent=False):
    raw = bytearray(128*1024)
    geo = bytearray(struct.pack('<II32sIII',0x616c4467,52,bytes(32),4096,1,4096))
    geo[8:40] = hashlib.sha256(geo).digest()
    raw[4096:4096+52] = raw[8192:8192+52] = geo
    parts = (struct.pack('<36s4I',b'system_a',1,0,3,0) + struct.pack('<36s4I',b'vendor_a',1,3,1,0)
             + struct.pack('<36s4I',b'vendor_b',1,4,0,0))
    extents = b''.join(struct.pack('<QIQI',*x) for x in [(4,0,1000000 if bad_extent else 80,0),(2,1,0,0),(2,0,84,0),(8,0,88,0)])
    groups = struct.pack('<36sIQ',b'default',0,0)
    device = struct.pack('<QIIQ36sI',64,4096,0,len(raw),b'super',0)
    tables = parts+extents+groups+device
    head = bytearray(128)
    struct.pack_into('<IHHI',head,0,0x414c5030,10,0,128)
    struct.pack_into('<I',head,44,len(tables));head[48:80]=hashlib.sha256(tables).digest()
    pos = 0
    for off,table,size in [(80,parts,52),(92,extents,24),(104,groups,48),(116,device,64)]:
        struct.pack_into('<III',head,off,pos,len(table)//size,size);pos+=len(table)
    head[12:44] = hashlib.sha256(head).digest()
    raw[12288:12288+128+len(tables)] = head+tables
    raw[16384:16384+128+len(tables)] = head+tables
    raw[80*512:84*512] = b'S'*2048
    raw[84*512:86*512] = b'T'*1024
    raw[88*512:96*512] = b'V'*4096
    return bytes(raw), b'S'*2048+bytes(1024)+b'T'*1024


def sparse(raw):
    # RAW, FILL and DONT_CARE, followed by RAW: exercise all data mappings.
    logical = raw[:65536]+b'abcd'*1024+bytes(4096)+raw[73728:]
    chunks = [(0xcac1,16,raw[:65536]),(0xcac2,1,b'abcd'),(0xcac3,1,b''),(0xcac1,(len(raw)-73728)//4096,raw[73728:])]
    out = struct.pack('<I4H4I',0xed26ff3a,1,0,28,12,4096,len(raw)//4096,len(chunks),0)
    for kind,blocks,data in chunks:
        out += struct.pack('<2H2I',kind,0,blocks,len(data)+12)+data
    return out, logical


def archive(entries, method=zipfile.ZIP_DEFLATED, zip64=False):
    out = io.BytesIO()
    with zipfile.ZipFile(out,'w',compression=method) as z:
        for name,data in entries:
            with z.open(name,'w',force_zip64=zip64) as f:
                f.write(data)
    return out.getvalue()


class ImageTests(unittest.IsolatedAsyncioTestCase):
    async def test_cache_uses_configured_writable_data_mount_and_is_cleaned(self):
        with tempfile.TemporaryDirectory() as folder:
            remote=Remote('https://example.com/rom.zip',cache_dir=folder)
            remote.session=MagicMock(close=AsyncMock())
            cache=remote.cache_directory()
            self.assertEqual(cache.parent,Path(folder).resolve())
            (cache/'entry').write_bytes(b'test')
            await remote.__aexit__(None,None,None)
            self.assertFalse(cache.exists())

    async def test_existing_payload_detection_preserves_manifest_hash_verification(self):
        payload,image = fixture(8)
        for data in (payload,archive([('payload.bin',payload)],zipfile.ZIP_STORED,True)):
            with tempfile.TemporaryDirectory() as folder:
                source=await load_source(Memory(data,folder))
                result=await source.extract('boot',folder)
                self.assertEqual(Path(result['path']).read_bytes(),image)

    async def test_folder_images_deflate_stored_zip64_and_crc(self):
        image=b'Android image bytes'*20000
        for method in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED):
            with tempfile.TemporaryDirectory() as folder:
                r=Memory(archive([('system/my_stock.img',image),('firmware-update/boot.img',b'x'*4096)],method,True),folder)
                source=await load_source(r)
                self.assertEqual(set(source.parts),{'my_stock','boot'})
                result=await source.extract('my_stock',folder)
                self.assertEqual(Path(result['path']).read_bytes(),image)
                self.assertEqual(result['sha256'],hashlib.sha256(image).hexdigest())

    async def test_raw_and_sparse_super_direct_and_nested_zip_extract_extents(self):
        raw,expected=super_fixture()
        for image in (raw,sparse(raw)[0]):
            for data in (image,archive([('super/super.img',image)],zipfile.ZIP_STORED,True),archive([('super/super.img',image)])):
                with tempfile.TemporaryDirectory() as folder:
                    source=await load_source(Memory(data,folder))
                    self.assertEqual(set(source.parts),{'system','vendor'})
                    result=await source.extract('system',folder,batch_bytes=777)
                    self.assertEqual(Path(result['path']).read_bytes(),expected)
                    self.assertEqual(result['sha256'],hashlib.sha256(expected).hexdigest())

    async def test_super_combines_existing_boot_image_with_logical_partitions(self):
        raw,_=super_fixture()
        with tempfile.TemporaryDirectory() as folder:
            source=await load_source(Memory(archive([('images/boot.img',b'B'*4096),('super/super.img',raw)]),folder))
            self.assertEqual(set(source.parts),{'boot','system','vendor'})

    async def test_sparse_cross_chunk_reads_unaligned_fill_and_zero(self):
        raw,_=super_fixture();packed,logical=sparse(raw)
        with tempfile.TemporaryDirectory() as folder:
            reader=await SparseImage.open(Memory(packed,folder))
            self.assertEqual(await reader.read(65533,8200),logical[65533:65533+8200])

    async def test_metadata_hash_failure_uses_backup_but_both_corrupt_fail(self):
        raw,_=super_fixture();broken=bytearray(raw);broken[12288+12]^=1
        with tempfile.TemporaryDirectory() as folder:
            source=await load_source(Memory(bytes(broken),folder))
            self.assertIn('system',source.parts)
            broken[16384+12]^=1
            with self.assertRaisesRegex(SourceError,'metadata'):
                await load_source(Memory(bytes(broken),folder))

    async def test_geometry_checksum_tables_checksum_and_extent_bounds_rejected(self):
        raw,_=super_fixture()
        variants=[]
        broken=bytearray(raw);broken[4104]^=1;broken[8200]^=1;variants.append(bytes(broken))
        broken=bytearray(raw);broken[12288+128]^=1;broken[16384+128]^=1;variants.append(bytes(broken))
        variants.append(super_fixture(bad_extent=True)[0])
        for data in variants:
            with tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(SourceError):await load_source(Memory(data,folder))

    async def test_zip_wrong_crc_never_commits_image(self):
        for method in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED):
            data=bytearray(archive([('system/a.img',b'x'*4096)],method))
            pos=data.find(b'PK\x01\x02');data[pos+16]^=1
            with tempfile.TemporaryDirectory() as folder:
                source=await load_source(Memory(bytes(data),folder))
                with self.assertRaisesRegex(SourceError,'CRC32'):
                    await source.extract('a',folder)
                self.assertFalse((Path(folder)/'a.img').exists())
                self.assertFalse((Path(folder)/'a.img.partial').exists())

    async def test_zip_traversal_duplicate_partition_and_encryption_rejected(self):
        examples=[archive([('../system.img',b'x'*4096)]),
                  archive([('system/boot.img',b'x'*4096),('images/boot.img',b'y'*4096)])]
        for data in examples:
            with tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(SourceError):await load_source(Memory(data,folder))
        data=bytearray(archive([('system/a.img',b'x'*4096)]));p=data.find(b'PK\x01\x02');data[p+8]|=1
        with tempfile.TemporaryDirectory() as folder:
            source=await load_source(Memory(bytes(data),folder))
            self.assertFalse(source.parts['a']['supported'])
            with self.assertRaises(SourceError):await source.extract('a',folder)

    async def test_compressed_super_metadata_read_does_not_inflate_whole_image(self):
        raw,_=super_fixture();raw+=bytes(8*1024**2)
        # Geometry's device remains within the larger source image.
        with tempfile.TemporaryDirectory() as folder:
            source=await load_source(Memory(archive([('super/super.img',raw)]),folder))
            self.assertIn('vendor',source.parts)
            cache=[p for p in Path(folder).iterdir() if p.name.startswith('entry-')]
            self.assertLess(sum(p.stat().st_size for p in cache),len(raw))

    async def test_sparse_bad_chunk_bounds_rejected(self):
        raw,_=super_fixture();packed=bytearray(sparse(raw)[0]);struct.pack_into('<I',packed,28+8,13)
        with tempfile.TemporaryDirectory() as folder:
            reader=await SparseImage.open(Memory(bytes(packed),folder))
            with self.assertRaises(SourceError):await reader.read(0,4096)
