"""让 `python -m lanshare_cli` 可用（安装器就是这么调的）。"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
