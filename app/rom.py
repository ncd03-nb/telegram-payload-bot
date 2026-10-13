"""Identify ROM versions without exposing URL query credentials."""
import re
from urllib.parse import unquote, urlsplit


def named_version(text):
    patterns = (
        r'(?<![A-Za-z0-9])(?:OS|V)\d+\.\d+\.\d+\.\d+\.[A-Z0-9]{5,12}(?![A-Za-z0-9])',
        r'(?<![A-Za-z0-9])[A-Z]{2,6}\d{3,6}[_-]\d{1,2}\.\d+\.\d+(?:\.\d+)?(?:\([A-Z0-9]+\))?',
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return ''


def version_from_url(url):
    return named_version(unquote(urlsplit(url).path))


def version_from_metadata(raw):
    values = {}
    for line in raw.decode('utf-8', errors='replace').splitlines():
        key, separator, value = line.partition('=')
        if separator:
            values[key.strip()] = value.strip()
    incremental = values.get('post-build-incremental', '')
    # Use only the target build; pre-build describes the ROM being upgraded from.
    for value in (incremental, values.get('post-build', '')):
        version = named_version(value)
        if version:
            return version
    if re.fullmatch(r'[A-Za-z0-9_.()+-]{1,160}', incremental):
        return 'Build ' + incremental
    return ''
