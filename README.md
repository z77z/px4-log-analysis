# PX4 Log Analysis

> PX4 飞行日志分析与调参顾问 — 从 `.ulg` 日志到可操作的调参建议,一站式完成。

[![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![PX4](https://img.shields.io/badge/PX4-Autopilot-orange?logo=px4&logoColor=white)](https://px4.io/)

---

## 简介

`px4-log-analysis` 是一个面向 PX4 自驾仪的端到端飞行日志诊断与调参工具。它接受本地 `.ulg` 文件或 `logs.px4.io` 在线日志链接,从原始日志中提取飞行状态、控制跟踪、振动、电源、EKF 估计器等关键指标,结合内置判读知识库给出可操作的调参建议,并生成按「日期 + 机型 + 机架」命名的归档报告,支持跨次飞行的纵向对比。

核心由两部分协同:

- **摘要脚本** `px4_log_summary.py` — 一次性抽取 11 个结构化小节(平台信息 / 振动 / 角速率跟踪 / 执行器饱和 / 电源 / GPS / EKF / 飞行剖面等)
- **SmartTune CLI** `smarttune/` (v3.0.3, MIT) — 自动化深度分析:日志质量评分、PID 阶跃响应评级、FFT 振动频谱与陷波建议、SysID 系统辨识

## 功能特性

| 能力 | 说明 | PX4 |
|------|------|:---:|
| `quality` | 日志质量评分(完整性 / 激励 / 采样率) | ✅ |
| `pid` | PID 阶跃响应评级(上升时间 / 超调 / 振荡) | ✅ |
| `fft` | 振动频谱分析 + 陷波滤波器建议 | ✅ |
| `sysid` | ARX 系统辨识(自然频率 / 阻尼 / 带宽) | ✅ |
| 摘要提取 | 机型判别 / 模式时间线 / 电源 / 空速 / 参数快照 | ✅ |
| 历史对比 | 同机型跨次飞行指标趋势对比 | ✅ |

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 基本用法

```bash
# 1. 提取日志摘要(11 节结构化输出)
python scripts/px4_log_summary.py flight.ulg summary.txt

# 2. 自动运行 SmartTune 全部分析
python scripts/run_smarttune.py scripts flight.ulg

# 3. 合并报告正文与附录
python scripts/merge_report.py report_body.md "flight_reports/20260729_multicopter_xxx.md"
```

### 使用 SmartTune CLI 单项分析

```bash
# PID 阶跃响应(带图表)
python scripts/stune.py pid -i flight.ulg --visual

# FFT 振动频谱
python scripts/stune.py fft -i flight.ulg --visual

# 日志质量评分
python scripts/stune.py quality -i flight.ulg

# 系统辨识
python scripts/stune.py sysid -i flight.ulg -a roll
```

## 分析工作流

```
.ulg 日志 → 摘要脚本 → SmartTune 自动分析 → 针对性深挖 → 归档报告
                ↓              ↓                    ↓            ↓
          summary.txt    stune_output.txt     deep_dive.txt   flight_reports/
```

1. **获取日志** — 本地 `.ulg` 文件或从 `logs.px4.io` 下载
2. **摘要提取** — `px4_log_summary.py` 输出 11 节结构化摘要
3. **自动分析** — `run_smarttune.py` 批量执行 quality / pid / fft / sysid
4. **针对性深挖** — 按异常指标写小脚本深入分析
5. **归档报告** — 按 `YYYYMMDD_机型_机架_序号.md` 命名,支持历史对比

## 项目结构

```
px4-log-analysis/
├── SKILL.md                 # 分析流程 + 判读知识库 + 报告模板
├── requirements.txt         # Python 依赖
├── LICENSE                   # MIT
├── .gitignore
└── scripts/
    ├── px4_log_summary.py    # 摘要提取脚本
    ├── run_smarttune.py      # 批量运行脚本
    ├── merge_report.py       # 报告合并脚本
    ├── stune.py              # SmartTune CLI 启动器
    └── smarttune/            # 内置分析引擎 (v3.0.3)
        ├── cli.py            # 命令入口
        ├── analyzers/        # PID / FFT / SysID 分析器
        ├── knowledge/        # JSON 规则知识库
        ├── models/           # 数据模型
        ├── output/           # 输出格式化
        ├── platform/         # 平台适配器 (PX4 / ArduPilot)
        └── services/         # 分析编排
```

## 判读知识库

内置 JSON 规则文件,SmartTune 运行时自动加载生成评级:

- `knowledge/rules/px4/pid_rules.json` — PID 阶跃响应阈值
- `knowledge/rules/px4/filter_rules.json` — 滤波器参数规则
- `knowledge/rules/common/vibration_rules.json` — 振动分级标准

### PID 阶跃响应阈值摘要

| 指标 | 理想 | 可接受 | 边缘 | 差 |
|------|------|--------|------|----|
| 上升时间 (roll/pitch) | 60-110 ms | 40-160 ms | >160 ms | — |
| 超调量 | 0-10% | 0-15% | 15-25% | >25% |
| 稳定时间 | 150-350 ms | 100-600 ms | 600-1000 ms | >1000 ms |

## 依赖

| 包 | 用途 |
|----|------|
| `pyulog` | PX4 ULog 解析 |
| `numpy` | 数值计算 |
| `scipy` | 信号处理 (FFT / 系统辨识) |
| `matplotlib` | 图表可视化 |
| `click` | CLI 框架 |
| `rich` | 终端美化输出 |

## 许可证

[MIT License](LICENSE)

SmartTune 引擎版权所有 © 2026 Raylan LIN,基于 MIT 许可证开源。

## 参考

- [PX4 官方文档](https://docs.px4.io/main/en/) — Flight Review 图表判读 / PID 调参 / 滤波调参
- [PX4 Flight Review](https://logs.px4.io/) — 在线日志分析平台
