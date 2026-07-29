"""
smarttune/platform/px4/__init__.py

PX4 ULog 日志适配器 — 完整实现（基于 pyulog）。

uORB 主题映射：
  vehicle_angular_velocity  → PID 实际值 (rad/s → deg/s) + 陀螺仪
  vehicle_rates_setpoint    → PID 目标值 (rad/s → deg/s)
  sensor_combined           → gyro/accel 回退源
  vehicle_acceleration      → accel (m/s²)
  sensor_mag                → mag (Gauss → mGauss)
  actuator_motors           → motor_output (0~1，PX4 v1.14+)
  actuator_outputs          → motor_output 回退（PWM → 归一化）
  actuator_servos           → servo_output (-1~1，固定翼/VTOL 舵面)
  battery_status            → 电压 / 电流
  vehicle_gps_position /
  sensor_gps                → extras["gps_position"]（lat/lon 1e7 缩放）
  vehicle_attitude          → extras["attitude_quat"]（四元数）
  vtol_vehicle_status       → extras["vtol_mode_changes"] + FlightData.mode_changes

参数契约（A2）：parse() 会把 MC_ROLLRATE_P / FW_RR_P 等平台参数同时以
generic key（"pid.roll.p"）注入 FlightData.params，
供 PIDReviewer._get_current_pid 直接消费。机型由 AIRFRAME_TYPE 决定：
MC 机型使用 MC_*RATE_*，FW 机型使用 FW_RR_*/FW_PR_*/FW_YR_*，
VTOL 机型按 vtol_in_rw_mode 在 MC 与 FW 参数集之间动态切换。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import numpy as np

from smarttune.platform.base import PlatformAdapter
from smarttune.platform.registry import register
from smarttune.models.flight_data import AxisPIDSignal, FlightData, ModeChange
from smarttune.errors import (
    LogFileNotFoundError, LogFileCorruptError, ParseError,
    InsufficientPIDDataError, SmartTuneError,
)

logger = logging.getLogger(__name__)

# ── 多旋翼 (MC) Rate PID 参数映射 ────────────────────────────────
_PARAM_MAP_MC = {
    "pid.roll.p":    "MC_ROLLRATE_P",
    "pid.roll.i":    "MC_ROLLRATE_I",
    "pid.roll.d":    "MC_ROLLRATE_D",
    "pid.roll.ff":   "MC_ROLLRATE_FF",
    "pid.pitch.p":   "MC_PITCHRATE_P",
    "pid.pitch.i":   "MC_PITCHRATE_I",
    "pid.pitch.d":   "MC_PITCHRATE_D",
    "pid.pitch.ff":  "MC_PITCHRATE_FF",
    "pid.yaw.p":     "MC_YAWRATE_P",
    "pid.yaw.i":     "MC_YAWRATE_I",
    "pid.yaw.d":     "MC_YAWRATE_D",
    "pid.yaw.ff":    "MC_YAWRATE_FF",
}

# ── 固定翼 (FW) Rate PID 参数映射 ────────────────────────────────
# FW 特性：FF 是主项（~0.4），P 是次项（~0.06），D 通常为 0。
# PX4 官方参数：FW_RR_*/FW_PR_*/FW_YR_* 为 roll/pitch/yaw rate PID。
_PARAM_MAP_FW = {
    "pid.roll.p":    "FW_RR_P",
    "pid.roll.i":    "FW_RR_I",
    "pid.roll.d":    "FW_RR_D",
    "pid.roll.ff":   "FW_RR_FF",
    "pid.pitch.p":   "FW_PR_P",
    "pid.pitch.i":   "FW_PR_I",
    "pid.pitch.d":   "FW_PR_D",
    "pid.pitch.ff":  "FW_PR_FF",
    "pid.yaw.p":     "FW_YR_P",
    "pid.yaw.i":     "FW_YR_I",
    "pid.yaw.d":     "FW_YR_D",
    "pid.yaw.ff":    "FW_YR_FF",
}

# ── 滤波参数映射（机型无关）─────────────────────────────────────
_PARAM_MAP_FILTER = {
    "filter.gyro_lpf": "IMU_GYRO_CUTOFF",
    "filter.dterm_lpf": "IMU_DGYRO_CUTOFF",
    "filter.accel_lpf": "IMU_ACCEL_CUTOFF",
    # 静态陷波（PX4 无 mode/REF/HMC/ATT 概念，FFT 分析器已按平台分支
    # 只输出 freq/bw；enable 无独立参数 — NF0_FRQ=0 即禁用）
    "filter.notch1.freq": "IMU_GYRO_NF0_FRQ",
    "filter.notch1.bw":   "IMU_GYRO_NF0_BW",
    "filter.notch2.freq": "IMU_GYRO_NF1_FRQ",
    "filter.notch2.bw":   "IMU_GYRO_NF1_BW",
}

# 默认映射表（向后兼容：未检测到机型时使用 MC）
_PARAM_MAP_TO_PLATFORM = {**_PARAM_MAP_MC, **_PARAM_MAP_FILTER}

# 反向映射（MC 默认；FW 在 parse() 中按机型动态构造）
_PARAM_MAP_TO_GENERIC = {v: k for k, v in _PARAM_MAP_TO_PLATFORM.items()}

# ── PX4 AIRFRAME_TYPE 参数值 → 通用 frame_type ───────────────────
# 参考：PX4 airframe_reference.md（msg/Airframe.mc/vtol/fw）
# MC 系列：0=Quad, 1=Tri, 3=Hex(实际 4), 4=Hex, 5=Octo(实际 6), 6=Octo, 7=Octo,
# 2/8/9/10/11 = Heli / Coaxial / Helicopter coercion
# FW 系列：2=Fixed Wing
# VTOL 系列：18=VTOL Standard, 19=VTOL Tiltrotor, 20=VTOL Tail Sitter, 21=VTOL Tiltwing
_AIRFRAME_TYPE_TO_FRAME = {
    0:  "quad",
    1:  "tri",
    2:  "fixed_wing",
    3:  "hex",     # PX4 部分 Early Hex
    4:  "hex",
    5:  "octo",    # PX4 部分 Early Octo
    6:  "octo",
    7:  "octo",
    8:  "heli",    # Helicopter coercion
    9:  "heli",
    10: "heli",
    11: "heli",
    18: "vtol_standard",
    19: "vtol_tiltrotor",
    20: "vtol_tailsitter",
    21: "vtol_tiltwing",
}

# VTOL 机型集合（airframe_type 值）
_VTOL_AIRFRAME_TYPES = {18, 19, 20, 21}

# FW 机型集合
_FW_AIRFRAME_TYPES = {2}

# 判定是否为 FW 机型（含 VTOL 的 FW 阶段参数集判断）
def _is_fw_frame(frame_type: Optional[str]) -> bool:
    return frame_type in ("fixed_wing",)

def _is_vtol_frame(frame_type: Optional[str]) -> bool:
    return frame_type in ("vtol_standard", "vtol_tiltrotor",
                          "vtol_tailsitter", "vtol_tiltwing")

def _is_mc_frame(frame_type: Optional[str]) -> bool:
    return frame_type in ("quad", "tri", "hex", "octo", "heli")


def _select_param_map(frame_type: Optional[str]) -> Dict[str, str]:
    """根据机型选择 PID 参数映射表。

    MC 机型 → MC_*RATE_*；FW 机型 → FW_RR_*/FW_PR_*/FW_YR_*；
    VTOL 机型 → 默认 MC 参数集（VTOL 在 MC 阶段使用 MC 参数，
    FW 阶段的参数切换在解析 vtol_vehicle_status 时按时间段处理）。
    """
    if _is_fw_frame(frame_type):
        return {**_PARAM_MAP_FW, **_PARAM_MAP_FILTER}
    # MC 与 VTOL 默认使用 MC 参数集
    return {**_PARAM_MAP_MC, **_PARAM_MAP_FILTER}

# ULog 魔数: "ULog" 0x01 0x12 0x35
_ULOG_MAGIC = b"ULog\x01\x12\x35"

_RAD2DEG = 180.0 / np.pi


def _import_pyulog():
    try:
        from pyulog import ULog  # type: ignore
        return ULog
    except ImportError:
        raise SmartTuneError(
            code="E9002",
            message="解析 PX4 ULog 需要 pyulog，但未安装",
            hint="请使用: pip install pyulog 安装",
        )


def _get_dataset(ulog, name: str, multi_id: int = 0):
    """从 ULog 取指定主题的数据集，不存在返回 None。"""
    for d in ulog.data_list:
        if d.name == name and d.multi_id == multi_id:
            return d
    return None


def _ts_seconds(data: Dict[str, np.ndarray], t0_us: float) -> np.ndarray:
    """timestamp(µs) → 相对秒。"""
    return (data["timestamp"].astype(np.float64) - t0_us) * 1e-6


@register
class PX4Adapter(PlatformAdapter):
    """PX4 ULog 日志适配器（pyulog 后端）。"""

    @property
    def name(self) -> str:
        return "px4"

    @property
    def display_name(self) -> str:
        return "PX4"

    @property
    def supported_extensions(self) -> list[str]:
        return [".ulg", ".ulog"]

    @classmethod
    def detect(cls, path: Path) -> bool:
        if not path.is_file():
            return False
        if path.suffix.lower() not in (".ulg", ".ulog"):
            return False
        try:
            with open(path, "rb") as f:
                header = f.read(7)
            return header == _ULOG_MAGIC
        except (OSError, IOError):
            return False

    # ── 解析 ────────────────────────────────────────────────

    def parse(self, path: Path) -> FlightData:
        ULog = _import_pyulog()

        if not path.is_file():
            raise LogFileNotFoundError(
                message=f"日志文件未找到: {path}",
                hint="请检查文件路径并确保文件存在。",
            )

        try:
            ulog = ULog(str(path))
        except Exception as exc:
            raise LogFileCorruptError(
                message=f"ULog 解析失败: {exc}",
                hint="文件可能已损坏或不是有效的 ULog。",
            )

        # ── 参数：平台名 + generic key 双写（A2 契约）─────────
        params: Dict[str, float] = {}
        for k, v in (ulog.initial_parameters or {}).items():
            try:
                params[k] = float(v)
            except (TypeError, ValueError):
                pass

        # ── 机型检测（R3）：从 AIRFRAME_TYPE 解析 frame_type ──
        airframe_type_raw = params.get("AIRFRAME_TYPE")
        frame_type: Optional[str] = None
        if airframe_type_raw is not None:
            try:
                af_int = int(airframe_type_raw)
                frame_type = _AIRFRAME_TYPE_TO_FRAME.get(af_int)
            except (TypeError, ValueError):
                logger.debug("AIRFRAME_TYPE=%r 无法转为整数", airframe_type_raw)
        if frame_type is None:
            # 回退：检查 AIRFRAME 参考名（如 "quad_x"、"plane"、"vtol_standard"）
            airframe_ref = ulog.msg_info_dict.get("airframe", "") if ulog.msg_info_dict else ""
            airframe_ref_lower = airframe_ref.lower() if isinstance(airframe_ref, str) else ""
            if airframe_ref_lower.startswith(("plane", "fw_", "fixed_wing")):
                frame_type = "fixed_wing"
            elif airframe_ref_lower.startswith(("vtol", "tiltrotor", "tailsitter", "tiltwing")):
                frame_type = "vtol_standard"
            elif airframe_ref_lower.startswith(("quad", "hex", "octo", "tri", "heli")):
                # 简单推断为 quad（具体细分由 AIRFRAME_TYPE 决定）
                frame_type = "quad"

        # ── 根据机型选择参数映射表（R1）──────────────────────
        param_map = _select_param_map(frame_type)
        # 反向映射（供 map_param_to_generic 使用）
        param_map_reverse = {v: k for k, v in param_map.items()}

        # 缓存为实例属性，供 map_param_to_platform / map_param_to_generic 使用（Issue 3 修复）
        # 否则这两个方法只能查 _PARAM_MAP_TO_PLATFORM（永远 MC），FW/VTOL 会拿到错误的参数名
        self._param_map = param_map
        self._param_map_reverse = param_map_reverse
        self._frame_type = frame_type

        # 平台参数 → generic key 双写
        for generic, plat in param_map.items():
            if plat in params:
                params[generic] = params[plat]

        # VTOL：同时注入 FW 参数（若存在），供 FW 阶段分析使用
        if _is_vtol_frame(frame_type):
            for generic, plat in _PARAM_MAP_FW.items():
                # 用 fw. 前缀避免覆盖 MC 的 pid.* 键
                if plat in params:
                    params[f"fw.{generic}"] = params[plat]

        # ── 角速度（actual / gyro）────────────────────────────
        d_angvel = _get_dataset(ulog, "vehicle_angular_velocity")
        d_sensor = _get_dataset(ulog, "sensor_combined")

        if d_angvel is not None and "xyz[0]" in d_angvel.data:
            src = d_angvel.data
            gyro_keys = ("xyz[0]", "xyz[1]", "xyz[2]")
        elif d_sensor is not None and "gyro_rad[0]" in d_sensor.data:
            src = d_sensor.data
            gyro_keys = ("gyro_rad[0]", "gyro_rad[1]", "gyro_rad[2]")
        else:
            raise ParseError(
                message="ULog 中没有角速度主题 "
                        "(vehicle_angular_velocity / sensor_combined)",
                hint="请确保日志使用默认 PX4 logger 主题记录。",
            )

        t0_us = float(src["timestamp"][0])
        t_gyro = _ts_seconds(src, t0_us)
        gyro = np.column_stack([
            src[gyro_keys[0]].astype(np.float64) * _RAD2DEG,
            src[gyro_keys[1]].astype(np.float64) * _RAD2DEG,
            src[gyro_keys[2]].astype(np.float64) * _RAD2DEG,
        ])

        if len(t_gyro) < 100:
            raise InsufficientPIDDataError(
                message=f"角速度样本数过少 ({len(t_gyro)})",
                hint="飞行记录可能过短。",
            )

        # ── 角速率 setpoint（desired）→ 插值到 gyro 时间轴 ────
        d_sp = _get_dataset(ulog, "vehicle_rates_setpoint")
        pid_data: Dict[str, AxisPIDSignal] = {}
        if d_sp is not None and "roll" in d_sp.data:
            t_sp = _ts_seconds(d_sp.data, t0_us)
            for axis, key, col in (("roll", "roll", 0),
                                   ("pitch", "pitch", 1),
                                   ("yaw", "yaw", 2)):
                sp_deg = d_sp.data[key].astype(np.float64) * _RAD2DEG
                desired = np.interp(t_gyro, t_sp, sp_deg)
                pid_data[axis] = AxisPIDSignal(
                    timestamp_s=t_gyro.copy(),
                    desired=desired,
                    actual=gyro[:, col].copy(),
                )
        else:
            logger.warning(
                "vehicle_rates_setpoint 未记录 — PID 阶跃分析不可用 "
                "（FFT/质量分析仍可用）"
            )

        # ── 加速度 ───────────────────────────────────────────
        accel = None
        d_acc = _get_dataset(ulog, "vehicle_acceleration")
        if d_acc is not None and "xyz[0]" in d_acc.data:
            accel = np.column_stack([
                d_acc.data["xyz[0]"], d_acc.data["xyz[1]"], d_acc.data["xyz[2]"],
            ]).astype(np.float64)
        elif d_sensor is not None and "accelerometer_m_s2[0]" in d_sensor.data:
            accel = np.column_stack([
                d_sensor.data["accelerometer_m_s2[0]"],
                d_sensor.data["accelerometer_m_s2[1]"],
                d_sensor.data["accelerometer_m_s2[2]"],
            ]).astype(np.float64)

        # ── 磁力计（Gauss → mGauss，对齐 FlightData 单位契约）──
        mag = None
        mag_ts = None
        d_mag = _get_dataset(ulog, "sensor_mag")
        if d_mag is not None and "x" in d_mag.data:
            mag = np.column_stack([
                d_mag.data["x"], d_mag.data["y"], d_mag.data["z"],
            ]).astype(np.float64) * 1000.0
            mag_ts = _ts_seconds(d_mag.data, t0_us)
        elif d_sensor is not None and "magnetometer_ga[0]" in d_sensor.data:
            # 老固件（≤v1.8）无独立 sensor_mag 主题，磁力计在
            # sensor_combined.magnetometer_ga（已用 pyulog sample.ulg 验证）
            mag = np.column_stack([
                d_sensor.data["magnetometer_ga[0]"],
                d_sensor.data["magnetometer_ga[1]"],
                d_sensor.data["magnetometer_ga[2]"],
            ]).astype(np.float64) * 1000.0
            mag_ts = _ts_seconds(d_sensor.data, t0_us)

        # ── 电机输出 ─────────────────────────────────────────
        motor_output = None
        motor_ts = None
        d_motors = _get_dataset(ulog, "actuator_motors")
        if d_motors is not None:
            cols = [k for k in sorted(d_motors.data.keys())
                    if k.startswith("control[")]
            arrs = [d_motors.data[c].astype(np.float64) for c in cols]
            arrs = [a for a in arrs if np.any(np.isfinite(a)) and np.nanmax(np.abs(a)) > 0]
            if arrs:
                motor_output = np.clip(np.nan_to_num(np.column_stack(arrs)), 0.0, 1.0)
                motor_ts = _ts_seconds(d_motors.data, t0_us)
        if motor_output is None:
            d_out = _get_dataset(ulog, "actuator_outputs")
            if d_out is not None:
                cols = [k for k in sorted(d_out.data.keys()) if k.startswith("output[")]
                arrs = []
                for c in cols:
                    a = d_out.data[c].astype(np.float64)
                    if np.nanmax(a) > 800:        # 活跃 PWM 通道
                        arrs.append(np.clip((a - 1000.0) / 1000.0, 0.0, 1.0))
                if arrs:
                    motor_output = np.column_stack(arrs)
                    motor_ts = _ts_seconds(d_out.data, t0_us)

        # ── 舵面输出（R8：固定翼 / VTOL）─────────────────────
        # PX4 v1.14+ 的 actuator_servos 主题记录舵面归一化输出 (-1~1)。
        # 通道约定：0=aileron right, 1=aileron left, 2=elevator, 3=rudder,
        # 4=flaps, 5=spoilers（具体见 PX4 actuator_servos.msg）。
        servo_output = None
        servo_ts = None
        if _is_fw_frame(frame_type) or _is_vtol_frame(frame_type):
            d_servos = _get_dataset(ulog, "actuator_servos")
            if d_servos is not None:
                cols = [k for k in sorted(d_servos.data.keys())
                        if k.startswith("control[")]
                arrs = [d_servos.data[c].astype(np.float64) for c in cols]
                arrs = [a for a in arrs if np.any(np.isfinite(a)) and np.nanmax(np.abs(a)) > 0]
                if arrs:
                    # actuator_servos 输出范围 -1~1，不裁剪
                    servo_output = np.nan_to_num(np.column_stack(arrs))
                    servo_ts = _ts_seconds(d_servos.data, t0_us)

        # ── 电池 ─────────────────────────────────────────────
        batt_v = batt_a = batt_ts = None
        d_batt = _get_dataset(ulog, "battery_status")
        if d_batt is not None and "voltage_v" in d_batt.data:
            batt_v = d_batt.data["voltage_v"].astype(np.float64)
            if "current_a" in d_batt.data:
                batt_a = d_batt.data["current_a"].astype(np.float64)
            batt_ts = _ts_seconds(d_batt.data, t0_us)

        # ── GPS / 姿态 → extras ──────────────────────────────
        extras: Dict[str, Any] = {"ulog_msg_count": len(ulog.data_list)}
        if frame_type is not None:
            extras["airframe_type"] = frame_type
        if airframe_type_raw is not None:
            extras["airframe_type_raw"] = airframe_type_raw

        for gps_topic in ("vehicle_gps_position", "sensor_gps"):
            d_gps = _get_dataset(ulog, gps_topic)
            if d_gps is not None and "lat" in d_gps.data and len(d_gps.data["lat"]) > 0:
                lat_arr = d_gps.data["lat"].astype(np.float64)
                lon_arr = d_gps.data["lon"].astype(np.float64)
                scale = 1e-7 if np.nanmax(np.abs(lat_arr)) > 1000 else 1.0
                mid = len(lat_arr) // 2
                alt_m = 0.0
                if "alt" in d_gps.data:
                    alt_raw = float(d_gps.data["alt"][mid])
                    alt_m = alt_raw * 1e-3 if abs(alt_raw) > 100000 else alt_raw
                extras["gps_position"] = {
                    "lat": float(lat_arr[mid]) * scale,
                    "lon": float(lon_arr[mid]) * scale,
                    "alt": alt_m,
                }
                break

        d_att = _get_dataset(ulog, "vehicle_attitude")
        if d_att is not None and "q[0]" in d_att.data:
            extras["attitude_quat"] = {
                "time": _ts_seconds(d_att.data, t0_us),
                "q": np.column_stack([
                    d_att.data["q[0]"], d_att.data["q[1]"],
                    d_att.data["q[2]"], d_att.data["q[3]"],
                ]).astype(np.float64),
            }

        # ── VTOL 模式切换追踪（R10）──────────────────────────
        # vtol_vehicle_status.vtol_in_rw_mode: 1=MC (rotary wing), 0=FW (fixed wing)
        # 模式切换事件填充到 FlightData.mode_changes 与 extras["vtol_mode_changes"]
        mode_changes: List[ModeChange] = []
        if _is_vtol_frame(frame_type):
            d_vtol = _get_dataset(ulog, "vtol_vehicle_status")
            if d_vtol is not None and "vtol_in_rw_mode" in d_vtol.data:
                vtol_ts = _ts_seconds(d_vtol.data, t0_us)
                vtol_mode = d_vtol.data["vtol_in_rw_mode"].astype(np.int8)
                vtol_transitions: List[Dict[str, Any]] = []
                prev_mode = int(vtol_mode[0]) if len(vtol_mode) > 0 else -1
                for i in range(1, len(vtol_mode)):
                    cur_mode = int(vtol_mode[i])
                    if cur_mode != prev_mode:
                        # 切换事件
                        ts = float(vtol_ts[i])
                        # 1→0: MC→FW 转换；0→1: FW→MC 转换
                        if cur_mode == 0:
                            mode_name = "fw"
                            raw_mode = "VTOL_FW"
                        else:
                            mode_name = "mc"
                            raw_mode = "VTOL_MC"
                        mode_changes.append(ModeChange(
                            timestamp_s=ts, mode_name=mode_name, raw_mode=raw_mode,
                        ))
                        vtol_transitions.append({
                            "timestamp_s": ts,
                            "from": "mc" if prev_mode == 1 else "fw",
                            "to": mode_name,
                        })
                        prev_mode = cur_mode
                if vtol_transitions:
                    extras["vtol_mode_changes"] = vtol_transitions
                    logger.info(
                        "VTOL 日志检测到 %d 次模式切换", len(vtol_transitions),
                    )

        # ── 舵面输出 → extras ──────────────────────────────
        if servo_output is not None:
            extras["servo_output"] = {
                "time": servo_ts,
                "values": servo_output,
                "channels": servo_output.shape[1] if servo_output.ndim > 1 else 1,
                "note": "actuator_servos 归一化输出 (-1~1)，通道 0-5 见 PX4 msg",
            }

        # ── 采样率 / 时长 ─────────────────────────────────────
        dts = np.diff(t_gyro)
        dts = dts[dts > 1e-6]
        sample_rate_hz = 1.0 / float(np.median(dts)) if len(dts) else 0.0
        duration_s = float(t_gyro[-1] - t_gyro[0]) if len(t_gyro) > 1 else 0.0

        sys_name = ""
        try:
            sys_name = ulog.msg_info_dict.get("sys_name", "")
            fw = ulog.msg_info_dict.get("ver_sw", "")
        except Exception:
            fw = ""

        return FlightData(
            platform="px4",
            firmware_version=str(fw),
            frame_type=frame_type,
            board_name=str(sys_name) or None,
            log_file=str(path),
            sample_rate_hz=sample_rate_hz,
            duration_s=duration_s,
            pid=pid_data,
            gyro=gyro,
            accel=accel,
            imu_timestamp_s=t_gyro,
            mag=mag,
            mag_timestamp_s=mag_ts,
            motor_output=motor_output,
            motor_timestamp_s=motor_ts,
            battery_voltage=batt_v,
            battery_current=batt_a,
            battery_timestamp_s=batt_ts,
            mode_changes=mode_changes,
            params=params,
            extras=extras,
        )

    # ── 参数映射 ────────────────────────────────────────────

    def map_param_to_platform(self, generic_name: str) -> str:
        # Issue 3 修复：使用 parse() 中按机型缓存的 _param_map，
        # 否则 FW/VTOL 日志会错误地返回 MC_*RATE_* 参数名
        pm = getattr(self, "_param_map", None)
        if pm is not None:
            return pm.get(generic_name, generic_name)
        # 回退：parse() 未调用前用 MC 默认表（仅用于命令行帮助等场景）
        return _PARAM_MAP_TO_PLATFORM.get(generic_name, generic_name)

    def map_param_to_generic(self, platform_name: str) -> str:
        pm_rev = getattr(self, "_param_map_reverse", None)
        if pm_rev is not None:
            return pm_rev.get(platform_name, platform_name)
        return _PARAM_MAP_TO_GENERIC.get(platform_name, platform_name)

    # ── 能力声明 ────────────────────────────────────────────

    def capabilities(self) -> Set[str]:
        # filter/hardware 已移除：滤波器分析由 fft 模块的陷波建议覆盖，
        # 硬件信息由摘要脚本 px4_log_summary.py 第 1/11 节覆盖，避免功能重复
        return {"pid", "fft", "sysid", "quality"}
