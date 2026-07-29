# PX4 Log Analysis

> AI-powered PX4 flight log analysis & PID tuning skill — analyze `.ulg` logs, diagnose vibration / tracking / EKF issues, generate tuning guides with historical trend comparison.

> AI 驱动的 PX4 飞行日志分析与 PID 调参技能 — 解析 `.ulg` 日志,诊断振动 / 控制跟踪 / EKF 问题,输出含历史对比的调参指南。

[![AI Skill](https://img.shields.io/badge/AI-Skill-8b5cf6?logo=openai&logoColor=white)](SKILL.md)
[![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![PX4](https://img.shields.io/badge/PX4-Autopilot-orange?logo=px4&logoColor=white)](https://px4.io/)
[![ULog](https://img.shields.io/badge/Format-.ulg-3776ab)](https://docs.px4.io/main/en/log_formats.html)
[![FFT](https://img.shields.io/badge/FFT-Notch%20Filter-ff6b35)](https://docs.px4.io/main/en/config_mc/filter_tuning.html)
[![PID](https://img.shields.io/badge/PID-Tuning-00d2d3)](https://docs.px4.io/main/en/config_mc/pid_tuning_guide_multicopter.html)

---

## 这是什么

`px4-log-analysis` 是一个 **AI Skill**(大模型技能包)。它赋予 AI 助手分析 PX4 飞行日志的能力 —— 当用户丢来一个 `.ulg` 日志文件或 `logs.px4.io` 链接时,AI 会自动执行完整的诊断流程:提取飞行指标、评估控制跟踪、定位振动问题、给出参数修改建议,最终生成一份结构化的归档报告。

与传统命令行工具不同,这个 Skill 的核心是 [`SKILL.md`](SKILL.md) —— 一份写给 AI 的指令手册,定义了 7 步分析流程、判读阈值知识库和报告模板。AI 按此手册编排 `scripts/` 下的工具链,完成从原始日志到可操作建议的端到端转换。

### 工作原理

```
用户: "帮我分析这个飞行日志 flight.ulg"
                ↓
   AI 读取 SKILL.md → 按流程编排工具链
                ↓
   ┌─────────────────────────────────────────┐
   │  1. px4_log_summary.py  → 11 节摘要     │
   │  2. run_smarttune.py    → 自动深度分析   │
   │  3. 针对性深挖           → 异常定位      │
   │  4. merge_report.py     → 归档报告       │
   └─────────────────────────────────────────┘
                ↓
   AI 输出: 飞行诊断 + 问题清单 + 调参指南
```

## 能力概览

| 能力 | 说明 | 工具 |
|------|------|------|
| 日志质量评分 | 完整性 / 激励 / 采样率 | `smarttune quality` |
| PID 阶跃响应评级 | 上升时间 / 超调 / 振荡 | `smarttune pid` |
| FFT 振动频谱 | 频谱分析 + 陷波滤波器建议 | `smarttune fft` |
| 系统辨识 | 自然频率 / 阻尼比 / 带宽 / P 增益建议 | `smarttune sysid` |
| 结构化摘要 | 机型判别 / 振动 / 跟踪 / 电源 / GPS / EKF / 空速 / 参数快照 | `px4_log_summary.py` |
| 历史纵向对比 | 同机型跨次飞行指标趋势对比 | `flight_reports/` 归档 |

## SKILL.md — 写给 AI 的指令手册

这是整个 Skill 的核心。AI 助手加载它后,获得以下能力:

- **7 步标准化流程** — 从获取日志到归档清理,确保每次分析可复现
- **判读知识库** — 振动 / PID / 滤波 / EKF / 电源等量化阈值(部分源自内置 JSON 规则)
- **调参指南原则** — 先机械后软件、按环分层、步进安全限制
- **报告模板** — 元信息头 + 五节正文 + 自动附录

### 判读阈值示例

| 指标 | 理想 | 可接受 | 边缘 | 差 |
|------|------|--------|------|----|
| 加速度去趋势 std | <1 m/s² | 1-3 m/s² | >3 m/s² | — |
| 上升时间 (roll/pitch) | 60-110 ms | 40-160 ms | >160 ms | — |
| 超调量 | 0-10% | 0-15% | 15-25% | >25% |
| 悬停油门(归一化) | <0.5 | 0.5-0.7 | >0.7 | — |
| 日志质量评分 | ≥90 EXCELLENT | ≥75 GOOD | <55 POOR | — |

阈值源自 `scripts/smarttune/knowledge/rules/` 下的 JSON 规则文件,SmartTune 运行时自动加载生成评级。

## 工具链

| 脚本 | 作用 | 依赖 |
|------|------|------|
| `scripts/px4_log_summary.py` | 11 节结构化摘要提取 | pyulog, numpy |
| `scripts/run_smarttune.py` | 批量执行 quality/pid/fft/sysid | smarttune |
| `scripts/merge_report.py` | 报告正文 + 附录合并 | — |
| `scripts/stune.py` | SmartTune CLI 启动器 | click, rich, scipy, matplotlib |

### 也可以独立使用

这些脚本不依赖 AI,也可以作为普通命令行工具直接运行:

```bash
# 安装依赖
pip install -r requirements.txt

# 提取摘要
python scripts/px4_log_summary.py flight.ulg summary.txt

# 自动分析(PID + FFT + 质量 + SysID)
python scripts/run_smarttune.py scripts flight.ulg

# 单项分析
python scripts/stune.py pid -i flight.ulg --visual
python scripts/stune.py fft -i flight.ulg --visual
python scripts/stune.py quality -i flight.ulg
```

## 项目结构

```
px4-log-analysis/
├── SKILL.md                          # AI 指令手册(流程 + 知识库 + 模板)
├── requirements.txt                  # Python 依赖
├── LICENSE                            # MIT
└── scripts/
    ├── px4_log_summary.py             # 摘要提取
    ├── run_smarttune.py               # 批量分析
    ├── merge_report.py                # 报告合并
    ├── stune.py                       # CLI 启动器
    └── smarttune/                     # 内置分析引擎 v3.0.3 (MIT)
        ├── cli.py                     # 命令入口
        ├── analyzers/                 # PID / FFT / SysID 分析器
        ├── knowledge/rules/           # JSON 规则知识库
        │   ├── px4/pid_rules.json
        │   ├── px4/filter_rules.json
        │   └── common/vibration_rules.json
        ├── models/                    # 数据模型
        ├── output/                    # 终端 / Markdown / HTML 输出
        ├── platform/                  # 平台适配器 (PX4 / ArduPilot)
        └── services/                  # 分析编排
```

## 依赖

| 包 | 用途 |
|----|------|
| `pyulog` | PX4 ULog 二进制日志解析 |
| `numpy` | 数值计算 |
| `scipy` | 信号处理 (FFT / 系统辨识) |
| `matplotlib` | 图表可视化 |
| `click` | CLI 框架 |
| `rich` | 终端美化输出 |

## 许可证

[MIT License](LICENSE) — SmartTune 引擎版权所有 © 2026 Raylan LIN。

## 参考

- [PX4 官方文档](https://docs.px4.io/main/en/) — Flight Review 图表判读 / PID 调参 / 滤波调参
- [PX4 Flight Review](https://logs.px4.io/) — 在线日志分析平台
