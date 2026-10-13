import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiohttp

from app.gdrive import Drive
from app.security import SourceError


class Response:
    def __init__(self, status, obj=None, headers=None, error=None):
        self.status, self.obj, self.headers, self.error = status, obj, headers or {}, error

    async def __aenter__(self):
        if self.error: raise self.error
        return self

    async def __aexit__(self, *args): pass
    async def json(self): return self.obj


class UploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_uncertain_chunk_commit_queries_offset_before_resending(self):
        with tempfile.TemporaryDirectory() as folder:
            data = b'A' * (8*1024**2) + b'B' * 1024
            path = Path(folder)/'boot.img'; path.write_bytes(data)
            result = {'path':str(path),'name':'boot.img','size':len(data),'sha256':hashlib.sha256(data).hexdigest()}
            meta = {'id':'drive-id','size':str(len(data)),'md5Checksum':hashlib.md5(data).hexdigest()}
            class Session:
                def __init__(self): self.puts = []
                def post(self,*args,**kwargs):
                    return Response(200,headers={'Location':'https://www.googleapis.com/upload/session'})
                def put(self,url,**kwargs):
                    self.puts.append(kwargs)
                    if len(self.puts)==1:
                        # Server committed first chunk, response was lost.
                        return Response(0,error=aiohttp.ClientConnectionError('lost response'))
                    if len(self.puts)==2:
                        return Response(308,headers={'Range':'bytes=0-8388607'})
                    return Response(200,obj=meta)
            drive=Drive('unused'); drive.session=Session()
            drive.token=AsyncMock(return_value='fixture'); drive.folder=AsyncMock(return_value='folder')
            drive.api=AsyncMock()
            with patch('app.gdrive.asyncio.sleep',new=AsyncMock()):
                uploaded=await drive.upload(result,'abcdef012345',1)
            puts=drive.session.puts
            self.assertEqual(puts[1]['data'],b'')
            self.assertEqual(puts[1]['headers']['Content-Range'],f'bytes */{len(data)}')
            self.assertEqual(puts[2]['data'],b'B'*1024)
            self.assertEqual(puts[2]['headers']['Content-Range'],f'bytes 8388608-{len(data)-1}/{len(data)}')
            self.assertEqual(uploaded['id'],'drive-id')
            drive.api.assert_not_awaited()

    async def test_public_upload_grants_anyone_reader_after_checksum_verification(self):
        with tempfile.TemporaryDirectory() as folder:
            data=b'verified image'; path=Path(folder)/'boot.img'; path.write_bytes(data)
            result={'path':str(path),'name':'boot.img','size':len(data),'sha256':hashlib.sha256(data).hexdigest()}
            class Session:
                def post(self,*args,**kwargs): return Response(200,headers={'Location':'https://www.googleapis.com/upload/session'})
                def put(self,*args,**kwargs): return Response(200,obj={'id':'drive-id','size':str(len(data)),'md5Checksum':hashlib.md5(data).hexdigest()})
            drive=Drive('unused'); drive.session=Session(); drive.token=AsyncMock(return_value='fixture')
            drive.folder=AsyncMock(return_value='folder'); drive.api=AsyncMock()
            await drive.upload(result,'abcdef012345',2,public=True)
            drive.api.assert_awaited_once_with('POST','files/drive-id/permissions',
                {'type':'anyone','role':'reader','allowFileDiscovery':False})

    async def test_remote_checksum_mismatch_rejects_completion(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'boot.img'; path.write_bytes(b'original image')
            result={'path':str(path),'name':'boot.img','size':path.stat().st_size,'sha256':'fixture'}
            class Session:
                def post(self,*args,**kwargs): return Response(200,headers={'Location':'https://www.googleapis.com/upload/session'})
                def put(self,*args,**kwargs): return Response(200,obj={'id':'drive-id','size':str(result['size']),'md5Checksum':'wrong'})
            drive=Drive('unused'); drive.session=Session(); drive.token=AsyncMock(return_value='fixture')
            drive.folder=AsyncMock(return_value='folder')
            drive.api=AsyncMock()
            with self.assertRaises(SourceError): await drive.upload(result,'abcdef012345',1,public=True)
            drive.api.assert_not_awaited()

    async def test_upload_location_cannot_forward_google_token_to_foreign_host(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'boot.img'; path.write_bytes(b'image')
            class Session:
                def post(self,*args,**kwargs): return Response(200,headers={'Location':'https://attacker.example/upload'})
                def put(self,*args,**kwargs): raise AssertionError('must not send Google token')
            drive=Drive('unused'); drive.session=Session(); drive.token=AsyncMock(return_value='fixture')
            drive.folder=AsyncMock(return_value='folder')
            with self.assertRaises(SourceError):
                await drive.upload({'path':str(path),'name':'boot.img','size':5,'sha256':'fixture'},'abcdef012345',1)
