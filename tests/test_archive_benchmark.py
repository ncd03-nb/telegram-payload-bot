import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('archive_benchmark',Path(__file__).resolve().parents[1]/'scripts/benchmark_archive.py')
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class ArchiveDestinationTests(unittest.TestCase):
    def test_redirects_cannot_send_archive_keys_to_another_service(self):
        for url in ['http://s3.us.archive.org/test','https://archive.org.attacker.invalid/test',
                    'https://attacker.invalid/test','https://archive.org@attacker.invalid/test',
                    'https://user:password@archive.org/test','https://archive.org:8443/test']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                benchmark.archive_url(url)

    def test_official_https_archive_hosts_are_allowed(self):
        for url in ['https://s3.us.archive.org/test','https://ia800123.us.archive.org/test','https://archive.org/download/test']:
            self.assertEqual(benchmark.archive_url(url),url)
