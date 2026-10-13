import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.remote import Remote
from app.security import SourceError


class DownloadCheckTests(unittest.IsolatedAsyncioTestCase):
    def session(self, location):
        response=MagicMock(status=302,headers={'Location':location})
        context=MagicMock()
        context.__aenter__=AsyncMock(return_value=response)
        context.__aexit__=AsyncMock(return_value=False)
        session=MagicMock()
        session.get.return_value=context
        session.close=AsyncMock()
        return session

    async def test_cn_downloadcheck_uses_updater_headers_then_validated_cdn(self):
        session=self.session('https://cdn.example.com/rom.zip')
        with patch('app.remote.aiohttp.TCPConnector'), patch('app.remote.aiohttp.ClientSession',return_value=session), \
                patch.object(Remote,'read',new_callable=AsyncMock) as read:
            async with Remote('https://component-ota-cn.allawntech.com/downloadCheck?c=fixture') as remote:
                self.assertEqual(remote.url,'https://cdn.example.com/rom.zip')
                headers=session.get.call_args.kwargs['headers']
                self.assertEqual(headers['userId'],'oplus-ota|16002018')
                self.assertEqual(headers['User-Agent'],'okhttp/3.12.12')
                self.assertFalse(session.get.call_args.kwargs['allow_redirects'])
                read.assert_awaited_once_with(0,32)
        session.close.assert_awaited_once()

    async def test_lookalike_host_cannot_receive_updater_headers(self):
        for host in ('component-ota-cn.allawntech.com.example.org','fakeallawntech.com'):
            session=self.session('https://cdn.example.com/rom.zip')
            with patch('app.remote.aiohttp.TCPConnector'), patch('app.remote.aiohttp.ClientSession',return_value=session):
                with self.assertRaises(SourceError):
                    async with Remote('https://'+host+'/downloadCheck'): pass
            session.get.assert_not_called()
            session.close.assert_awaited_once()

    async def test_cn_redirect_to_private_address_remains_blocked(self):
        session=self.session('http://127.0.0.1/rom.zip')
        with patch('app.remote.aiohttp.TCPConnector'), patch('app.remote.aiohttp.ClientSession',return_value=session), \
                patch.object(Remote,'read',new_callable=AsyncMock) as read:
            with self.assertRaises(SourceError):
                async with Remote('https://component-ota-cn.allawntech.com/downloadCheck'): pass
            read.assert_not_awaited()
        session.close.assert_awaited_once()
