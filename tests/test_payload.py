import bz2
import hashlib
import io
import lzma
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestServer
import aiohttp

from app.payload import Payload, fields, decompress
from app.remote import Remote
from app.runner import selection
from app.security import SourceError, validate_url
from app.store import Store


def var(n):
    out = bytearray()
    while n > 127:
        out.append((n & 127) | 128); n >>= 7
    return bytes(out) + bytes([n])


def f(n, value):
    return var(n << 3) + var(value) if isinstance(value, int) else var((n << 3) | 2) + var(len(value)) + value


def fixture(kind=0, final_hash=None, op_hash=None, name=b'boot', extent_start=0):
    image = (b'Android verified payload ' * 256)[:4096].ljust(4096, b'\0')
    raw = {0: lambda x:x, 1:bz2.compress, 8:lzma.compress}.get(kind, lambda x:x)(image)
    if kind in {6, 7}:
        image, raw = bytes(4096), b''
    extent = f(1, extent_start) + f(2, 1)
    op = f(1, kind) + f(2, 0) + f(3, len(raw)) + f(6, extent)
    if raw: op += f(8, op_hash or hashlib.sha256(raw).digest())
    info = f(1,len(image)) + f(2,final_hash or hashlib.sha256(image).digest())
    part = f(1,name) + f(7,info) + f(8,op)
    manifest = f(3,4096) + f(13,part)
    return struct.pack('>4sQQI', b'CrAU',2,len(manifest),0) + manifest + raw, image


class MemoryRemote:
    payload_region = Remote.payload_region
    def __init__(self, data): self.data, self.size = data, len(data)
    async def read(self, offset, length):
        if offset < 0 or offset + length > self.size: raise SourceError('bounds')
        return self.data[offset:offset+length]


class PayloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_ops_and_final_hash(self):
        for kind in (0,1,6,7,8):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder:
                data,image=fixture(kind)
                payload=await Payload(MemoryRemote(data)).load()
                result=await payload.extract('boot',folder)
                self.assertEqual(Path(result['path']).read_bytes(),image)
                self.assertEqual(result['sha256'],hashlib.sha256(image).hexdigest())

    async def test_ota_zip64_local_header(self):
        data,image=fixture(8)
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w') as z:
            z.writestr('META-INF/metadata','OTA')
            with z.open('payload.bin','w',force_zip64=True) as out: out.write(data)
        payload=await Payload(MemoryRemote(buf.getvalue())).load()
        with tempfile.TemporaryDirectory() as folder:
            result=await payload.extract('boot',folder)
            self.assertEqual(Path(result['path']).read_bytes(),image)

    async def test_corrupt_operation_or_final_hash_removes_partial(self):
        for kwargs in ({'op_hash':bytes(32)},{'final_hash':bytes(32)}):
            payload=await Payload(MemoryRemote(fixture(**kwargs)[0])).load()
            with tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(SourceError): await payload.extract('boot',folder)
                self.assertEqual(list(Path(folder).iterdir()),[])

    async def test_extent_outside_image_rejected(self):
        payload=await Payload(MemoryRemote(fixture(extent_start=1)[0])).load()
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(SourceError): await payload.extract('boot',folder)

    async def test_delta_is_listed_but_not_extracted(self):
        payload=await Payload(MemoryRemote(fixture(kind=4)[0])).load()
        self.assertFalse(payload.summary()[0]['supported'])
        with self.assertRaises(SourceError): selection('all',payload.parts)

    async def test_partition_name_traversal_rejected_but_hyphen_allowed(self):
        with self.assertRaises(SourceError): await Payload(MemoryRemote(fixture(name=b'../boot')[0])).load()
        payload=await Payload(MemoryRemote(fixture(name=b'vm-bootsys')[0])).load()
        self.assertIn('vm-bootsys',payload.parts)

    async def test_compressed_zip_payload_rejected(self):
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w',compression=zipfile.ZIP_DEFLATED) as z: z.writestr('payload.bin',fixture()[0])
        with self.assertRaises(SourceError): await Payload(MemoryRemote(buf.getvalue())).load()

    def test_protobuf_truncation_and_decompression_bomb(self):
        for raw in (b'\x80', b'\x0a\x7fsmall', b'\x00'):
            with self.assertRaises(SourceError): list(fields(raw))
        for kind,compress in ((1,bz2.compress),(8,lzma.compress)):
            with self.assertRaises(SourceError): decompress(compress(bytes(100000)),kind,4096)

    def test_source_url_security(self):
        for url in ('http://127.0.0.1/a','http://169.254.169.254/','https://user:pass@example.com/file','https://localhost/a','https://example.com:8080/a'):
            with self.assertRaises(SourceError): validate_url(url)

    def test_resume_actions_does_not_requeue_or_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db')
            j=store.add(1,1,1,'https://example.com/file','boot',backend='actions')
            store.claim(); store.change(j['id'],dispatch_at=1,run_id=123,url='')
            store.recover()
            current=store.claim()
            self.assertEqual(current['run_id'],123)
            self.assertEqual(current['dispatch_at'],1)
            self.assertEqual(current['url'],'')
            self.assertIsNone(store.get(j['id'],2))
            store.close()


class RangeTests(unittest.IsolatedAsyncioTestCase):
    async def test_oplus_mislabeled_utf8_ranges_extract_verified_image(self):
        data, image = fixture(8)
        for encoding in ('UTF-8', 'utf8', 'Identity'):
            async def handler(request):
                start, end = map(int, request.headers['Range'][6:].split('-'))
                return web.Response(body=data[start:end+1], status=206, headers={
                    'Content-Range':f'bytes {start}-{end}/{len(data)}',
                    'Content-Encoding':encoding})
            app=web.Application(); app.router.add_get('/',handler)
            async with TestServer(app) as server:
                remote=Remote('https://example.com/file'); remote.url=str(server.make_url('/'))
                async with aiohttp.ClientSession(auto_decompress=False) as session:
                    remote.session=session
                    with patch('app.remote.validate_url',side_effect=lambda x:x):
                        await remote.read(0,32)
                        payload=await Payload(remote).load()
                        with tempfile.TemporaryDirectory() as folder:
                            result=await payload.extract('boot',folder)
                            self.assertEqual(Path(result['path']).read_bytes(),image)
                            self.assertEqual(result['sha256'],hashlib.sha256(image).hexdigest())

    async def test_encoded_or_invalid_utf8_ranges_remain_rejected(self):
        for encoding,content_range,body in (
            ('gzip','bytes 0-31/100',bytes(32)),
            ('UTF-8',None,bytes(32)),
            ('UTF-8','bytes 1-32/100',bytes(32)),
            ('UTF-8','bytes 0-31/100',bytes(33))):
            async def handler(request):
                headers={'Content-Encoding':encoding}
                if content_range: headers['Content-Range']=content_range
                return web.Response(body=body,status=206,headers=headers)
            app=web.Application(); app.router.add_get('/',handler)
            async with TestServer(app) as server:
                remote=Remote('https://example.com/file'); remote.url=str(server.make_url('/'))
                async with aiohttp.ClientSession(auto_decompress=False) as session:
                    remote.session=session
                    with patch('app.remote.validate_url',side_effect=lambda x:x):
                        with self.assertRaises(SourceError): await remote.read(0,32)

    async def test_wrong_content_range_and_ignored_range_rejected(self):
        for status,content_range in ((200,None),(206,'bytes 1-32/100')):
            async def handler(request):
                headers={'Content-Range':content_range} if content_range else {}
                return web.Response(body=bytes(32),status=status,headers=headers)
            app=web.Application(); app.router.add_get('/',handler)
            async with TestServer(app) as server:
                remote=Remote('https://example.com/file'); remote.url=str(server.make_url('/'))
                async with aiohttp.ClientSession() as session:
                    remote.session=session
                    with patch('app.remote.validate_url',side_effect=lambda x:x):
                        with self.assertRaises(SourceError): await remote.read(0,32)

    async def test_changed_etag_rejected(self):
        async def handler(request):
            return web.Response(body=bytes(32),status=206,headers={'Content-Range':'bytes 0-31/100','ETag':'new'})
        app=web.Application(); app.router.add_get('/',handler)
        async with TestServer(app) as server:
            remote=Remote('https://example.com/file'); remote.url=str(server.make_url('/')); remote.etag='old'
            async with aiohttp.ClientSession() as session:
                remote.session=session
                with patch('app.remote.validate_url',side_effect=lambda x:x):
                    with self.assertRaises(SourceError): await remote.read(0,32)
