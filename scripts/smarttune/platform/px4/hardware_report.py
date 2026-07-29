"""
smarttune/platform/px4/hardware_report.py

PX4 硬件配置报告 — 从 ULog 参数提取飞控型号、IMU 配置、
罗盘配置、滤波器配置和 PID 参数概要。

输出 dict 供 services 层序列化为 HardwareReport 或直接展示。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from smarttune.models.analysis_result import HardwareReport
from smarttune.models.flight_data import FlightData


# ---------------------------------------------------------------------------
# 参数提取辅助
# ---------------------------------------------------------------------------

def _get_param(params: Dict[str, float], name: str, default: Any = None) -> Any:
    """从参数字典中安全取值。"""
    val = params.get(name)
    if val is None:
        return default
    return val


def _get_param_float(params: Dict[str, float], name: str, default: float = 0.0) -> float:
    """从参数字典中安全取浮点值。"""
    val = params.get(name)
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# 各配置项提取
# ---------------------------------------------------------------------------

def _extract_imu_configs(params: Dict[str, float]) -> List[Dict[str, Any]]:
    """提取 IMU 配置（陀螺仪/加速度计 ID、优先级）。

    PX4 IMU 参数前缀：SENS_IMU_* / IMU_*
    """
    configs: List[Dict[str, Any]] = []

    # PX4 SENS_IMU_* 参数记录各 IMU 的设备 ID
    for i in range(3):
        dev_id = _get_param(params, f"SENS_IMU{ i }_ID")
        if dev_id is not None and _get_param_float(params, f"SENS_IMU{ i }_ID", 0) > 0:
            configs.append({
                "index": i,
                "device_id": int(_get_param_float(params, f"SENS_IMU{ i }_ID")),
                "gyro_cutoff_hz": _get_param_float(params, "IMU_GYRO_CUTOFF", 40.0),
                "accel_cutoff_hz": _get_param_float(params, "IMU_ACCEL_CUTOFF", 30.0),
            })

    if not configs:
        # 回退：仅记录滤波器配置
        configs.append({
            "index": 0,
            "gyro_cutoff_hz": _get_param_float(params, "IMU_GYRO_CUTOFF", 40.0),
            "accel_cutoff_hz": _get_param_float(params, "IMU_ACCEL_CUTOFF", 30.0),
        })

    return configs


def _extract_compass_configs(params: Dict[str, float]) -> List[Dict[str, Any]]:
    """提取罗盘配置。

    PX4 罗盘参数：CAL_MAG*_ID / CAL_MAG*_PRIO / SENS_MAG*_ID
    """
    configs: List[Dict[str, Any]] = []

    for i in range(2):
        dev_id = _get_param(params, f"SENS_MAG{ i }_ID")
        prio = _get_param(params, f"CAL_MAG{ i }_PRIO")
        if dev_id is not None or prio is not None:
            configs.append({
                "index": i,
                "device_id": int(_get_param_float(params, f"SENS_MAG{ i }_ID", 0)),
                "priority": int(_get_param_float(params, f"CAL_MAG{ i }_PRIO", 0)),
                "external": bool(_get_param_float(params, f"CAL_MAG{ i }_EXT", 0)),
            })

    return configs


def _extract_filter_config(params: Dict[str, float]) -> Dict[str, Any]:
    """提取滤波器配置。"""
    return {
        "gyro_cutoff_hz": _get_param_float(params, "IMU_GYRO_CUTOFF", 40.0),
        "dgyro_cutoff_hz": _get_param_float(params, "IMU_DGYRO_CUTOFF", 30.0),
        "accel_cutoff_hz": _get_param_float(params, "IMU_ACCEL_CUTOFF", 30.0),
        "notch0_freq_hz": _get_param_float(params, "IMU_GYRO_NF0_FRQ", 0.0),
        "notch0_bw_hz": _get_param_float(params, "IMU_GYRO_NF0_BW", 20.0),
        "notch1_freq_hz": _get_param_float(params, "IMU_GYRO_NF1_FRQ", 0.0),
        "notch1_bw_hz": _get_param_float(params, "IMU_GYRO_NF1_BW", 20.0),
    }


def _extract_pid_params(params: Dict[str, float], frame_type: Optional[str] = None) -> Dict[str, Dict[str, float]]:
    """提取 PID 参数，按机型选择 MC_*RATE_* 或 FW_RR_*/FW_PR_*/FW_YR_*。

    返回 {axis: {p, i, d, ff}} 形式的 dict。
    """
    is_fw = frame_type in ("fixed_wing",)

    if is_fw:
        prefix_map = {
            "roll":  ("FW_RR_P", "FW_RR_I", "FW_RR_D", "FW_RR_FF"),
            "pitch": ("FW_PR_P", "FW_PR_I", "FW_PR_D", "FW_PR_FF"),
            "yaw":   ("FW_YR_P", "FW_YR_I", "FW_YR_D", "FW_YR_FF"),
        }
    else:
        prefix_map = {
            "roll":  ("MC_ROLLRATE_P", "MC_ROLLRATE_I", "MC_ROLLRATE_D", "MC_ROLLRATE_FF"),
            "pitch": ("MC_PITCHRATE_P", "MC_PITCHRATE_I", "MC_PITCHRATE_D", "MC_PITCHRATE_FF"),
            "yaw":   ("MC_YAWRATE_P", "MC_YAWRATE_I", "MC_YAWRATE_D", "MC_YAWRATE_FF"),
        }

    pid_params: Dict[str, Dict[str, float]] = {}
    for axis, (p, i, d, ff) in prefix_map.items():
        pid_params[axis] = {
            "p":  _get_param_float(params, p, 0.0),
            "i":  _get_param_float(params, i, 0.0),
            "d":  _get_param_float(params, d, 0.0),
            "ff": _get_param_float(params, ff, 0.0),
        }

    return pid_params


def _check_integrity(
    params: Dict[str, float],
    imu_configs: List[Dict[str, Any]],
    filter_config: Dict[str, Any],
) -> List[str]:
    """检查配置完整性，返回问题列表。"""
    issues: List[str] = []

    # 检查陀螺仪低通是否过低（影响响应）
    gyro_cutoff = filter_config.get("gyro_cutoff_hz", 40.0)
    if 0 < gyro_cutoff < 10:
        issues.append(f"IMU_GYRO_CUTOFF={gyro_cutoff:.0f}Hz 过低，可能引起响应延迟")

    # 检查陷波器中心频率是否在合理范围
    nf0_freq = filter_config.get("notch0_freq_hz", 0.0)
    if nf0_freq > 0 and nf0_freq < 50:
        issues.append(f"IMU_GYRO_NF0_FRQ={nf0_freq:.0f}Hz 低于典型电机基频范围")

    # 检查 IMU 数量
    if len(imu_configs) == 0:
        issues.append("未检测到任何 IMU 配置")

    return issues


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def generate_hardware_report(
    params: Dict[str, float],
    flight_data: Optional[FlightData] = None,
) -> Dict[str, Any]:
    """生成 PX4 硬件配置报告。

    Parameters
    ----------
    params : Dict[str, float]
        从 ULog 解析的参数字典。
    flight_data : FlightData, optional
        飞行数据（用于提取机型、固件版本等元信息）。

    Returns
    -------
    Dict[str, Any]
        硬件配置报告，可直接序列化或用于构造 HardwareReport。
    """
    frame_type = None
    firmware_version = ""
    board_name = ""

    if flight_data is not None:
        frame_type = flight_data.frame_type
        firmware_version = flight_data.firmware_version
        board_name = flight_data.board_name or ""

    imu_configs = _extract_imu_configs(params)
    compass_configs = _extract_compass_configs(params)
    filter_config = _extract_filter_config(params)
    pid_params = _extract_pid_params(params, frame_type)
    integrity_issues = _check_integrity(params, imu_configs, filter_config)

    report = HardwareReport(
        firmware_version=firmware_version,
        board_name=board_name,
        imu_configs=imu_configs,
        compass_configs=compass_configs,
        filter_config=filter_config,
        pid_params=pid_params,
        integrity_issues=integrity_issues,
    )

    # 返回 dict（供 services 层直接 JSON 序列化）
    return {
        "firmware_version": report.firmware_version,
        "board_name": report.board_name,
        "frame_type": frame_type or "unknown",
        "imu_configs": report.imu_configs,
        "compass_configs": report.compass_configs,
        "filter_config": report.filter_config,
        "pid_params": report.pid_params,
        "integrity_issues": report.integrity_issues,
    }
