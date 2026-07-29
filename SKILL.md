---
name: "px4-log-analysis"
description: "分析 PX4 ULog 飞行日志(.ulg 文件或 logs.px4.io 链接), 输出飞行情况诊断报告和参数修改/调参指南, 并将报告与脚本结果按「日期+机架类型」命名存档, 支持与历史飞行报告纵向对比。当用户提供 .ulg 日志文件、PX4 Flight Review (logs.px4.io) 日志链接, 或要求分析飞行日志/排查飞行问题/根据日志调参时使用。"
---

# PX4 飞行日志分析与调参指南

当用户提供 **本地 .ulg 文件** 或 **logs.px4.io 日志链接** 时, 按本流程执行完整分析, 输出「飞行情况诊断 + 参数修改指南」。

## 分析流程

### 第 1 步: 获取日志文件

- **本地文件**: 直接使用用户提供的路径。
- **logs.px4.io 链接**: 从链接中提取 log id, 下载原始 ulg:
  - 链接形如 `https://logs.px4.io/plot_app?log=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`
  - 下载地址: `https://logs.px4.io/download?log=<log_id>`
  - 用 PowerShell 下载:
    ```powershell
    Invoke-WebRequest -Uri "https://logs.px4.io/download?log=<log_id>" -OutFile "flight.ulg"
    ```

### 第 2 步: 准备解析环境

确认依赖已安装 (只需一次):

```powershell
pip install "pyulog>=1.0,<2.0" "numpy>=1.21" "scipy>=1.7" "matplotlib>=3.5" "click>=8.0" "rich>=13.0"
```

> smarttune-cli 已内置在本 skill 的 `scripts/smarttune/` 目录中, 无需联网下载。它提供 PX4 日志的日志质量评分、PID 阶跃响应分析、FFT 振动频谱分析、SysID 系统辨识, 作为摘要脚本的自动化补充。PX4 平台支持 `quality`/`pid`/`fft`/`sysid`, 不支持 `filter`/`hardware`/`magfit`。
>
> 若依赖安装失败 (如无 scipy/matplotlib), smarttune-cli 将不可用, 此时仅依靠摘要脚本 `px4_log_summary.py` + 第 5 步手动深挖即可完成基本分析, 但会缺少 PID 阶跃响应评级、FFT 陷波建议和 SysID 辨识。

### 第 3 步: 运行摘要脚本

使用本 skill 自带的脚本一次性提取全部关键信息 (代替每次手写临时脚本):

```powershell
python <skill目录>/scripts/px4_log_summary.py flight.ulg summary.txt
```

> `<skill目录>` 为本 skill 的安装路径,通常为 `~/.trae-cn/skills/px4-log-analysis` (Trae CN) 或 `~/.trae/skills/px4-log-analysis` (Trae),以实际环境为准。

输出 11 个结构化小节: 平台信息(含机型判别) / 日志消息 / 模式时间线 / 振动 / 角速率与姿态跟踪 / 执行器饱和 / 电池与电源(按机型输出功率统计) / GPS / EKF 创新量 / 飞行剖面(含固定翼空速专项) / 参数快照。详见 `summary.txt` 输出。

### 第 4 步: 运行 smarttune-cli 自动分析

摘要脚本完成后, 用 smarttune-cli (已内置) 对同一日志做自动化分析, 获取摘要脚本不覆盖的指标。批量运行脚本自动执行 quality/pid(三轴)/fft/sysid(两轴) 并合并输出到 `stune_output.txt`:

```powershell
python <skill目录>/scripts/run_smarttune.py <skill目录>/scripts flight.ulg
```

> 脚本自动捕获各子命令输出 (smarttune 用 rich 输出到 stderr), 按子命令分节写入 `stune_output.txt`。运行后读取该文件用于判读分析, 报告归档时由 `merge_report.py` 自动合并到附录, 无需模型复述输出。

> 读取 `stune_output.txt` 后, 结合下方「判读知识库」的阈值进行解读。各子命令输出含义: quality=日志质量评分; pid=PID 阶跃响应评级; fft=振动频谱+陷波建议; sysid=系统辨识(自然频率/阻尼比/带宽/P增益建议)。
>
> 各子命令可加 `--visual` 生成 matplotlib 图表 (PID 阶跃响应曲线、FFT 频谱图等), 加 `--theme dark` 切换深色主题。

### 第 5 步: 针对性深挖 (按需)

摘要发现异常后, 再写小脚本深挖对应话题。常用数据集与字段:

| 目的           | 数据集                                                        | 关键字段                                                                                      |
| -------------- | ------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| 角速率跟踪     | `vehicle_rates_setpoint` vs `vehicle_angular_velocity`        | roll/pitch/yaw vs xyz[0..2]                                                                   |
| 姿态跟踪       | `vehicle_attitude_setpoint` vs `vehicle_attitude`             | roll/pitch/yaw_body vs q(四元数)                                                              |
| 振动频谱       | `sensor_gyro` / `sensor_accel` (高采样率)                     | gyro_rad[0..2], 需 SDLOG_PROFILE 开高采样                                                     |
| 电机输出       | `actuator_outputs` / `actuator_motors`                        | output[i] / control[i]                                                                        |
| 固定翼姿态控制 | `fw_att_control_status` (v1.16+), `tecs_status`               | 各增益、TECS 高度/速度误差                                                                    |
| 固定翼空速     | `airspeed_validated` / `airspeed`, `tecs_status`              | calibrated/indicated_airspeed_m_s, equivalent_airspeed_sp, true_airspeed_sp, underspeed_ratio |
| 多旋翼位置控制 | `vehicle_local_position_setpoint` vs `vehicle_local_position` | x/y/z, vx/vy/vz                                                                               |
| 磁力计干扰     | `sensor_mag` + `actuator_outputs`                             | 磁场模长随油门变化                                                                            |

注意 pyulog 数据访问方式: `ulog.get_dataset("topic").data["field"]`, 数组字段形如 `"accelerometer_m_s2[0]"`; 时间戳单位微秒。

深挖脚本骨架示例 (按需修改话题/字段):

```python
import numpy as np
from pyulog import ULog
ulog = ULog("flight.ulg")
sp = ulog.get_dataset("vehicle_rates_setpoint").data
est = ulog.get_dataset("vehicle_angular_velocity").data
t_sp = np.asarray(sp["timestamp"], dtype=np.float64)
t_est = np.asarray(est["timestamp"], dtype=np.float64)
roll_sp = np.asarray(sp["roll"], dtype=np.float64)
roll_est = np.asarray(est["xyz[0]"], dtype=np.float64)
roll_est_i = np.interp(t_sp, t_est, roll_est)
err = roll_est_i - roll_sp
print(f"Roll rate RMS error: {np.sqrt(np.mean(err**2)):.3f} rad/s")
```

> 深挖脚本输出重定向到 `deep_dive.txt` (供报告附录 C 使用): `python analyze_vibration.py flight.ulg > deep_dive.txt 2>&1`

### 第 6 步: 检查历史报告并输出报告

先检查 `flight_reports/` 中是否有**同机型/同机架**的历史报告, 再输出报告并保存。

**6a. 检查历史报告 (有同机型历史报告时必做)**:

- 有: 读取最近一份 (重点看其元信息头、关键指标和附录数据), 在报告模板「二、飞行情况诊断」后增加「与上次飞行对比」小节, 逐项列出关键指标的变化趋势并解读:
  - 振动 std / clipping、角速率跟踪 RMS (逐轴)
  - 悬停油门/悬停功率 (旋翼) 或 巡航空速/巡航功率/TECS 误差 (固定翼、VTOL)
  - 电池最低单芯电压、电压跌落、消耗电量
  - EKF 创新量 RMS、GPS 质量
  - 上次报告「参数修改指南」中建议的参数, 本次是否已改、效果如何
- 无: 在报告中注明「本次为该机型首次分析, 无历史基线」, 本次报告即作为后续对比基线。

**6b. 输出报告正文**: 在对话中按报告模板输出报告正文 (元信息头 + 一~五节, 含历史对比小节)。**不输出附录** -- 附录由命令合并脚本输出文件自动生成, 无需模型复述, 节省 token。

**6c. 保存归档文档**:

1. **保存目录**: 工作目录下的 `flight_reports/` (不存在则创建), 全部历史报告统一放这里便于检索对比。
2. **文件命名**: `YYYYMMDD_<机型>_<机架标识>_<起飞时间或序号>.md`
   - 日期取日志起飞日期 (`ulog.start_timestamp`, 北京时间); 取不到则用当天日期。
   - 机型用英文标识: `fixed_wing` / `vtol` / `multicopter` / `unknown` (来自摘要脚本第 1 节机型判别)。
   - 机架标识: 优先 `SYS_AUTOSTART` 对应机架名或日志 `ver_sw`/`sys_name` 中的机型名; 取不到则询问用户或填 `airframe`。
   - 末尾可加起飞时间 (`HHmm`) 或当日序号 (`01`) 区分同日多次飞行。
   - 示例: `flight_reports/20260726_fixed_wing_mini-talon_1430.md`
3. **组装方式**: 将报告正文写入 `report_body.md`, 然后用合并脚本自动拼入附录 (无需模型手动嵌入):

   ```powershell
   python <skill目录>/scripts/merge_report.py report_body.md "flight_reports/YYYYMMDD_xxx.md"
   ```

   脚本自动读取同目录下的 `summary.txt`、`stune_output.txt`、`deep_dive.txt` (可选) 拼入附录 A/B/C。也可用参数指定路径: `--summary`/`--stune`/`--deep-dive`。

4. 保存后告知用户文档路径。

### 第 7 步: 清理临时文件

报告保存完成后, 清理分析过程中产生的中间文件, 保持工作目录整洁:

- **应删除**: `summary.txt`、`stune_output.txt`、`deep_dive.txt`、`report_body.md` (已合并入报告附录)、第 5 步生成的临时分析脚本 (如 `analyze_*.py`、`plot_*.py`)
- **应删除**: 从 logs.px4.io 下载的 `flight.ulg` (可随时通过 log id 重新下载; 若用户有保留需求则跳过)
- **保留**: `flight_reports/` 目录下的报告文件、用户原有的日志文件 (本地路径输入时)

---

## 判读知识库 (阈值与经验)

> 以下量化阈值部分参考本 skill 内置的 smarttune 规则文件: `scripts/smarttune/knowledge/rules/px4/pid_rules.json` (PID)、`filter_rules.json` (滤波)、`common/vibration_rules.json` (振动)。smarttune-cli 运行时自动加载这些规则生成评级, 此处列出供手动判读参考。

### 振动

- 加速度高频波动 std: <1 m/s² 优秀; 1~3 可接受; >3 需机械排查 (桨/电机动平衡、机架松动、减震)
  - 注: 此阈值为**去趋势 std** (摘要脚本第 4 节); smarttune 用**原始 RMS**, 阈值为 3/10/20/30 m/s² (excellent/good/marginal/poor/critical), 两者度量不同不可直接比较
- 去趋势后加速度峰峰值持续 >30 m/s² 或 clipping 计数增长: 严重, 先解决振动再谈调参
- Actuator controls FFT 只在低频(<20Hz)有单峰为正常; 中频尖峰 -> 配置陀螺仪陷波滤波器 `IMU_GYRO_NF0_FRQ`/`IMU_GYRO_NF0_BW`
- **smarttune FFT**: 振动 RMS >3 m/s² 为 POOR/SEVERE, 会直接给出陷波滤波器建议参数; 若 `IMU_GYRO_NF0_FRQ=0` (未启用) 且 smarttune 建议设为某频率, 应优先采纳
- 陀螺噪声地板 (deg/s, 来自 `vibration_rules.json`): <0.5 优秀 / <1.5 良好 / <3.0 边缘 / >=5.0 差
- FFT 峰值显著性: +10dB above floor 为显著, +20dB 为严重

### 角速率/姿态跟踪 (PID 判据)

- 角速率跟踪: 估计值应紧跟设定值。滞后/幅度不足 -> P 增益偏低; 高频抖动/超调振荡 -> P 或 D 偏高
- 姿态跟踪: RMS 误差 <2 deg 优秀; 2-5 可接受; >5 偏大; 稳态偏差大=外环 P 不足或需配平
- 存在稳态偏差 -> I 不足或需配平; 大机动时误差大、小机动正常 -> 执行器饱和, 查 `MC_*RATE_MAX`/推力余量
- **smarttune PID**: 评级 GOOD/MARGINAL/POOR; 超调 >50% 或振荡次数 >3 通常为 POOR; 注意 smarttune 的阶跃检测可能误判 (实际速率远超设定值时超调可达 1000%+, 说明传感器失效或失控而非纯粹 PID 问题)
- **smarttune SysID**: 自然频率与角速率误差频域主峰交叉验证 -> 若两者吻合, 存在结构共振
- 多旋翼角速率环: `MC_ROLLRATE_P/I/D/K`, `MC_PITCHRATE_*`, `MC_YAWRATE_*`
- 姿态环: `MC_ROLL_P`, `MC_PITCH_P`, `MC_YAW_P`
- 固定翼角速率环: `FW_RR_P/I/D/FF` (roll), `FW_PR_P/I/D/FF` (pitch), `FW_YR_P/I/D/FF` (yaw), FF 优先调好再调 P
- 固定翼姿态/航向: `FW_R_TC`, `FW_P_TC`; 航迹/高度: `NPFG_*`, `TECS_*` (FW_T_CLMB_MAX, FW_T_SINK_MAX 等)

**PID 阶跃响应量化阈值 - 多旋翼** (来自 `pid_rules.json`, smarttune PID 自动评级依据):

| 指标                  | 理想       | 可接受     | 边缘        | 差       |
| --------------------- | ---------- | ---------- | ----------- | -------- |
| 上升时间 (roll/pitch) | 60-110 ms  | 40-160 ms  | >160 ms     | -        |
| 上升时间 (yaw)        | 100-200 ms | 80-300 ms  | >300 ms     | -        |
| 超调量                | 0-10%      | 0-15%      | 15-25%      | >25%     |
| 稳定时间              | 150-350 ms | 100-600 ms | 600-1000 ms | >1000 ms |
| 振荡次数              | 0-1        | 1-2        | 2-3         | >3       |

**PID 阶跃响应量化阈值 - 固定翼** (来自 `pid_rules_fw.json`, FW 日志评级依据):

| 指标                  | 理想        | 可接受      | 边缘          | 差         |
| --------------------- | ----------- | ----------- | ------------- | ---------- |
| 上升时间 (roll/pitch) | 200-500 ms  | 150-800 ms  | >800 ms       | -          |
| 上升时间 (yaw)        | 300-700 ms  | 200-1000 ms | >1000 ms      | -          |
| 超调量                | 0-20%       | 0-30%       | 30-50%        | >50%       |
| 稳定时间              | 500-1500 ms | 300-2500 ms | 2500-4000 ms  | >4000 ms   |
| 振荡次数              | 0-1         | 1-2         | 2-3           | >3         |

> 注: FW 阈值整体宽于 MC — FW 时间常数 TC=0.5s, 响应慢于 MC 属正常; FW 的 FF 是主项, P 是次项, 调参顺序为 FF → P → I (与 MC 的 P → D → I 相反)。

**症状 -> 参数映射** (多旋翼 rate 环):

| 症状       | 主要调整                        | 次要检查                   |
| ---------- | ------------------------------- | -------------------------- |
| 过冲大     | 提 `MC_*RATE_D`                 | 适当降 `MC_*RATE_P`        |
| 响应慢     | 提 `MC_*RATE_P` 或 `MC_*RATE_K` | `IMU_GYRO_CUTOFF` 是否过低 |
| 稳态误差   | 提 `MC_*RATE_I`                 | `MC_*R_INT_LIM` 积分限幅   |
| 高频抖振   | 降 `MC_*RATE_D`                 | 降 `IMU_DGYRO_CUTOFF`      |
| 外扰恢复慢 | 提 `MC_*RATE_P`                 | 适当提 `MC_*RATE_I`        |

> 注: 超调时主调 D (D 用于 rate 阻尼, 抑制超调) — PX4 官方文档明确 "D gain is used for rate damping... avoid overshoots"。

**症状 -> 参数映射** (固定翼 rate 环):

| 症状       | 主要调整          | 次要检查                   |
| ---------- | ----------------- | -------------------------- |
| 过冲大     | 降 `FW_*_FF`      | 降 `FW_*_P`                |
| 响应慢     | 提 `FW_*_FF`      | 检查 `FW_*_RMAX` 限幅      |
| 稳态误差   | 提 `FW_*_I`       | 检查配平 `TRIM_*`          |
| 高频抖振   | 降 `FW_*_P`       | 检查 `IMU_GYRO_CUTOFF`     |
| 外扰恢复慢 | 提 `FW_*_FF`      | 适当提 `FW_*_P`            |

> 注: FW 的 FF 是主项, 过冲/响应慢优先调 FF; FW 的 D 通常为 0 (空气动力阻尼已足够), 不作为主要调整项。

**调参步进与安全限制**:

- 步进: P=0.01/步, I=0.02/步, D=0.0005/步, K=0.05/步; 单步最大变化 P/I=0.03/0.05, D=0.001
- 单次飞行参数变更不超过 ±25% (P/D/I 分别计)
- 调参顺序: 1)确认滤波器与振动匹配 -> 2)rate 环 P->D->I (Acro 打杆) -> 3)姿态环 -> 4)全姿态验证

**PX4 特有注意事项**:

- `MC_*RATE_K` 是整轴总增益乘子, P/I/D 比例合适但整体响应弱时优先调 K, 而非同步放大三项
- PX4 D 项量级 (~0.003) 远小于 ArduPilot (~0.015), 不可横向比较
- PX4 v1.14+ 推荐先用机载自动调参 (`MC_AT_EN`) 取得基线, 再用日志分析精修

### EKF / 估计器

- 位置/速度创新量 RMS 持续 >1 (水平位置 m / 水平速度 m/s) 或 >0.5 (垂直速度 m/s): GPS 质量差或 `EKF2_GPS_*` 噪声参数过松
- 磁力计创新量大/磁场随油门变化: 罗盘受电流干扰 → 外置罗盘远离电源线, 或重校准, 必要时 `EKF2_MAG_TYPE`
- estimator_status filter_fault_flags 非零 → 找对应传感器故障
- v1.16+ 创新量话题改名为 `estimator_innovations`, 字段变为 `gps_hpos/gps_vpos/gps_hvel/gps_vvel/mag_field` 等 (摘要脚本第 9 节已自动兼容)

### 执行器 / 动力

- 电机输出持续贴上限: 推力余量不足 (机重过大/电池电压低/桨效率低), 调参解决不了, 需提示硬件
- 某通道输出系统性高于其他: 机架不对称/重心偏移/电机或桨差异
- 角速率上限默认值 (来自 `px4.json`): `MC_ROLLRATE_MAX`=220 deg/s, `MC_PITCHRATE_MAX`=220, `MC_YAWRATE_MAX`=200; 姿态环 `MC_ROLL_P`/`MC_PITCH_P` 默认 4.0, `MC_YAW_P` 默认 2.8; 实际值以日志参数快照为准

### 滤波器参数 (来自 `filter_rules.json`)

| 参数               | 范围      | 默认 | 说明                                                                             |
| ------------------ | --------- | ---- | -------------------------------------------------------------------------------- |
| `IMU_GYRO_NF0_FRQ` | 0-1000 Hz | 0    | 静态陷波 0 中心频率, 0=禁用                                                      |
| `IMU_GYRO_NF0_BW`  | 0-100 Hz  | 20   | 静态陷波 0 带宽                                                                  |
| `IMU_GYRO_NF1_FRQ` | 0-1000 Hz | 0    | 静态陷波 1 (第二振动峰值)                                                        |
| `IMU_GYRO_NF1_BW`  | 0-100 Hz  | 20   | 静态陷波 1 带宽                                                                  |
| `IMU_GYRO_CUTOFF`  | 0-1000 Hz | 80   | 陀螺仪低通, 0=禁用; PX4 官方默认 80 Hz, 常用 40-120 Hz                            |
| `IMU_DGYRO_CUTOFF` | 0-1000 Hz | 30   | D 项专用低通; 注: PX4 官方默认 30 Hz, `filter_rules.json` 同; 以日志参数快照为准 |
| `IMU_ACCEL_CUTOFF` | 5-1000 Hz | 30   | 加速度计低通                                                                     |

- PX4 静态陷波 (`IMU_GYRO_NF0/NF1`) 不跟踪油门或 FFT, 无 ArduPilot 的 mode/REF/HMC/ATT 概念
- 电机基频随油门漂移时, 静态陷波需把 BW 设宽以覆盖漂移范围, 或改用 ESC RPM 动态陷波
- PX4 v1.14+ 支持 ESC RPM 动态陷波 (`IMU_GYRO_DNF_*`, 需 DShot telemetry), 对电机噪声效果优于静态陷波

### 固定翼空速 (摘要脚本第 10 节自动输出, 仅固定翼/VTOL)

- 巡航段空速: 平均/中位/最大/低速 5 分位, 并与 `FW_AIRSPD_TRIM`/`FW_AIRSPD_MIN`/`FW_AIRSPD_STALL` 对比
- 巡航空速持续偏离配平空速 >2 m/s: 配平值设置不当或空速计校准/安装问题 (查 `CAL_AIR_*`、皮托管动/静压孔)
- 低于 `FW_AIRSPD_MIN` 样本占比 >5% 或低速 5 分位接近/低于失速空速: 失速风险 → 提高 `FW_AIRSPD_MIN`/`FW_AIRSPD_TRIM`, 或减重、限制滚转/爬升角
- TECS 空速跟踪 RMS 误差 >3 m/s 或持续偏差: 查 `FW_T_*` 时间常数与增益、`TECS_*`; 持续偏慢+大俯角=推力/升力不足
- `underspeed_ratio` 持续 >0.1: TECS 判定欠速, 推力到上限或 MIN 空速定太高
- 空速计无效样本占比高 / 空速跳变: 皮托管堵塞、管路积水、空速计故障; 查 `ASPD_*`、`FW_ARSP_MODE`(无空速计飞行配置)

### 电源

- 悬停油门(归一化): <0.5 推力余量充足; 0.5~0.7 可接受; >0.7 余量不足, 抗风/机动能力下降, 调参解决不了, 需减重或换动力
- 悬停功率/巡航功率: 结合消耗电量可估算续航裕度; 同机型纵向对比可发现动力效率退化(桨磨损/电机老化)
- 电压跌落大 (如 4.2V/芯->3.3V/芯以下大电流时): 电池内阻大/C 数不足; 填 `BAT1_V_LOAD_DROP` 改善电压补偿
- 单芯最低电压 <3.3V(大电流时) 或 <3.5V(巡航): 电池过放风险, 提示检查电池健康度与 `BAT_LOW_THR` 类阈值
- 电量估算跳变: 检查 `BAT1_CAPACITY`, `BAT1_R_INTERNAL`

### 日志质量

- Dropout 多/大: SD 卡慢 -> 换卡或调整 `SDLOG_PROFILE` (关闭高采样位) / 减少记录话题, 数据缺失期结论不可信
- 深挖角速率环细节前建议用户下次开 `SDLOG_PROFILE` 高采样率位

**smarttune quality 评分机制** (来自 `services/analysis.py`):

- 起始分 100, 逐项扣分: 缺失必选数据(PID/Gyro) -20/项; 缺失可选数据(Mag/Motor/Battery) -5/项; 时长 <30s -30、<120s -10; 阶跃窗口总数 <3 -25、某轴 <3 -8; 丢包率 >5% -8、抖动 >20% -5
- 评级阈值: EXCELLENT >=90 / GOOD >=75 / MARGINAL >=55 / POOR <55
- 评分差时深度分析结论不可信, 应先改善日志配置 (增时长、加激励动作、换 SD 卡)

---

## 调参指南输出原则

1. **先机械后软件**: 振动超标、推力饱和、传感器安装问题必须先指出, 调参不能替代硬件修复
2. **按环分层**: 角速率环 → 姿态环 → 速度环 → 位置环, 内环没调好不动外环
3. **每条建议给出**: 参数名、当前值(来自日志参数快照)、建议方向/范围、依据(日志证据)、风险提示
4. **一次少改**: 建议分批修改, 参考知识库的调参步进值 (P=0.01/I=0.02/D=0.0005/K=0.05 每步), 单次飞行参数变更不超过 ±25%, 每次改完飞一版日志对比
5. **注明固件版本差异**: 参数名以日志中的 `ver_sw` 对应版本为准 (如 v1.16 的 `FW_RR_*` 与旧版不同)
6. **只推荐日志中存在的参数**: 日志参数快照中真实存在的参数均可给出修改建议, 无需额外验证; 参数名和当前值以摘要脚本第 11 节参数快照为准

---

## 报告模板

```markdown
## PX4 飞行日志分析报告

> **元信息头** (存档与对比检索用, 每项必填):
> 日期: YYYY-MM-DD | 机型: fixed_wing/vtol/multicopter | 机架: <机架标识> | 固件: <ver_sw> | 硬件: <ver_hw> | 日志来源: <文件名或 log id> | 时长: <s> | 日志质量: <stune quality 评分> | 报告文件: <本 md 文件名>

### 一、平台与日志概况

(硬件/固件/机型/时长/飞行模式/GPS/日志质量/功率与电源/固定翼空速 -- 数据来自 summary.txt 对应小节)

### 二、飞行情况诊断

1. 控制跟踪: (角速率/姿态跟踪 RMS, 逐轴结论)
2. 振动水平: (数值 + 是否正常)
3. 估计器状态: (EKF 创新量、故障标志)
4. 动力与电源: (饱和度、电压电流)
5. 固定翼空速与 TECS: (仅固定翼/垂起: 巡航空速 vs 配平/失速空速、跟踪误差、欠速比)
6. 事件与告警: (日志消息中的 WARN/ERROR, 附时间戳)

### 二附、与上次飞行对比 (有同机型历史报告时填写, 首次则写「无历史基线」)

| 指标 | 上次 (报告文件名) | 本次 | 变化 | 解读 |
| ---- | ----------------- | ---- | ---- | ---- |

### 三、问题清单 (按严重程度排序, 无异常时写「未发现异常」)

| 级别 | 问题 | 日志证据 |
| ---- | ---- | -------- |

### 四、参数修改指南 (无调整建议时写「暂无」)

| 参数 | 当前值 | 建议值/方向 | 依据 | 风险 |
| ---- | ------ | ----------- | ---- | ---- |

### 五、下次飞行验证建议

(改完后飞什么动作、看哪些曲线对比)

---

## 附录: 脚本执行结果 (由 merge_report.py 自动合并, 模型无需输出)
```

---

## 参考资料

PX4 官方文档: Flight Review 图表判读 / 多旋翼 PID 调参 / 多旋翼滤波调参 / 固定翼调参 / 参数查询 -- 均见 https://docs.px4.io/main/en/
