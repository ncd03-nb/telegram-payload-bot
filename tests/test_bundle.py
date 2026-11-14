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
    async def test_oplus_all_zip_has_exact_three_folders_and_verified_image_contents(self):
        names={'system.img':'SYSTEM', 'my_stock.img':'SYSTEM', 'abl.img':'CRITICAL',
               'xbl_config.img':'CRITICAL', 'boot.img':'BOOTLOADER',
               'vbmeta_system.img':'BOOTLOADER', 'boot_a.img':'BOOTLOADER',
               'system_ext_b.img':'SYSTEM', 'unknown_firmware.img':'CRITICAL'}
        images={name:(name.encode()*1024) for name in names}
        with tempfile.TemporaryDirectory() as folder:
            with Bundle(folder,'abcdef012345',layout='oplus') as bundle:
                for name,data in images.items():
                    path=Path(folder)/name; path.write_bytes(data)
                    await bundle.add({'path':str(path),'name':name,'size':len(data),'sha256':hashlib.sha256(data).hexdigest()})
                    self.assertFalse(path.exists())
                result=await bundle.finish()
            with zipfile.ZipFile(result['path']) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual({p.split('/')[0] for p in archive.namelist()}, {'SYSTEM','CRITICAL','BOOTLOADER'})
                self.assertEqual({i.filename for i in archive.infolist() if i.is_dir()},
                                 {'SYSTEM/','CRITICAL/','BOOTLOADER/'})
                self.assertEqual(len(archive.infolist()),len(images)+3)
                for name,data in images.items():
                    target=names[name]+'/'+name
                    self.assertEqual(archive.read(target),data)
                    member=next(m for m in result['members'] if m['name']==target)
                    self.assertEqual(member['sha256'],hashlib.sha256(data).hexdigest())
            self.assertEqual(result['sha256'],hashlib.sha256(Path(result['path']).read_bytes()).hexdigest())
            self.assertEqual(len(result['members']),len(images))

    async def test_oplus_zip_keeps_empty_categories_even_for_one_image(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'boot.img'; path.write_bytes(b'boot')
            with Bundle(folder,'abcdef012345',layout='oplus') as bundle:
                await bundle.add({'path':str(path),'name':path.name,'size':4,'sha256':hashlib.sha256(b'boot').hexdigest()})
                result=await bundle.finish()
            with zipfile.ZipFile(result['path']) as archive:
                self.assertEqual(set(archive.namelist()), {'SYSTEM/','CRITICAL/','BOOTLOADER/','BOOTLOADER/boot.img'})

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

    async def test_runner_edits_progress_instead_of_sending_another_result(self):
        telegram=type('Telegram',(),{'send':AsyncMock(),'edit':AsyncMock()})()
        record={'name':'boot.img','size':1024,'url':'https://drive.google.com/file','rom_version':'OS3.test'}
        await send_file(telegram,1,record,message_id=42)
        telegram.send.assert_not_awaited()
        telegram.edit.assert_awaited_once()
        args=telegram.edit.await_args
        self.assertEqual(args.args[:2],(1,42))
        self.assertIn('boot.img',args.args[2])
        self.assertEqual(args.kwargs['reply_markup']['inline_keyboard'][0][0]['url'],record['url'])
