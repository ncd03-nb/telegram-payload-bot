import asyncio
import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

from app.bundle import Bundle
from app.runner import send_file
from app.security import SourceError


class BundleTests(unittest.IsolatedAsyncioTestCase):
    async def test_archive_preserves_exact_images_and_removes_intermediate_files(self):
        images={'boot.img':b'Android boot image'*4096,'init_boot.img':b'init boot'*8192}
        with tempfile.TemporaryDirectory() as folder:
            with Bundle(folder,'abcdef012345') as bundle:
                for name,data in images.items():
                    path=Path(folder)/name; path.write_bytes(data)
                    await bundle.add({'path':str(path),'name':name,'size':len(data),'sha256':hashlib.sha256(data).hexdigest()})
                    self.assertFalse(path.exists())
                result=await bundle.finish()
            with zipfile.ZipFile(result['path']) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(set(archive.namelist()),set(images))
                for name,data in images.items(): self.assertEqual(archive.read(name),data)
            self.assertEqual(result['sha256'],hashlib.sha256(Path(result['path']).read_bytes()).hexdigest())
            self.assertEqual(len(result['members']),2)
            self.assertEqual([p.name for p in Path(folder).iterdir()],['payload-abcdef012345.zip'])

    async def test_foreign_path_rejected_and_partial_bundle_removed(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as other:
            path=Path(other)/'boot.img'; path.write_bytes(b'image')
            with self.assertRaises(SourceError):
                with Bundle(folder,'abcdef012345') as bundle:
                    await bundle.add({'path':str(path),'name':'boot.img','size':5,'sha256':'fixture'})
            self.assertEqual(list(Path(folder).iterdir()),[])
            self.assertTrue(path.exists())

    async def test_multiple_partition_archive_sends_one_message_with_one_button(self):
        telegram=type('Telegram',(),{'send':AsyncMock()})()
        record={'name':'payload-abcdef012345.zip','size':1024,'url':'https://drive.google.com/file',
                'members':[{'name':'boot.img'},{'name':'init_boot.img'}]}
        await send_file(telegram,1,record,reply_to=42,thread_id=5)
        telegram.send.assert_awaited_once()
        args=telegram.send.await_args
        self.assertIn('2 phân vùng',args.args[1])
        self.assertIn('boot.img, init_boot.img',args.args[1])
        self.assertEqual(args.kwargs['reply_to'],42)
        self.assertEqual(args.kwargs['thread_id'],5)
        self.assertEqual(args.kwargs['reply_markup']['inline_keyboard'],[[{'text':'⬇ Tải ZIP từ Drive','url':record['url']}]])
