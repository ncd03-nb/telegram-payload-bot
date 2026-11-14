import unittest

from app.partition_layout import dump_layout, oplus_folder


class PartitionLayoutTests(unittest.TestCase):
    def test_screenshot_system_partition_names(self):
        for name in ('my_bigball', 'my_carrier', 'my_company', 'my_engineering',
                     'my_heytap', 'my_manifest', 'my_preload', 'my_product',
                     'my_region', 'my_stock', 'odm', 'product', 'system',
                     'system_dlkm', 'system_ext', 'vendor', 'vendor_dlkm'):
            with self.subTest(name=name):
                self.assertEqual(oplus_folder(name), 'SYSTEM')

    def test_screenshot_critical_partition_names(self):
        for name in ('abl', 'aop', 'aop_config', 'bluetooth', 'cpucp', 'cpucp_dtb',
                     'devcfg', 'dsp', 'engineering_cdt', 'featenabler', 'hyp',
                     'imagefv', 'keymaster', 'oplus_sec', 'oplusstanvbk', 'qupfw',
                     'shrm', 'splash', 'tz', 'uefi', 'uefisecapp', 'xbl',
                     'xbl_config', 'xbl_ramdump'):
            with self.subTest(name=name):
                self.assertEqual(oplus_folder(name), 'CRITICAL')

    def test_screenshot_bootloader_partition_names(self):
        for name in ('boot', 'dtbo', 'init_boot', 'recovery', 'vbmeta',
                     'vbmeta_system', 'vbmeta_vendor', 'vendor_boot'):
            with self.subTest(name=name):
                self.assertEqual(oplus_folder(name), 'BOOTLOADER')

    def test_slot_names_and_extra_partitions_are_preserved_and_classified(self):
        for name, expected in (('my_stock_a','SYSTEM'), ('system_ext_b','SYSTEM'),
                               ('vbmeta_vendor_b','BOOTLOADER'), ('boot_a','BOOTLOADER'),
                               ('vendor_kernel_boot','BOOTLOADER'), ('modem_a','CRITICAL'),
                               ('unknown_firmware','CRITICAL')):
            self.assertEqual(oplus_folder(name), expected)

    def test_extract_all_detection_works_for_drive_raw_payload_and_oplus_cdn(self):
        self.assertEqual(dump_layout(' ALL ', {'my_stock':{},'boot':{}}, '',
                                    'https://drive.google.com/file/d/example/view'), 'oplus')
        self.assertEqual(dump_layout('all', {'oplus_sec':{},'boot':{}}, '',
                                    'https://drive.google.com/file/d/example/view'), 'oplus')
        for version in ('PJF110_16.0.5.1001(CN01)', 'CPH2745_16.0.1.2(EX01)',
                        'RMX5010_16.0.1.2', 'OPD2401_16.0.1.2'):
            self.assertEqual(dump_layout('all', {'boot':{}}, version), 'oplus')
        self.assertEqual(dump_layout('all', {'boot':{}}, '',
                                    'https://component-ota-cn.allawntech.com/downloadCheck?id=1'), 'oplus')

    def test_selected_images_and_non_oplus_roms_keep_existing_layout(self):
        self.assertIsNone(dump_layout('boot,init_boot', {'my_stock':{},'boot':{}},
                                     'PJF110_16.0.5.1001(CN01)'))
        self.assertIsNone(dump_layout('all', {'boot':{},'system':{}}, 'OS4.0.0.10.XOMCNXM',
                                     'https://cdn.d.miui.com/rom.zip'))
        self.assertIsNone(dump_layout('all', {'boot':{}}, '',
                                     'https://allawntech.com.evil.org/rom.zip'))


if __name__ == '__main__':
    unittest.main()
