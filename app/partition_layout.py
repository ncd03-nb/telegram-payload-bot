"""OPlus dump folder layout for an explicit extract-all request."""
import re
from urllib.parse import urlsplit


OPLUS_FOLDERS = ('SYSTEM', 'CRITICAL', 'BOOTLOADER')
SYSTEM_PARTITIONS = {
    'system', 'system_ext', 'system_dlkm', 'vendor', 'vendor_dlkm',
    'odm', 'odm_dlkm', 'product', 'super', 'userdata', 'metadata', 'cache',
}
BOOT_PARTITIONS = {
    'boot', 'init_boot', 'vendor_boot', 'vendor_kernel_boot', 'recovery', 'dtbo',
    'vbmeta',
}


def base_partition(name):
    """Classify A/B names without changing the exported filename."""
    return re.sub(r'_[ab]$', '', name.lower())


def dump_layout(requested, parts, rom_version='', source_url=''):
    if requested.strip().lower() != 'all':
        return None
    # OPlus partition names identify Drive links and raw payloads too.
    if any(base_partition(name).startswith(('my_', 'oplus')) for name in parts):
        return 'oplus'
    if re.search(r'(?:^|[^A-Za-z0-9])(?:CPH\d{4}|RMX\d{4}|OPD\d{4}|P[A-Z]{2}\d{3})[_-]\d', rom_version):
        return 'oplus'
    host = (urlsplit(source_url).hostname or '').lower()
    if host.endswith(('.allawnos.com', '.allawntech.com', '.coloros.com', '.oplusota.com')):
        return 'oplus'
    return None


def oplus_folder(name):
    partition = base_partition(name)
    if partition in SYSTEM_PARTITIONS or partition.startswith('my_'):
        return 'SYSTEM'
    if partition in BOOT_PARTITIONS or partition.startswith('vbmeta_'):
        return 'BOOTLOADER'
    # All other firmware images are retained; no partition is silently dropped.
    return 'CRITICAL'
