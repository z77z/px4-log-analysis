# -*- coding: utf-8 -*-
"""
smarttune-cli 本地入口 (stune 替代)
用法: python stune.py <command> [options]
示例: python stune.py quality -i flight.ulg
      python stune.py pid -i flight.ulg -a roll

依赖: pip install "pyulog>=1.0,<2.0" "numpy>=1.21" "scipy>=1.7" "matplotlib>=3.5" "click>=8.0" "rich>=13.0"
"""
import sys
import os

# 将本脚本所在目录加入 sys.path, 使 vendored smarttune 包可被导入
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from smarttune.cli import main

if __name__ == "__main__":
    main()
