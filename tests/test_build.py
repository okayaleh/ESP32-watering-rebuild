import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("build_tool", Path("tools/build.py"))
build_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_tool)

class BuildTests(unittest.TestCase):
    def test_credentials_scrubbed_without_executing_config(self):
        value = build_tool.scrub_config('WIFI_SSID="Private network"\nWIFI_PASSWORD="private password"\nBOARD="esp32"\n')
        self.assertNotIn("Private network", value)
        self.assertNotIn("private password", value)
        self.assertIn('BOARD="esp32"', value)
    def test_credentials_repeated_in_comments_abort_build(self):
        with self.assertRaises(ValueError):
            build_tool.scrub_config('WIFI_SSID="garden"\nWIFI_PASSWORD="secret123"\n# secret123\n')
    def test_nested_and_nonliteral_credentials_abort_build(self):
        for value in ('WIFI_SSID=""\nWIFI_PASSWORD=""\nif True:\n WIFI_PASSWORD="nested"\n',
                      'WIFI_SSID=""\nWIFI_PASSWORD=get_password()\n'):
            with self.assertRaises(ValueError): build_tool.scrub_config(value)

if __name__ == "__main__": unittest.main()
