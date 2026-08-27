"""Tiny dependency-free HTTP health probe for container health checks."""

from __future__ import annotations

import sys
from urllib.error import URLError
from urllib.request import urlopen


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    try:
        with urlopen(sys.argv[1], timeout=2) as response:
            return 0 if 200 <= response.status < 300 else 1
    except URLError:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
