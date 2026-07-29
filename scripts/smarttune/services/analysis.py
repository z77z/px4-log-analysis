"""
smarttune/services/analysis.py

飞行日志分析的纯库层。

不产生 Rich 输出、不执行 shell、不做任意写入。
返回结构化 dataclass 与 dict，适合 JSON 序列化。

与 CLI 命令完全对齐（PX4 平台仅支持以下 4 类分析）：
  - get_log_quality()     ↔  stune quality
  - analyze_log()         ↔  stune analyze  (pid + fft + sysid)
  - analyze_pid()         ↔  stune pid
  - analyze_fft()         ↔  stune fft
  - analyze_sysid()       ↔  stune sysid

PX4 不支持 magfit/filter/hardware：
  - 滤波器建议由 fft 模块的陷波输出覆盖
  - 硬件信息由摘要脚本 px4_log_summary.py 第 1 节覆盖
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from smarttune.errors import SmartTuneError
from smarttune.knowledge import KnowledgeBase
from smarttune.models.analysis_result import FullAnalysisResult
from smarttune.models.flight_data import FlightData
from smarttune.platform.base import PlatformAdapter
from smarttune.platform.registry import resolve_adapter
from smarttune.services.serialize import (
    serialize_full_result,
    serialize_pid_result,
    serialize_fft_result,
    serialize_sysid_results,
    serialize_extra_analyzers_results,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 加载 / 解析
# ---------------------------------------------------------------------------

def load_flight_data(
    log_path: Path,
    platform: str = "auto",
) -> Tuple[PlatformAdapter, FlightData]:
    """解析飞行日志并返回适配器与统一 FlightData。

    若检测或解析失败则抛出 SmartTuneError。
    """
    _lp = Path(log_path) if isinstance(log_path, str) else log_path
    adapter = resolve_adapter(platform, _lp)
    flight_data = adapter.parse(_lp)
    return adapter, flight_data


# ---------------------------------------------------------------------------
# 日志质量 — 与 CLI quality 命令完全对齐
# ---------------------------------------------------------------------------

def get_log_quality(
    log_path: Path,
    platform: str = "auto",
) -> Dict[str, Any]:
    """解析日志并返回质量评估 dict。

    完全对应 CLI ``stune quality`` 命令：
      1. 数据完整性（PID/Gyro/Mag/Motor/Battery）
      2. 时长检查
      3. 激励（每个轴的阶跃响应窗口）
      4. 采样率一致性（抖动、丢包率）
    """
    _lp = Path(log_path) if isinstance(log_path, str) else log_path
    adapter, fd = load_flight_data(_lp, platform)

    issues: List[str] = []
    score = 100

    # ── 1. 数据完整性 ──────────────────────────────────
    has_pid = bool(fd.pid)
    has_gyro = fd.gyro is not None and len(fd.gyro) > 0
    has_mag = fd.has_mag
    has_motor = fd.has_motor
    has_battery = fd.has_battery

    completeness: List[Dict[str, Any]] = []

    n_pid = 0
    if has_pid:
        for sig in fd.pid.values():
            n_pid = max(n_pid, sig.sample_count)
    completeness.append({"name": "PID/RATE", "samples": n_pid, "ok": n_pid > 0, "required": True})

    n_gyro = len(fd.gyro) if has_gyro else 0
    completeness.append({"name": "IMU/Gyro", "samples": n_gyro, "ok": n_gyro > 0, "required": True})

    completeness.append({"name": "Magnetometer", "samples": len(fd.mag) if has_mag else 0, "ok": has_mag, "required": False})
    completeness.append({"name": "Motor", "samples": len(fd.motor_output) if has_motor else 0, "ok": has_motor, "required": False})
    completeness.append({"name": "Battery", "samples": len(fd.battery_voltage) if has_battery else 0, "ok": has_battery, "required": False})

    for item in completeness:
        if not item["ok"] and item["required"]:
            issues.append(f"缺失必选数据: {item['name']}")
            score -= 20
        elif not item["ok"] and not item["required"]:
            issues.append(f"可选数据缺失: {item['name']}")
            score -= 5

    # ── 2. 时长检查 ─────────────────────────────────────
    duration_s = fd.duration_s
    duration_min = duration_s / 60.0

    if duration_s < 30:
        issues.append(f"日志时长仅 {duration_s:.0f}s - 过短（建议 >= 3 分钟）")
        score -= 30
    elif duration_s < 120:
        issues.append(f"日志时长 {duration_s:.0f}s 偏短 - 建议至少 3 分钟")
        score -= 10
    elif duration_s < 300:
        issues.append(f"日志时长 {duration_min:.1f} 分钟 - 可满足基本分析")

    # ── 3. 激励（阶跃响应窗口）─────────────────
    # C9 修复：此处使用与 PIDReviewer 相同的 detect_steps()，使
    # `stune quality` 与 `stune pid` 显示的“阶跃窗口”计数不再互相矛盾
    # （旧代码此处使用临时基于 std 的阈值，而分析器使用基于 max 的阈值）。
    step_counts: Dict[str, int] = {}
    if has_pid and n_pid > 10:
        try:
            from smarttune.analyzers.pid_reviewer import detect_steps

            for ax in fd.axes:
                sig = fd.pid.get(ax)
                if sig is None or sig.desired is None or len(sig.desired) <= 10:
                    step_counts[ax] = 0
                    continue
                dt_ms = 4.0
                t = sig.timestamp_s
                if t is not None and len(t) > 1:
                    dts = np.diff(t)
                    dts_valid = dts[dts > 1e-6]
                    if len(dts_valid) > 0:
                        dt_ms = max(float(np.median(dts_valid)) * 1000.0, 0.1)
                step_counts[ax] = len(detect_steps(sig.desired, dt_ms=dt_ms))
        except Exception:
            pass

    if step_counts:
        min_steps = min(step_counts.values()) if step_counts else 0
        total_steps = sum(step_counts.values())

        if total_steps < 3:
            ax_str = " / ".join(f"{a.capitalize()}:{step_counts.get(a, 0)}" for a in step_counts)
            issues.append(
                f"阶跃响应窗口不足 ({ax_str}) "
                "- 请在 Stabilize/AltHold 模式下进行快速打杆激励"
            )
            score -= 25
        elif min_steps < 3:
            weak_axis = min(step_counts, key=step_counts.get)
            issues.append(
                f"{weak_axis.capitalize()} 轴阶跃窗口较少 ({step_counts[weak_axis]}), "
                "PID 分析可能不可靠"
            )
            score -= 8

    # ── 4. 采样率一致性 ────────────────────────────
    rate_consistency: List[Dict[str, Any]] = []

    if has_pid and n_pid > 2:
        for sig in fd.pid.values():
            t = sig.timestamp_s
            if t is not None and len(t) > 2:
                dts = np.diff(t)
                dts_valid = dts[(dts > 0) & (dts < 1.0)]
                if len(dts_valid) > 0:
                    median_dt = float(np.median(dts_valid))
                    sr = 1.0 / median_dt if median_dt > 0 else 0
                    std_dt = float(np.std(dts_valid))
                    jitter_pct = std_dt / median_dt * 100 if median_dt > 0 else 0
                    drop_count = int(np.sum(dts_valid > median_dt * 1.5))
                    drop_rate = drop_count / len(dts_valid) * 100
                    rate_consistency.append({
                        "source": "RATE/PID",
                        "sample_rate_hz": round(sr, 1),
                        "jitter_percent": round(jitter_pct, 1),
                        "drop_rate_percent": round(drop_rate, 1),
                    })
                    if drop_rate > 5:
                        issues.append(f"RATE 消息丢包率偏高 ({drop_rate:.1f}%)")
                        score -= 8
                    if jitter_pct > 20:
                        issues.append(f"RATE 消息时序抖动偏大 ({jitter_pct:.1f}%)")
                        score -= 5
                break  # 仅检查第一个轴

    score = max(0, min(100, score))

    # 评级
    if score >= 90:
        rating, advice = "EXCELLENT", "可进行全面分析"
    elif score >= 75:
        rating, advice = "GOOD", "可进行分析；部分数据可能受限"
    elif score >= 55:
        rating, advice = "MARGINAL", "可进行分析但结果可能不完整"
    else:
        rating, advice = "POOR", "日志质量较低；建议改善日志配置后重新飞行"

    file_size_mb = _lp.stat().st_size / (1024 * 1024)

    return {
        "platform": adapter.name,
        "display_name": adapter.display_name,
        "log_file": _lp.name,
        "file_size_mb": round(file_size_mb, 2),
        "duration_s": round(fd.duration_s, 1),
        "sample_rate_hz": round(fd.sample_rate_hz, 1),
        "axes": fd.axes,
        "has_gyro": has_gyro,
        "has_accel": fd.accel is not None and len(fd.accel) > 0,
        "has_mag": has_mag,
        "has_motor": has_motor,
        "has_battery": has_battery,
        "data_completeness": completeness,
        "step_counts": step_counts if step_counts else None,
        "rate_consistency": rate_consistency if rate_consistency else None,
        "validation_issues": fd.validate(),
        "quality": {
            "score": score,
            "rating": rating,
            "advice": advice,
        },
    }


# ---------------------------------------------------------------------------
# 共享模块运行器 — 分析器装配的唯一事实来源（A1 修复）
#
# CLI（Rich 渲染）与本 services 层（JSON 序列化）都调用 run_module()；
# 分析器实例化 / 知识库装配 / 能力与数据校验仅在此处存在，
# 因此两个入口不会再发生漂移。
# ---------------------------------------------------------------------------

_MODULE_ERROR_CODES = {
    "pid":      ("E5010", "E5011"),
    "fft":      ("E5020", "E5021"),
    "magfit":   ("E5030", "E5031"),
    "sysid":    ("E5040", "E5041"),
    "filter":   ("E5050", "E5051"),
    "hardware": ("E5060", "E5061"),
}


def _require_capability(module: str, adapter: PlatformAdapter) -> None:
    if module not in adapter.capabilities():
        cap_code = _MODULE_ERROR_CODES.get(module, ("E5000",))[0]
        raise SmartTuneError(
            message=f"{adapter.display_name} 不支持 {module} 分析",
            hint=f"支持的功能: {', '.join(sorted(adapter.capabilities()))}",
            code=cap_code,
        )


def run_module(
    module: str,
    adapter: PlatformAdapter,
    fd: FlightData,
    kb: Optional[KnowledgeBase] = None,
    axis: str = "all",
    na: int = 3,
    nb: int = 2,
) -> Any:
    """在已解析的 FlightData 上运行单个分析模块。

    返回原始结果对象（PIDAnalysisResult / FFTAnalysisResult /
    sysid dict）—— 由调用方决定是序列化（services）还是渲染（CLI）。

    若缺少能力或缺少所需数据则抛出 SmartTuneError。
    """
    _require_capability(module, adapter)
    data_code = _MODULE_ERROR_CODES.get(module, ("E5000", "E5001"))[1]
    _axis = axis if axis != "all" else None
    if kb is None:
        kb = KnowledgeBase(platform=adapter.name)

    if module == "pid":
        if not fd.pid:
            raise SmartTuneError(
                message="日志中未找到 PID 数据",
                hint="日志可能缺少 RATE 或 PID 控制器数据",
                code=data_code,
            )
        from smarttune.analyzers.pid_reviewer import PIDReviewer
        # 根据 frame_type 选择 MC 或 FW 知识库规则
        # VTOL 默认使用 MC 规则（MC 阶段为主），FW 阶段分段分析为后续迭代
        is_fw = fd.frame_type in ("fixed_wing",)
        pid_kb = kb.get("pid_rules_fw" if is_fw else "pid_rules", {})
        reviewer = PIDReviewer(knowledge=pid_kb)
        return reviewer.analyze(fd, axis=_axis)

    if module == "fft":
        if fd.gyro is None or len(fd.gyro) == 0:
            raise SmartTuneError(
                message="日志中未找到陀螺仪数据",
                hint="FFT 分析需要陀螺仪数据",
                code=data_code,
            )
        from smarttune.analyzers.fft_analyzer import FFTAnalyzer
        # 合并 filter_rules + vibration_rules，使 FFTAnalyzer 能读取振动阈值
        # (vibration_thresholds 定义在 vibration_rules.json，不在 filter_rules.json)
        fft_kb = {**kb.get("filter_rules", {}), **kb.get("vibration_rules", {})}
        analyzer = FFTAnalyzer(knowledge=fft_kb)
        return analyzer.analyze(fd)

    if module == "sysid":
        if not fd.pid:
            raise SmartTuneError(
                message="日志中未找到 PID 数据",
                hint="系统辨识需要 PID 角速率数据",
                code=data_code,
            )
        from smarttune.analyzers.sysid_analyzer import SysIDAnalyzer
        analyzer = SysIDAnalyzer(na=na, nb=nb)
        return analyzer.analyze(fd, axis=_axis)

    raise ValueError(f"未知分析模块: {module!r}")


# ---------------------------------------------------------------------------
# 各单项分析函数（分别对应每个 CLI 命令）
# ---------------------------------------------------------------------------

def analyze_pid(
    log_path: Path,
    platform: str = "auto",
    axis: str = "all",
    max_recommendations: int = 20,
) -> Dict[str, Any]:
    """运行 PID 阶跃响应分析。对应 ``stune pid``。"""
    _lp = Path(log_path) if isinstance(log_path, str) else log_path
    adapter, fd = load_flight_data(_lp, platform)
    result = run_module("pid", adapter, fd, axis=axis)

    return {
        "platform": adapter.name,
        "display_name": adapter.display_name,
        "log_file": _lp.name,
        "duration_s": round(fd.duration_s, 1),
        **serialize_pid_result(result, adapter, max_recommendations),
    }


def analyze_fft(
    log_path: Path,
    platform: str = "auto",
    max_recommendations: int = 20,
) -> Dict[str, Any]:
    """运行 FFT 振动频谱分析。对应 ``stune fft``。"""
    _lp = Path(log_path) if isinstance(log_path, str) else log_path
    adapter, fd = load_flight_data(_lp, platform)
    result = run_module("fft", adapter, fd)

    return {
        "platform": adapter.name,
        "display_name": adapter.display_name,
        "log_file": _lp.name,
        "duration_s": round(fd.duration_s, 1),
        **serialize_fft_result(result, adapter, max_recommendations),
    }


def analyze_sysid(
    log_path: Path,
    platform: str = "auto",
    axis: str = "all",
    na: int = 3,
    nb: int = 2,
) -> Dict[str, Any]:
    """运行 ARX 系统辨识。对应 ``stune sysid``。"""
    _lp = Path(log_path) if isinstance(log_path, str) else log_path
    adapter, fd = load_flight_data(_lp, platform)
    results = run_module("sysid", adapter, fd, axis=axis, na=na, nb=nb)

    if not results:
        raise SmartTuneError(
            message="系统辨识未产出结果",
            hint="日志可能缺少充分的数据或激励",
            code="E5042",
        )

    return {
        "platform": adapter.name,
        "display_name": adapter.display_name,
        "log_file": _lp.name,
        "duration_s": round(fd.duration_s, 1),
        **serialize_sysid_results(results),
    }


# ---------------------------------------------------------------------------
# 综合分析 — 完整（对应 `stune analyze`）
# ---------------------------------------------------------------------------

def analyze_log(
    log_path: Path,
    platform: str = "auto",
    axis: str = "all",
    include_modules: Optional[List[str]] = None,
    max_recommendations: int = 20,
) -> Dict[str, Any]:
    """运行综合分析并返回结构化结果 dict。

    Parameters
    ----------
    log_path : Path
        飞行日志文件路径。
    platform : str
        "auto" 或显式平台名。
    axis : str
        "all"、"roll"、"pitch" 或 "yaw"。
    include_modules : list[str] | None
        ["pid", "fft", "sysid"] 的子集。为 None 表示运行所有可用模块。
    max_recommendations : int
        包含的参数建议最大数量。

    Returns
    -------
    dict
        可 JSON 序列化的分析结果，包含 modules、module_failures
        以及安全元数据。
    """
    _lp = Path(log_path) if isinstance(log_path, str) else log_path
    adapter, fd = load_flight_data(_lp, platform)
    capabilities = adapter.capabilities()
    kb = KnowledgeBase(platform=adapter.name)

    full_result = FullAnalysisResult(platform=adapter.name, log_file=_lp.name)
    module_failures: List[Dict[str, str]] = []

    # 确定要运行的模块
    # PX4 仅支持 pid/fft/sysid；magfit/filter/hardware 不支持
    all_modules = {"pid", "fft", "sysid"}
    if include_modules is not None:
        requested = set(include_modules) & all_modules
    else:
        requested = all_modules

    # --- PID ---
    if "pid" in requested and "pid" in capabilities and fd.pid:
        try:
            full_result.pid = run_module("pid", adapter, fd, kb=kb, axis=axis)
        except Exception as exc:
            logger.warning("PID 分析失败: %s", exc)
            module_failures.append({"module": "pid", "error": str(exc)})

    # --- FFT ---
    if "fft" in requested and "fft" in capabilities and fd.gyro is not None:
        try:
            full_result.fft = run_module("fft", adapter, fd, kb=kb)
        except Exception as exc:
            logger.warning("FFT 分析失败: %s", exc)
            module_failures.append({"module": "fft", "error": str(exc)})

    # --- SysID ---
    sysid_dict = None
    if "sysid" in requested and "sysid" in capabilities and fd.pid:
        try:
            sysid_results = run_module("sysid", adapter, fd, kb=kb, axis=axis)
            if sysid_results:
                sysid_dict = serialize_sysid_results(sysid_results)
        except Exception as exc:
            logger.warning("SysID 分析失败: %s", exc)
            module_failures.append({"module": "sysid", "error": str(exc)})

    # --- 额外分析器（平台专属，例如 Betaflight 的 FF/RPM/DTerm）---
    extra_results = None
    extra_analyzers = adapter.extra_analyzers()
    if extra_analyzers:
        extra_results = {}
        for ea in extra_analyzers:
            name = getattr(ea, 'name', type(ea).__name__)
            try:
                ea_result = ea.analyze(fd)
                extra_results[name] = ea_result
            except Exception as exc:
                logger.warning("额外分析器 %s 失败: %s", name, exc)
                module_failures.append({"module": name, "error": str(exc)})
        if not extra_results:
            extra_results = None

    # 检查至少一个模块成功
    has_any = any([
        full_result.pid, full_result.fft,
        sysid_dict, extra_results,
    ])
    if not has_any:
        if module_failures:
            raise SmartTuneError(
                message="所有请求的分析模块均失败",
                hint="; ".join(f"{mf['module']}: {mf['error']}" for mf in module_failures),
                code="E5099",
            )
        raise SmartTuneError(
            message="无分析模块可在此日志上运行",
            hint="日志可能缺少所请求分析所需的必要数据（PID 信号、陀螺仪）。",
            code="E5098",
        )

    # 序列化
    result = serialize_full_result(full_result, adapter, max_recommendations)

    # 添加不在 FullAnalysisResult 数据类中的模块
    if sysid_dict is not None:
        result["modules"]["sysid"] = sysid_dict
    if extra_results is not None:
        result["modules"]["extra"] = serialize_extra_analyzers_results(extra_results)

    result["display_name"] = adapter.display_name
    result["duration_s"] = round(fd.duration_s, 1)
    result["module_failures"] = module_failures
    result["safety"] = {
        "read_only": True,
        "path_validated": True,
        "parameter_write_performed": False,
    }
    return result
