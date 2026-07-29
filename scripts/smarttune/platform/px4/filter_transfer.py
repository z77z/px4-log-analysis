"""
smarttune/platform/px4/filter_transfer.py

PX4 滤波器传递函数分析 — 基于 IMU_GYRO_* / IMU_DGYRO_* / IMU_GYRO_NF* 参数
计算低通 + 陷波滤波器链的 Bode 图数据。

PX4 滤波参数体系（参考 filter_rules.json）：
  IMU_GYRO_CUTOFF   — 陀螺仪低通截止频率 (Hz)，0 = 禁用
  IMU_DGYRO_CUTOFF  — D 项专用低通截止频率 (Hz)
  IMU_ACCEL_CUTOFF  — 加速度计低通截止频率 (Hz)
  IMU_GYRO_NF0_FRQ  — 静态陷波 0 中心频率 (Hz)，0 = 禁用
  IMU_GYRO_NF0_BW   — 静态陷波 0 带宽 (Hz)
  IMU_GYRO_NF1_FRQ  — 静态陷波 1 中心频率 (Hz)，0 = 禁用
  IMU_GYRO_NF1_BW   — 静态陷波 1 带宽 (Hz)

注意：PX4 静态陷波不跟踪油门或 FFT — 无 ArduPilot 的 mode/REF/HMC/ATT 概念。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np


# ── 默认值（参数缺失时使用）──────────────────────────────────────
_DEFAULT_GYRO_CUTOFF_HZ = 40.0
_DEFAULT_DGYRO_CUTOFF_HZ = 30.0
_DEFAULT_ACCEL_CUTOFF_HZ = 30.0
_DEFAULT_NOTCH_BW_HZ = 20.0


# ---------------------------------------------------------------------------
# 参数提取辅助
# ---------------------------------------------------------------------------

def _get_param(params: Dict[str, float], name: str, default: float = 0.0) -> float:
    """从参数字典中安全取值。"""
    val = params.get(name)
    if val is None:
        return default
    try:
        v = float(val)
        return v if np.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def get_fallback_gyro_filter_hz(params: Dict[str, float]) -> float:
    """获取陀螺仪低通截止频率（缺失或禁用时回退到默认值）。"""
    cutoff = _get_param(params, "IMU_GYRO_CUTOFF", _DEFAULT_GYRO_CUTOFF_HZ)
    if cutoff <= 0:
        return _DEFAULT_GYRO_CUTOFF_HZ
    return cutoff


def get_notch_bandwidth_hz(params: Dict[str, float]) -> float:
    """获取陷波带宽（缺失时回退到默认值）。"""
    bw = _get_param(params, "IMU_GYRO_NF0_BW", _DEFAULT_NOTCH_BW_HZ)
    if bw <= 0:
        return _DEFAULT_NOTCH_BW_HZ
    return bw


# ---------------------------------------------------------------------------
# 滤波器传递函数计算
# ---------------------------------------------------------------------------

def _lowpass_response(
    freqs: np.ndarray,
    sample_rate: float,
    cutoff_hz: float,
) -> Tuple[np.ndarray, np.ndarray]:
    """一阶低通滤波器频率响应（双线性变换实现）。

    H(s) = wc / (s + wc),  wc = 2π * cutoff_hz
    双线性变换 → H(z) = (1+a)/2 * (1 + z^-1) / (1 + a*z^-1)
    """
    if cutoff_hz <= 0:
        # 禁用：直通
        return np.zeros_like(freqs), np.zeros_like(freqs)

    dt = 1.0 / sample_rate
    wc = 2.0 * np.pi * cutoff_hz
    # 预扭曲
    tan_half = np.tan(wc * dt / 2.0)
    a = (1.0 - tan_half) / (1.0 + tan_half)

    # z = e^(j*ω*dt), ω = 2π*f
    omega = 2.0 * np.pi * freqs
    z1 = np.exp(-1j * omega * dt)

    numerator = (1.0 + a) / 2.0 * (1.0 + z1)
    denominator = 1.0 + a * z1

    h = numerator / denominator
    mag_db = 20.0 * np.log10(np.abs(h) + 1e-12)
    phase_deg = np.degrees(np.angle(h))
    return mag_db, phase_deg


def _notch_response(
    freqs: np.ndarray,
    sample_rate: float,
    center_hz: float,
    bandwidth_hz: float,
) -> Tuple[np.ndarray, np.ndarray]:
    """二阶陷波滤波器频率响应。

    陷波传递函数（数字域）：
    H(z) = (1 - 2*cos(w0)*z^-1 + z^-2) / (1 - 2*r*cos(w0)*z^-1 + r^2*z^-2)
    其中 w0 = 2π*center_hz*dt, r 控制陷波深度/宽度（由带宽决定）。
    """
    if center_hz <= 0 or bandwidth_hz <= 0:
        return np.zeros_like(freqs), np.zeros_like(freqs)

    dt = 1.0 / sample_rate
    w0 = 2.0 * np.pi * center_hz * dt
    # 带宽 → r 的近似映射：BW 越宽 r 越小（陷波越浅/越宽）
    bw_norm = bandwidth_hz / (sample_rate / 2.0)
    r = max(0.5, 1.0 - bw_norm * 2.0)
    r = min(r, 0.99)

    omega = 2.0 * np.pi * freqs
    z1 = np.exp(-1j * omega * dt)
    z2 = np.exp(-2j * omega * dt)

    cos_w0 = np.cos(w0)
    numerator = 1.0 - 2.0 * cos_w0 * z1 + z2
    denominator = 1.0 - 2.0 * r * cos_w0 * z1 + (r ** 2) * z2

    h = numerator / denominator
    mag_db = 20.0 * np.log10(np.abs(h) + 1e-12)
    phase_deg = np.degrees(np.angle(h))
    return mag_db, phase_deg


def compute_filter_response(
    freqs: np.ndarray,
    sample_rate: float,
    gyro_cutoff_hz: Optional[float] = None,
    notch_params: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, float]] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """计算滤波器链总频率响应（低通 + 陷波级联）。

    支持两种调用模式：
    1. 手动模式：直接传入 gyro_cutoff_hz 和 notch_params
    2. 自动模式：传入 params 字典，从 PX4 参数推导滤波器配置

    返回 (magnitude_db, phase_deg) 数组。
    """
    # 自动模式：从参数推导
    if params is not None and gyro_cutoff_hz is None:
        return _compute_from_params(freqs, sample_rate, params)

    # 手动模式
    cutoff = gyro_cutoff_hz if gyro_cutoff_hz is not None else _DEFAULT_GYRO_CUTOFF_HZ
    mag_total = np.zeros_like(freqs)
    phase_total = np.zeros_like(freqs)

    # 低通
    mag_lp, phase_lp = _lowpass_response(freqs, sample_rate, cutoff)
    mag_total += mag_lp
    phase_total += phase_lp

    # 陷波
    if notch_params is not None:
        center = notch_params.get("center_hz", 0.0)
        bw = notch_params.get("bandwidth_hz", _DEFAULT_NOTCH_BW_HZ)
        mag_nf, phase_nf = _notch_response(freqs, sample_rate, center, bw)
        mag_total += mag_nf
        phase_total += phase_nf

    return mag_total, phase_total


def _compute_from_params(
    freqs: np.ndarray,
    sample_rate: float,
    params: Dict[str, float],
) -> Tuple[np.ndarray, np.ndarray]:
    """从 PX4 参数推导完整滤波器链并计算频率响应。"""
    mag_total = np.zeros_like(freqs)
    phase_total = np.zeros_like(freqs)

    # 陀螺仪低通
    gyro_cutoff = _get_param(params, "IMU_GYRO_CUTOFF", _DEFAULT_GYRO_CUTOFF_HZ)
    if gyro_cutoff > 0:
        mag_lp, phase_lp = _lowpass_response(freqs, sample_rate, gyro_cutoff)
        mag_total += mag_lp
        phase_total += phase_lp

    # 静态陷波 0
    nf0_freq = _get_param(params, "IMU_GYRO_NF0_FRQ", 0.0)
    nf0_bw = _get_param(params, "IMU_GYRO_NF0_BW", _DEFAULT_NOTCH_BW_HZ)
    if nf0_freq > 0:
        mag_nf0, phase_nf0 = _notch_response(freqs, sample_rate, nf0_freq, nf0_bw)
        mag_total += mag_nf0
        phase_total += phase_nf0

    # 静态陷波 1
    nf1_freq = _get_param(params, "IMU_GYRO_NF1_FRQ", 0.0)
    nf1_bw = _get_param(params, "IMU_GYRO_NF1_BW", _DEFAULT_NOTCH_BW_HZ)
    if nf1_freq > 0:
        mag_nf1, phase_nf1 = _notch_response(freqs, sample_rate, nf1_freq, nf1_bw)
        mag_total += mag_nf1
        phase_total += phase_nf1

    return mag_total, phase_total


# ---------------------------------------------------------------------------
# 滤波器配置推导与展示
# ---------------------------------------------------------------------------

def derive_filters_from_params(params: Dict[str, float]) -> Dict[str, Any]:
    """从 PX4 参数推导当前活跃的滤波器链配置。

    返回 dict 包含 config_summary 和各滤波器参数详情。
    """
    gyro_cutoff = _get_param(params, "IMU_GYRO_CUTOFF", _DEFAULT_GYRO_CUTOFF_HZ)
    dgyro_cutoff = _get_param(params, "IMU_DGYRO_CUTOFF", _DEFAULT_DGYRO_CUTOFF_HZ)
    accel_cutoff = _get_param(params, "IMU_ACCEL_CUTOFF", _DEFAULT_ACCEL_CUTOFF_HZ)

    nf0_freq = _get_param(params, "IMU_GYRO_NF0_FRQ", 0.0)
    nf0_bw = _get_param(params, "IMU_GYRO_NF0_BW", _DEFAULT_NOTCH_BW_HZ)
    nf1_freq = _get_param(params, "IMU_GYRO_NF1_FRQ", 0.0)
    nf1_bw = _get_param(params, "IMU_GYRO_NF1_BW", _DEFAULT_NOTCH_BW_HZ)

    parts: List[str] = []
    if gyro_cutoff > 0:
        parts.append(f"IMU_GYRO_CUTOFF={gyro_cutoff:.0f}Hz")
    if dgyro_cutoff > 0:
        parts.append(f"IMU_DGYRO_CUTOFF={dgyro_cutoff:.0f}Hz")
    if nf0_freq > 0:
        parts.append(f"NF0={nf0_freq:.0f}Hz/BW={nf0_bw:.0f}Hz")
    if nf1_freq > 0:
        parts.append(f"NF1={nf1_freq:.0f}Hz/BW={nf1_bw:.0f}Hz")

    config_summary = ", ".join(parts) if parts else "默认（无自定义滤波器）"

    return {
        "config_summary": config_summary,
        "gyro_cutoff_hz": gyro_cutoff,
        "dgyro_cutoff_hz": dgyro_cutoff,
        "accel_cutoff_hz": accel_cutoff,
        "notch0": {"freq_hz": nf0_freq, "bw_hz": nf0_bw, "enabled": nf0_freq > 0},
        "notch1": {"freq_hz": nf1_freq, "bw_hz": nf1_bw, "enabled": nf1_freq > 0},
    }


def build_filter_display_lines(params: Dict[str, float]) -> List[str]:
    """构建滤波器链的文本展示行（用于终端输出）。"""
    cfg = derive_filters_from_params(params)
    lines: List[str] = []

    gyro = cfg["gyro_cutoff_hz"]
    if gyro > 0:
        lines.append(f"  陀螺仪低通: IMU_GYRO_CUTOFF = {gyro:.0f} Hz")
    else:
        lines.append("  陀螺仪低通: 禁用 (IMU_GYRO_CUTOFF=0)")

    dgyro = cfg["dgyro_cutoff_hz"]
    if dgyro > 0:
        lines.append(f"  D 项低通:   IMU_DGYRO_CUTOFF = {dgyro:.0f} Hz")

    accel = cfg["accel_cutoff_hz"]
    if accel > 0:
        lines.append(f"  加速度低通: IMU_ACCEL_CUTOFF = {accel:.0f} Hz")

    for i, nf_key in enumerate(("notch0", "notch1")):
        nf = cfg[nf_key]
        if nf["enabled"]:
            lines.append(
                f"  静态陷波 {i}: IMU_GYRO_NF{i}_FRQ = {nf['freq_hz']:.0f} Hz, "
                f"BW = {nf['bw_hz']:.0f} Hz"
            )

    if not any([cfg["notch0"]["enabled"], cfg["notch1"]["enabled"]]):
        lines.append("  静态陷波: 未启用")

    return lines
