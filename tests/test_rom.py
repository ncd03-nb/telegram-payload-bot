import io
import unittest
import zipfile
from unittest.mock import AsyncMock

from app.remote import Remote
from app.rom import version_from_url, version_from_metadata
from app.runner import send_file
from test_payload import fixture


class RomTests(unittest.IsolatedAsyncioTestCase):
    def test_url_versions_ignore_query_and_support_encoded_oplus_name(self):
        self.assertEqual(version_from_url('https://example.com/OS4.0.24.0.XEOCNXM/rom.zip?token=secret'), 'OS4.0.24.0.XEOCNXM')
        self.assertEqual(version_from_url('https://example.com/CPH2609_15.0.0.501%28EX01%29.zip'), 'CPH2609_15.0.0.501(EX01)')
        self.assertEqual(version_from_url('https://example.com/payload.bin?secret=OS4.0.24.0.XEOCNXM'), '')

    def test_target_metadata_version_and_unknown_fallback(self):
        self.assertEqual(version_from_metadata(b'pre-build-incremental=OS3.0.1.0.XEOCNXM\npost-build-incremental=OS4.0.24.0.XEOCNXM'), 'OS4.0.24.0.XEOCNXM')
        self.assertEqual(version_from_metadata(b'post-build-incremental=123456'), 'Build 123456')
        self.assertEqual(version_from_metadata(b'pre-build-incremental=OS3.0.1.0.XEOCNXM\npost-build-incremental=https://secret.example'), '')

    async def test_remote_reads_small_stored_or_compressed_ota_metadata(self):
        for compression in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            buf=io.BytesIO()
            with zipfile.ZipFile(buf,'w') as z:
                z.writestr('META-INF/com/android/metadata',b'post-build-incremental=CPH2609_15.0.0.501(EX01)\n',compress_type=compression)
                z.writestr('payload.bin',fixture()[0])
            data=buf.getvalue(); remote=Remote('https://example.com/rom.zip'); remote.size=len(data)
            async def read(offset,length): return data[offset:offset+length]
            remote.read=read
            start,size=await remote.payload_region()
            self.assertEqual(data[start:start+4],b'CrAU')
            self.assertEqual(remote.rom_version,'CPH2609_15.0.0.501(EX01)')

    async def test_bad_optional_metadata_retains_url_version(self):
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w') as z:
            z.writestr('META-INF/com/android/metadata',b'post-build-incremental=bad\n')
            z.writestr('payload.bin',fixture()[0])
        data=buf.getvalue().replace(b'post-build-incremental=bad',b'post-build-incremental=foo',1)
        remote=Remote('https://example.com/OS4.0.24.0.XEOCNXM/rom.zip'); remote.size=len(data)
        async def read(offset,length): return data[offset:offset+length]
        remote.read=read
        await remote.payload_region()
        self.assertEqual(remote.rom_version,'OS4.0.24.0.XEOCNXM')

    async def test_internal_build_id_does_not_replace_named_rom_version(self):
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w') as z:
            z.writestr('META-INF/com/android/metadata',b'post-build-incremental=17OS4.0.261004.103610129.QCPECN.S\n')
            z.writestr('payload.bin',fixture()[0])
        data=buf.getvalue(); remote=Remote('https://example.com/OS4.0.24.0.XEOCNXM/rom.zip'); remote.size=len(data)
        async def read(offset,length): return data[offset:offset+length]
        remote.read=read
        await remote.payload_region()
        self.assertEqual(remote.rom_version,'OS4.0.24.0.XEOCNXM')

    async def test_large_metadata_is_skipped_without_reading_its_content(self):
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w') as z:
            z.writestr('META-INF/com/android/metadata',b'x'*100000,compress_type=zipfile.ZIP_DEFLATED)
            z.writestr('payload.bin',fixture()[0])
        data=buf.getvalue(); remote=Remote('https://example.com/rom.zip'); remote.size=len(data)
        reads=[]
        async def read(offset,length):
            reads.append((offset,length)); return data[offset:offset+length]
        remote.read=read
        await remote.payload_region()
        self.assertEqual(remote.rom_version,'')
        # Initial probe plus directory/payload local header; no metadata local header/read.
        self.assertEqual(len(reads),4)

    async def test_completion_preserves_rom_version_and_reply(self):
        tg=type('Telegram',(),{'send':AsyncMock()})()
        file={'name':'payload.zip','size':1024,'url':'https://drive.google.com/file',
              'rom_version':'OS4.0.24.0.XEOCNXM','members':[{'name':'boot.img'},{'name':'init_boot.img'}]}
        await send_file(tg,-100123,file,reply_to=12,thread_id=5)
        args=tg.send.await_args
        self.assertIn('📱 ROM: OS4.0.24.0.XEOCNXM',args.args[1])
        self.assertIn('boot.img, init_boot.img',args.args[1])
        self.assertEqual(args.kwargs['reply_to'],12)
        self.assertEqual(args.kwargs['thread_id'],5)
        del file['rom_version']
        await send_file(tg,-100123,file)
        self.assertIn('ROM: Không xác định',tg.send.await_args.args[1])
