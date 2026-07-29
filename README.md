# PX4 Log Analysis

> AI-powered PX4 flight log analysis & PID tuning skill — analyze `.ulg` logs for multirotor (MC) / fixed-wing (FW) / VTOL, diagnose vibration / tracking / EKF issues, generate tuning guides with historical trend comparison.

> AI 驱动的 PX4 飞行日志分析与 PID 调参技能 — 解析 `.ulg` 日志,支持多旋翼 (MC) / 固定翼 (FW) / VTOL 三种机型,诊断振动 / 控制跟踪 / EKF 问题,输出含历史对比的调参指南。

[![AI Skill](https://img.shields.io/badge/AI-Skill-8b5cf6?logo=openai&logoColor=white)](SKILL.md)
[![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![PX4](https://img.shields.io/badge/PX4-Autopilot-orange?logo=px4&logoColor=white)](https://px4.io/)
[![ULog](https://img.shields.io/badge/Format-.ulg-3776ab)](https://docs.px4.io/main/en/log_formats.html)
[![FFT](https://img.shields.io/badge/FFT-Notch%20Filter-ff6b35)](https://docs.px4.io/main/en/config_mc/filter_tuning.html)
[![PID](https://img.shields.io/badge/PID-Tuning-00d2d3)](https://docs.px4.io/main/en/config_mc/pid_tuning_guide_multicopter.html)
[![Airframe](https://img.shields.io/badge/Airframe-MC%2FFW%2FVTOL-9b59b6)](https://docs.px4.io/main/en/config_fw/)

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
| PID 阶跃响应评级 | 上升时间 / 超调 / 振荡 — 自动识别 MC/FW 机型切换阈值 | `smarttune pid` |
| FFT 振动频谱 | 频谱分析 + 陷波滤波器建议 | `smarttune fft` |
| 滤波器传递函数 | 低通 + 陷波链 Bode 图 / -3dB 截止频率 | `smarttune filter` |
| 系统辨识 | 自然频率 / 阻尼比 / 带宽 / P 增益建议 | `smarttune sysid` |
| 硬件配置报告 | 飞控 / IMU / 罗盘 / 滤波器 / PID 参数概要 | `smarttune hardware` |
| 结构化摘要 | 机型判别 / 振动 / 跟踪 / 电源 / GPS / EKF / 空速 / 参数快照 | `px4_log_summary.py` |
| 历史纵向对比 | 同机型跨次飞行指标趋势对比 | `flight_reports/` 归档 |

### 机型支持

| 机型 | 参数体系 | 说明 |
|------|----------|------|
| 多旋翼 (MC) | `MC_*RATE_*` | P 主项 (~0.15),D ~0.003,FF 可选;`MC_*RATE_K` 总增益乘子 |
| 固定翼 (FW) | `FW_RR_*/FW_PR_*/FW_YR_*` | FF 主项 (~0.4),P 次项 (~0.06),D 通常为 0;需考虑空速 |
| VTOL | MC + FW 动态切换 | MC 阶段用 `MC_*` 参数,FW 阶段用 `FW_*` 参数;自动追踪 `vtol_vehicle_status` 模式切换 |

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

> **机型自适应阈值** — 上表为多旋翼 (MC) 阈值 (`pid_rules.json`)。固定翼 (FW) 使用 `pid_rules_fw.json` 中独立的阈值体系:上升时间 ideal [200, 500]ms、超调 ideal [0, 20]%、settling_time ideal [500, 1500]ms。分析器根据 `AIRFRAME_TYPE` 参数自动选择对应规则集。

## 工具链

| 脚本 | 作用 | 依赖 |
|------|------|------|
| `scripts/px4_log_summary.py` | 11 节结构化摘要提取 | pyulog, numpy |
| `scripts/run_smarttune.py` | 批量执行 quality/pid/fft/filter/sysid/hardware | smarttune |
| `scripts/merge_report.py` | 报告正文 + 附录合并 | — |
| `scripts/stune.py` | SmartTune CLI 启动器 | click, rich, scipy, matplotlib |

### 也可以独立使用

这些脚本不依赖 AI,也可以作为普通命令行工具直接运行:

```bash
# 安装依赖
pip install -r requirements.txt

# 提取摘要
python scripts/px4_log_summary.py flight.ulg summary.txt

# 自动分析(PID + FFT + 质量 + SysID + Filter + Hardware)
python scripts/run_smarttune.py scripts flight.ulg

# 单项分析
python scripts/stune.py pid -i flight.ulg --visual          # PID 阶跃响应
python scripts/stune.py fft -i flight.ulg --visual          # FFT 振动频谱
python scripts/stune.py filter -i flight.ulg --visual       # 滤波器波特图
python scripts/stune.py sysid -i flight.ulg                 # ARX 系统辨识
python scripts/stune.py quality -i flight.ulg               # 日志质量评分
python scripts/stune.py hardware -i flight.ulg              # 硬件配置报告
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
    └── smarttune/                     # 内置分析引擎 v3.0.3 (MIT, PX4 专用)
        ├── cli.py                     # 命令入口 (pid/fft/filter/sysid/quality/hardware)
        ├── errors.py                  # 自定义异常体系
        ├── analyzers/                 # 平台无关分析器
        │   ├── pid_reviewer.py        #   PID 阶跃响应 (自动 MC/FW 阈值切换)
        │   ├── fft_analyzer.py        #   FFT 振动频谱
        │   ├── sysid_analyzer.py      #   ARX 系统辨识
        │   ├── arx_model.py           #   ARX 模型核心算法
        │   ├── step_response_fft.py   #   Wiener 反卷积阶跃响应 (平台无关)
        │   └── step_response_time_domain.py  # 时域阶跃回退
        ├── knowledge/                 # 分层知识库
        │   ├── loader.py              #   common → px4 → user → pro 四层叠加
        │   └── rules/
        │       ├── common/vibration_rules.json  # 跨平台振动阈值
        │       └── px4/
        │           ├── pid_rules.json       # MC 多旋翼 PID 规则
        │           ├── pid_rules_fw.json    # FW 固定翼 PID 规则
        │           └── filter_rules.json    # 滤波器参数定义
        ├── models/                    # 数据模型
        │   ├── flight_data.py         #   统一 FlightData (含 frame_type / mode_changes)
        │   └── analysis_result.py     #   分析结果类型 (含 SysIDResult 唯一定义)
        ├── platform/                  # 平台适配器 (仅 PX4)
        │   ├── base.py                #   PlatformAdapter 抽象基类
        │   ├── registry.py            #   注册表与自动发现
        │   └── px4/
        │       ├── __init__.py        #   PX4 ULog 适配器 (MC/FW/VTOL 机型检测)
        │       ├── filter_transfer.py #   滤波器传递函数 (Bode 图)
        │       ├── hardware_report.py #   硬件配置报告
        │       └── step_response_fft.py  # PX4 阶跃响应分派
        └── services/                  # 分析编排
            ├── analysis.py            #   run_module 统一装配
            └── serialize.py           #   JSON 序列化
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
- [多旋翼 PID 调参指南](https://docs.px4.io/main/en/config_mc/pid_tuning_guide_multicopter.html) — MC `MC_*RATE_*` 参数体系
- [固定翼 PID 调参指南](https://docs.px4.io/main/en/config_fw/) — FW `FW_RR_*/FW_PR_*/FW_YR_*` 参数体系
- [滤波器调参指南](https://docs.px4.io/main/en/config_mc/filter_tuning.html) — `IMU_GYRO_CUTOFF` / 陷波滤波器配置
