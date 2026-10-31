#!/usr/bin/env python3
"""Apply only the reviewed EasyTier root selection before defconfig."""
from pathlib import Path
import re
import sys

PACKAGES = ("easytier", "luci-app-easytier", "luci-i18n-easytier-zh-cn")


def transform(text):
    for name in PACKAGES:
        symbol = "CONFIG_PACKAGE_" + name
        pattern = r"(?m)^(?:" + re.escape(symbol) + r"=.*|# " + re.escape(symbol) + r" is not set)$"
        text, count = re.subn(pattern, "# " + symbol + " is not set", text)
        if count == 0 and name == "luci-i18n-easytier-zh-cn":
            # LuCI can select translations implicitly even without a seed row.
            text = text.rstrip("\n") + "\n# " + symbol + " is not set\n"
        elif count != 1:
            raise ValueError("expected one existing package root: " + name)
    return text


if __name__ == "__main__":
    path = Path(sys.argv[1])
    path.write_text(transform(path.read_text()))
