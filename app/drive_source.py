"""Resolve public Drive sharing pages without using the bot owner's OAuth."""
import re
from email.message import Message
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from .security import SourceError, validate_url


def drive_file(url):
    parsed = urlsplit(url)
    if parsed.hostname not in {'drive.google.com', 'www.drive.google.com', 'drive.usercontent.google.com'}:
        return None
    match = re.fullmatch(r'/file/d/([A-Za-z0-9_-]{10,200})(?:/[^/]*)?/?', parsed.path)
    query = parse_qs(parsed.query)
    identifier = match[1] if match else query.get('id', [''])[0] if parsed.path.rstrip('/') in {'/uc', '/open', '/download'} else ''
    if not re.fullmatch(r'[A-Za-z0-9_-]{10,200}', identifier):
        raise SourceError('Cần link Google Drive của một file ZIP OTA hoặc payload.bin, không phải thư mục.')
    resource = query.get('resourcekey', [''])[0]
    if resource and not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', resource):
        raise SourceError('Resource key Google Drive không hợp lệ.')
    return identifier, resource


class Confirmation(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action, self.fields, self.inside = '', {}, False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form':
            self.inside = attrs.get('id') == 'download-form'
            if self.inside:
                self.action = attrs.get('action', '')
        if tag == 'input' and self.inside and attrs.get('name') in {'id', 'export', 'confirm', 'uuid', 'resourcekey'}:
            self.fields[attrs['name']] = attrs.get('value', '')

    def handle_endtag(self, tag):
        if tag == 'form':
            self.inside = False


async def resolve_drive(session, url):
    identifier, resource = drive_file(url)
    fields = {'id': identifier, 'export': 'download', 'confirm': 't'}
    if resource:
        fields['resourcekey'] = resource
    current = 'https://drive.usercontent.google.com/download?' + urlencode(fields)
    confirmations = 0
    for _ in range(10):
        validate_url(current)
        host = (urlsplit(current).hostname or '').lower()
        if host not in {'drive.google.com', 'www.drive.google.com', 'drive.usercontent.google.com'} and not host.endswith('.googleusercontent.com'):
            raise SourceError('Google Drive chuyển tới trang đăng nhập hoặc địa chỉ tải không được hỗ trợ. Cần bật “Bất kỳ ai có đường liên kết”.')
        async with session.get(current, headers={'Range': 'bytes=0-31', 'Accept-Encoding': 'identity',
                'User-Agent': 'PayloadPartitionBot/1.0'}, allow_redirects=False) as response:
            if response.status in {301, 302, 303, 307, 308}:
                current = urljoin(current, response.headers.get('Location', ''))
                continue
            if response.status in {401, 403, 404, 429}:
                raise SourceError(f'Google Drive HTTP {response.status}: kiểm tra quyền tải, chia sẻ công khai hoặc quota tải xuống.')
            if response.status not in {200, 206}:
                raise SourceError(f'Google Drive trả HTTP {response.status}; thử lại khi link tải hoạt động.')
            if 'text/html' in response.headers.get('Content-Type', '').lower():
                html = bytearray()
                async for block in response.content.iter_chunked(8192):
                    html.extend(block)
                    if len(html) > 256 * 1024:
                        raise SourceError('Trang xác nhận Google Drive vượt giới hạn; cần link tải công khai.')
                form = Confirmation()
                form.feed(html.decode('utf-8', 'replace'))
                action = urljoin(current, form.action)
                p = urlsplit(action)
                if (confirmations >= 2 or not form.action or p.scheme != 'https'
                        or p.hostname != 'drive.usercontent.google.com' or p.path != '/download'
                        or form.fields.get('id') != identifier or not form.fields.get('confirm')):
                    raise SourceError('Google Drive yêu cầu đăng nhập, vượt quota hoặc tắt quyền tải. Cần file ZIP/payload được chia sẻ công khai.')
                validate_url(action)
                confirmations += 1
                fields = {**form.fields, 'id': identifier, 'export': 'download'}
                if resource:
                    fields['resourcekey'] = resource
                current = 'https://drive.usercontent.google.com/download?' + urlencode(fields)
                continue
            if response.status != 206:
                raise SourceError('Google Drive không hỗ trợ tải từng phần cho file này (HTTP 206). Kiểm tra quota hoặc dùng link CDN gốc.')
            # The Remote reader independently validates Content-Range/ETag and bytes.
            header = Message()
            header['Content-Disposition'] = response.headers.get('Content-Disposition', '')
            return current, header.get_filename() or ''
    raise SourceError('Google Drive chuyển hướng quá nhiều lần.')
