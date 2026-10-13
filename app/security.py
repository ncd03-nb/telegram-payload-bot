import asyncio
import ipaddress
import json
import re
import socket
from contextlib import asynccontextmanager
from email.message import Message
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit, unquote

import aiohttp


class SourceError(Exception):
    pass


def public_ip(value):
    ip = ipaddress.ip_address(value)
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def validate_url(url):
    try:
        u = urlsplit(url)
        if u.scheme not in {"http", "https"} or not u.hostname or u.username or u.password or u.port not in {None, 80, 443}:
            raise ValueError()
        host = u.hostname.rstrip(".").lower()
        if host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
            raise ValueError()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            if not public_ip(host):
                raise ValueError()
        if len(url) > 8192:
            raise ValueError()
    except ValueError:
        raise SourceError("Chỉ nhận URL HTTP(S) công khai, không nhận IP nội bộ / cổng tùy chỉnh.") from None
    return url


class PublicResolver(aiohttp.abc.AbstractResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        info = await asyncio.get_running_loop().getaddrinfo(host, port, family=family, type=socket.SOCK_STREAM)
        if not info or any(not public_ip(row[4][0]) for row in info):
            raise OSError("Địa chỉ nguồn không phải Internet công khai.")
        return [{"hostname": host, "host": row[4][0], "port": port, "family": row[0], "proto": row[2], "flags": socket.AI_NUMERICHOST} for row in info]

    async def close(self):
        pass


