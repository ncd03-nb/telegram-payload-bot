import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from app.drive_source import drive_file, resolve_drive
from app.remote import Remote
from app.security import SourceError


ID = 'example_Drive-file123'
LINK = 'https://drive.google.com/file/d/' + ID + '/view?usp=sharing'


def response(status, headers=None, body=b''):
    r = MagicMock(status=status, headers=headers or {})
    async def blocks(_):
        yield body
    r.content.iter_chunked = blocks
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=r)
    context.__aexit__ = AsyncMock(return_value=False)
    return context


class DriveSourceTests(unittest.IsolatedAsyncioTestCase):
    def test_link_formats_resource_keys_and_folder_rejection(self):
        for path in ('/file/d/' + ID + '/view', '/open?id=' + ID, '/uc?export=download&id=' + ID):
            self.assertEqual(drive_file('https://drive.google.com' + path), (ID, ''))
        self.assertEqual(drive_file(LINK + '&resourcekey=0_key'), (ID, '0_key'))
        self.assertEqual(drive_file('https://drive.usercontent.google.com/download?id=' + ID), (ID, ''))
        self.assertIsNone(drive_file('https://drive.google.com.evil.org/file/d/' + ID))
        with self.assertRaises(SourceError):
            drive_file('https://drive.google.com/drive/folders/' + ID)

    async def test_public_link_resolves_and_never_uses_owner_oauth(self):
        session = MagicMock()
        session.get.side_effect = [response(302, {'Location': 'https://download.googleusercontent.com/file'}),
                                  response(206, {'Content-Disposition': 'attachment; filename="OTA_OS3.0.6.0.VLLCNXM.zip"'})]
        url, name = await resolve_drive(session, LINK + '&resourcekey=0_key')
        self.assertEqual(url, 'https://download.googleusercontent.com/file')
        self.assertEqual(name, 'OTA_OS3.0.6.0.VLLCNXM.zip')
        query = parse_qs(urlsplit(session.get.call_args_list[0].args[0]).query)
        self.assertEqual(query['resourcekey'], ['0_key'])
        for call in session.get.call_args_list:
            self.assertNotIn('Authorization', call.kwargs['headers'])
            self.assertFalse(call.kwargs['allow_redirects'])
            self.assertEqual(call.kwargs['headers']['Range'], 'bytes=0-31')

    async def test_large_file_confirmation_preserves_id_and_resource_key(self):
        html = ('<form id="download-form" action="https://drive.usercontent.google.com/download">'
                '<input name="id" value="' + ID + '"><input name="confirm" value="token">'
                '<input name="uuid" value="nonce"></form>').encode()
        session = MagicMock()
        session.get.side_effect = [response(200, {'Content-Type': 'text/html'}, html), response(206)]
        await resolve_drive(session, LINK + '&resourcekey=key')
        query = parse_qs(urlsplit(session.get.call_args.args[0]).query)
        self.assertEqual(query['id'], [ID])
        self.assertEqual(query['confirm'], ['token'])
        self.assertEqual(query['uuid'], ['nonce'])
        self.assertEqual(query['resourcekey'], ['key'])

    async def test_confirmation_cannot_change_file_or_send_request_to_other_host(self):
        for action, identifier in [('https://evil.org/download', ID),
                                   ('http://127.0.0.1/download', ID),
                                   ('https://drive.usercontent.google.com/download', 'different_file_123')]:
            session = MagicMock()
            html = f'<form id="download-form" action="{action}"><input name="id" value="{identifier}"><input name="confirm" value="t"></form>'.encode()
            session.get.return_value = response(200, {'Content-Type': 'text/html'}, html)
            with self.assertRaises(SourceError):
                await resolve_drive(session, LINK)
            session.get.assert_called_once()

    async def test_private_quota_html_and_ignored_range_fail_clearly(self):
        for reply in (response(403), response(429), response(200, {'Content-Type': 'text/html'}, b'login'),
                      response(200, {'Content-Type': 'application/zip'}),
                      response(200, {'Content-Type': 'text/html'}, b'a' * (256 * 1024 + 1))):
            session = MagicMock()
            session.get.return_value = reply
            with self.assertRaisesRegex(SourceError, 'Google Drive'):
                await resolve_drive(session, LINK)

    async def test_login_redirect_is_rejected_and_not_followed(self):
        session = MagicMock()
        session.get.return_value = response(302, {'Location': 'https://accounts.google.com/login'})
        with self.assertRaises(SourceError):
            await resolve_drive(session, LINK)
        session.get.assert_called_once()

    async def test_remote_probes_resolved_file_and_retains_rom_name(self):
        session = MagicMock(close=AsyncMock())
        with patch('app.remote.aiohttp.TCPConnector'), patch('app.remote.aiohttp.ClientSession', return_value=session), \
                patch('app.remote.resolve_drive', AsyncMock(return_value=('https://download.googleusercontent.com/file', 'OTA_OS3.0.6.0.VLLCNXM.zip'))), \
                patch.object(Remote, 'read', new_callable=AsyncMock) as read:
            async with Remote(LINK) as remote:
                self.assertEqual(remote.url, 'https://download.googleusercontent.com/file')
                self.assertEqual(remote.rom_version, 'OS3.0.6.0.VLLCNXM')
                read.assert_awaited_once_with(0, 32)
        session.close.assert_awaited_once()
