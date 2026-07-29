# -*- coding: utf-8 -*-
"""
smarttune-cli 批量运行脚本: 依次执行 quality/pid/fft/sysid, 输出合并到 stune_output.txt。
用法: python run_smarttune.py <skill_scripts_dir> <flight.ulg> [输出文件]

smarttune 用 rich 输出到 stderr, 本脚本捕获 stderr 并合并到输出文件。
"""
import sys
import os
import subprocess
from pathlib import Path


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    scripts_dir = sys.argv[1]       # skill 的 scripts 目录
    ulg_file = sys.argv[2]          # 日志文件路径
    output = sys.argv[3] if len(sys.argv) > 3 else "stune_output.txt"

    stune = os.path.join(scripts_dir, "stune.py")
    if not os.path.isfile(stune):
        print(f"错误: 未找到 stune.py: {stune}", file=sys.stderr)
        sys.exit(1)

    if not os.path.isfile(ulg_file):
        print(f"错误: 日志文件未找到: {ulg_file}", file=sys.stderr)
        sys.exit(1)

    # 依次运行的子命令: (标题, 参数列表)
    commands = [
        ("quality",        ["quality", "-i", ulg_file]),
        ("pid roll",       ["pid", "-i", ulg_file, "-a", "roll"]),
        ("pid pitch",      ["pid", "-i", ulg_file, "-a", "pitch"]),
        ("pid yaw",        ["pid", "-i", ulg_file, "-a", "yaw"]),
        ("fft",            ["fft", "-i", ulg_file]),
        ("sysid roll",     ["sysid", "-i", ulg_file, "-a", "roll"]),
        ("sysid pitch",    ["sysid", "-i", ulg_file, "-a", "pitch"]),
    ]

    lines = []
    for title, args in commands:
        print(f"运行: stune {title} ...", file=sys.stderr)
        lines.append(f"=== {title} ===")
        try:
            result = subprocess.run(
                [sys.executable, stune] + args,
                capture_output=True, text=True, timeout=300,
            )
            # rich 输出到 stderr, 正常信息也在 stderr
            output_text = result.stderr.strip() if result.stderr else ""
            if result.stdout.strip():
                output_text = (output_text + "\n" + result.stdout.strip()).strip()
            if result.returncode != 0 and not output_text:
                output_text = f"(命令失败, 退出码 {result.returncode})"
            lines.append(output_text)
        except subprocess.TimeoutExpired:
            lines.append(f"(超时, 跳过)")
        except Exception as ex:
            lines.append(f"(执行失败: {ex})")
        lines.append("")  # 空行分隔

    content = "\n".join(lines)
    Path(output).write_text(content, encoding="utf-8")
    print(f"smarttune 输出已保存: {output}", file=sys.stderr)


if __name__ == "__main__":
    main()
