"""Every setting the laptop code reads (${BOX_X:-...} or os.environ.get("BOX_X")) is named in
lib.sh before its first function, where the README and the skills send people to find them."""
import glob
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INTERNAL = {"BOX_CONF", "BOX_POOL", "BOX_CLAUDE_SYNC"}   # paths, derived values, an old name


class SettingsAreListed(unittest.TestCase):
    def test_every_setting_is_in_lib_sh(self):
        with open(os.path.join(ROOT, "lib.sh")) as fh:
            head = re.split(r"^[a-z_]+\(\) *\{", fh.read(), maxsplit=1, flags=re.M)[0]
        files = ["box", "lib.sh", "setup.sh", "up.sh", "down.sh", "status.sh", "tabs.sh", "install-local.sh"]
        files += glob.glob(os.path.join(ROOT, "*.py"))
        used = set()
        for f in files:
            with open(os.path.join(ROOT, f)) as fh:
                text = fh.read()
            used |= set(re.findall(r"\$\{(BOX_[A-Z0-9_]+):-", text))
            used |= set(re.findall(r"environ\.get\(\"(BOX_[A-Z0-9_]+)\"", text))
        missing = sorted(v for v in used - INTERNAL if not re.search(rf"\b{v}\b", head))
        self.assertEqual(missing, [], "add these to the settings at the top of lib.sh")


if __name__ == "__main__":
    unittest.main()
