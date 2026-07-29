# -*- coding: utf-8 -*-
"""
报告合并脚本: 将报告正文 + 脚本输出文件合并为最终归档报告。
用法: python merge_report.py <report_body.md> <output.md> [--summary summary.txt] [--stune stune_output.txt] [--deep-dive deep_dive.txt]

附录自动拼入, 无需模型手动嵌入, 节省 token。
"""
import sys
import argparse
from pathlib import Path


def read_text(path):
    """读取文件, 不存在返回 None"""
    p = Path(path)
    if not p.is_file():
        return None
    return p.read_text(encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="合并报告正文与脚本输出为最终归档报告")
    parser.add_argument("body", help="报告正文文件 (report_body.md)")
    parser.add_argument("output", help="输出报告文件路径")
    parser.add_argument("--summary", default="summary.txt", help="摘要脚本输出 (默认 summary.txt)")
    parser.add_argument("--stune", default="stune_output.txt", help="smarttune 输出 (默认 stune_output.txt)")
    parser.add_argument("--deep-dive", default="deep_dive.txt", help="深挖脚本输出 (默认 deep_dive.txt)")
    args = parser.parse_args()

    report = read_text(args.body)
    if report is None:
        print(f"错误: 报告正文文件未找到: {args.body}", file=sys.stderr)
        sys.exit(1)

    report = report.rstrip() + "\n\n---\n\n## 附录: 脚本执行结果 (自动合并, 不删减)\n"

    # 附录 A: 摘要脚本
    summary = read_text(args.summary)
    if summary:
        report += f"\n### A. 摘要脚本输出 (`px4_log_summary.py`)\n\n```text\n{summary.rstrip()}\n```\n"
    else:
        report += f"\n### A. 摘要脚本输出 (`px4_log_summary.py`)\n\n(未找到 {args.summary})\n"

    # 附录 B: smarttune
    stune = read_text(args.stune)
    if stune:
        report += f"\n### B. smarttune-cli 分析输出\n\n```text\n{stune.rstrip()}\n```\n"
    else:
        report += f"\n### B. smarttune-cli 分析输出\n\n(未找到 {args.stune})\n"

    # 附录 C: 深挖脚本 (可选)
    deep_dive = read_text(args.deep_dive)
    if deep_dive:
        report += f"\n### C. 深挖脚本输出\n\n```text\n{deep_dive.rstrip()}\n```\n"

    # 确保输出目录存在
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(f"报告已保存: {out_path}")


if __name__ == "__main__":
    main()
