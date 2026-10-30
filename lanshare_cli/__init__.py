"""LAN Share 的安装与运维命令行。

对外只暴露 main()，安装器调用：python -m lanshare_cli install
"""

__all__ = ["main"]
__version__ = "1.0.0"


def main(argv: list[str] | None = None) -> int:
    from .cli import main as _main

    return _main(argv)
