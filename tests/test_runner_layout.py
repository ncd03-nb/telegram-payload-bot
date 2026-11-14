"""Exercise the real extract/bundle pipeline, mocking only services and transport."""
import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app import runner
from app.settings import Settings
from test_image_source import Memory


class LocalRemote(Memory):
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class RunnerLayoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_oplus_all_is_grouped_but_explicit_selection_stays_flat(self):
        images={'system/my_stock.img':b'my stock'*1024,
                'images/boot.img':b'boot'*2048, 'firmware/abl.img':b'abl'*1024}
        source=io.BytesIO()
        with zipfile.ZipFile(source,'w',compression=zipfile.ZIP_DEFLATED) as archive:
            for name,data in images.items():
                archive.writestr(name,data)
        cases=(('all', {'SYSTEM/my_stock.img', 'BOOTLOADER/boot.img', 'CRITICAL/abl.img'}),
               ('boot,abl', {'boot.img','abl.img'}), ('boot', None))
        for requested, expected in cases:
            with self.subTest(requested=requested), tempfile.TemporaryDirectory() as folder:
                remote=LocalRemote(source.getvalue(),folder)
                remote.rom_version='PJF110_16.0.5.1001(CN01)'
                drive=MagicMock()
                drive.__aenter__=AsyncMock(return_value=drive)
                drive.__aexit__=AsyncMock(return_value=False)
                drive.token=AsyncMock()
                async def upload(file, *args, **kwargs):
                    if expected is None:
                        self.assertEqual(file['name'],'boot.img')
                        self.assertEqual(Path(file['path']).read_bytes(),images['images/boot.img'])
                    else:
                        with zipfile.ZipFile(file['path']) as archive:
                            self.assertIsNone(archive.testzip())
                            self.assertEqual({i.filename for i in archive.infolist() if not i.is_dir()},expected)
                            if requested=='all':
                                self.assertEqual({i.filename for i in archive.infolist() if i.is_dir()},
                                                 {'SYSTEM/','CRITICAL/','BOOTLOADER/'})
                        self.assertEqual({m['name'] for m in file['members']},expected)
                    return {k:file[k] for k in ('name','size','sha256')} | {'url':'https://example.com/result'}
                drive.upload=AsyncMock(side_effect=upload)
                telegram=MagicMock(start=AsyncMock(),close=AsyncMock(),edit=AsyncMock(),send=AsyncMock())
                settings=Settings(token='1:fixture',users={1},data_dir=Path(folder),public_files=True)
                env={'JOB_ID':'abcdef012345','JOB_OWNER':'1','JOB_CHAT':'1','JOB_MESSAGE_ID':'42',
                     'JOB_SOURCE_URL':'https://drive.google.com/file/d/example12345/view',
                     'JOB_PARTITIONS':requested,'RESULT_PATH':str(Path(folder)/'result.json')}
                with patch.dict(os.environ,env,clear=True), patch.object(runner,'load_job_env'), \
                     patch.object(runner.Settings,'load',return_value=settings), \
                     patch.object(runner,'Remote',return_value=remote), \
                     patch.object(runner,'Drive',return_value=drive), \
                     patch.object(runner,'Telegram',return_value=telegram), \
                     patch.object(runner.Progress,'loop',new=AsyncMock()), \
                     patch.object(runner,'notify_result',new=AsyncMock()), \
                     patch.object(runner,'write_result') as write_result:
                    self.assertEqual(await runner.run(),0)
                drive.upload.assert_awaited_once()
                result=write_result.call_args.args[1]
                self.assertEqual(result['state'],'done')
                self.assertEqual(len(result['files']),1)
                if expected is not None:
                    self.assertEqual({m['name'] for m in result['files'][0]['members']},expected)
                telegram.send.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
