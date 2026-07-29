"""
smarttune/models/analysis_result.py

平台无关的分析结果结构。分析引擎输出这些结构，
输出层通过 ParamMapper 翻译回平台特有参数名。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

try:
    import numpy as np  # noqa: F401  — SysIDResult.a_coeffs/b_coeffs 类型标注用
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False


# ---------------------------------------------------------------------------
# 通用评估等级
# ---------------------------------------------------------------------------

class Assessment(str, Enum):
    EXCELLENT = "EXCELLENT"
    GOOD = "GOOD"
    MARGINAL = "MARGINAL"
    POOR = "POOR"
    UNUSABLE = "UNUSABLE"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ---------------------------------------------------------------------------
# 参数引用 — 平台无关的参数标识
# ---------------------------------------------------------------------------

@dataclass
class ParamRef:
    """平台无关的参数引用。

    分析引擎只使用 generic_name，输出层通过
    PlatformAdapter.map_param() 翻译为平台实际参数名。

    命名约定（generic_name）:
        pid.{axis}.p        → PID P 增益
        pid.{axis}.i        → PID I 增益
        pid.{axis}.d        → PID D 增益
        pid.{axis}.ff       → PID 前馈
        pid.{axis}.filt     → PID D 项滤波器
        pid.{axis}.filt_t   → PID 目标滤波器
        filter.gyro_lpf     → 陀螺仪低通截止频率
        filter.notch1.freq  → 第一陷波中心频率
        filter.notch1.bw    → 第一陷波带宽
        filter.notch1.att   → 第一陷波衰减
        mag.ofs.{axis}      → 磁力计偏移
    """

    generic_name: str
    axis: Optional[str] = None    # 可选值: "roll", "pitch", "yaw", "x", "y", "z"
    category: str = ""            # 类别: "pid", "filter", "mag", "general"

    def __post_init__(self):
        if not self.category:
            self.category = self.generic_name.split(".")[0]


# ---------------------------------------------------------------------------
# PID 分析结果
# ---------------------------------------------------------------------------

@dataclass
class StepMetrics:
    """单个阶跃响应的性能指标。"""
    rise_time_ms: float = -1.0
    overshoot_percent: float = -1.0
    settling_time_ms: float = -1.0
    oscillation_count: int = -1
    steady_state_error_percent: float = -1.0
    peak_value: float = -1.0
    final_value: float = -1.0
    step_magnitude: float = -1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rise_time_ms": self.rise_time_ms,
            "overshoot_percent": self.overshoot_percent,
            "settling_time_ms": self.settling_time_ms,
            "oscillation_count": self.oscillation_count,
            "steady_state_error_percent": self.steady_state_error_percent,
            "peak_value": self.peak_value,
            "final_value": self.final_value,
            "step_magnitude": self.step_magnitude,
        }


@dataclass
class DiagnosisEntry:
    """单条诊断结果。"""
    symptom: str
    severity: str          # 严重度: "high", "medium", "low"
    affected_metric: str
    rule_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symptom": self.symptom,
            "severity": self.severity,
            "affected_metric": self.affected_metric,
            "rule_id": self.rule_id,
        }


@dataclass
class ParamRecommendation:
    """单条参数修改建议 — 平台无关。"""
    param: ParamRef
    current: float
    suggested: float
    reason: str
    confidence: Confidence = Confidence.MEDIUM
    action: str = ""       # 操作类型: "increase", "decrease", "set"

    @property
    def change_percent(self) -> float:
        if self.current == 0:
            return 0.0
        return (self.suggested - self.current) / self.current * 100


@dataclass
class AxisPIDResult:
    """单轴 PID 分析结果。"""
    axis: str
    metrics: StepMetrics
    assessment: Assessment = Assessment.GOOD
    diagnoses: List[DiagnosisEntry] = field(default_factory=list)
    recommendations: List[ParamRecommendation] = field(default_factory=list)
    step_count: int = 0
    # 原始阶跃响应数据（用于时域绘图）
    step_responses: List[Dict[str, Any]] = field(default_factory=list)
    # FFT 频域阶跃响应（用于 WebTools 风格曲线图 — 核心可视化数据）
    fft_step: Dict[str, Any] = field(default_factory=dict)
    # 完整原始时间序列（time_ms, desired, actual, P, I, D）
    raw_data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "axis": self.axis,
            "metrics": self.metrics.to_dict(),
            "assessment": self.assessment.value,
            "recommendations": [
                {"param": r.param.generic_name, "current": r.current,
                 "suggested": r.suggested, "reason": r.reason, "action": r.action}
                for r in self.recommendations
            ],
            "step_count": self.step_count,
            "fft_step": self.fft_step,
            "raw_data": self.raw_data,
        }


@dataclass
class PIDAnalysisResult:
    """完整 PID 分析结果。"""
    axes: Dict[str, AxisPIDResult] = field(default_factory=dict)
    overall_assessment: Assessment = Assessment.GOOD


# ---------------------------------------------------------------------------
# FFT 分析结果
# ---------------------------------------------------------------------------

@dataclass
class VibrationPeak:
    """单个振动峰值。"""
    frequency_hz: float
    amplitude: float           # m/s²
    source_guess: str = ""     # 来源推测: "motor", "propeller", "frame", "unknown"
    harmonic_of: Optional[float] = None  # 如果是某频率的谐波


@dataclass
class FFTAnalysisResult:
    """FFT 频谱分析结果。"""
    peaks: List[VibrationPeak] = field(default_factory=list)
    noise_floor: float = 0.0   # m/s²
    vibration_level: Assessment = Assessment.GOOD
    recommendations: List[ParamRecommendation] = field(default_factory=list)
    # 原始频谱数据（用于绘图）
    spectrum_freqs: Optional[Any] = None
    spectrum_power: Optional[Any] = None


# ---------------------------------------------------------------------------
# 滤波器分析结果
# ---------------------------------------------------------------------------

@dataclass
class FilterAnalysisResult:
    """滤波器传递函数分析结果。"""
    cutoff_3db_hz: Optional[float] = None
    config_summary: str = ""
    recommendations: List[ParamRecommendation] = field(default_factory=list)
    # Bode plot 数据
    freqs: Optional[Any] = None
    magnitude_db: Optional[Any] = None
    phase_deg: Optional[Any] = None


# ---------------------------------------------------------------------------
# 磁力计分析结果
# ---------------------------------------------------------------------------

@dataclass
class MagFitResult:
    """磁力计校准分析结果。"""
    fitness_mgauss: float = 0.0
    assessment: Assessment = Assessment.GOOD
    offsets: Dict[str, float] = field(default_factory=dict)  # {"x": ..., "y": ..., "z": ...}
    recommendations: List[ParamRecommendation] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 系统辨识结果（R5 统一定义：从 analyzers/sysid_analyzer.py 迁移至此，
# 作为 SysIDResult 的唯一定义；sysid_analyzer.py 改为从此处导入）
# ---------------------------------------------------------------------------

@dataclass
class SysIDResult:
    """单轴系统辨识（ARX 模型）结果。"""
    axis: str

    # ARX 模型参数
    na: int
    nb: int
    delay_samples: int
    a_coeffs: Any = field(repr=False)   # np.ndarray
    b_coeffs: Any = field(repr=False)   # np.ndarray

    # 连续系统参数（二阶近似）
    natural_freq_hz: float
    damping_ratio: float
    dc_gain: float

    # PID 带宽建议
    suggested_bandwidth_hz: float
    suggested_p_gain: float

    # 拟合质量
    fit_quality_percent: float

    # 原始数据信息
    sample_rate_hz: float
    data_points: int

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式（用于输出/序列化）。"""
        a_list = self.a_coeffs.tolist() if hasattr(self.a_coeffs, "tolist") else list(self.a_coeffs)
        b_list = self.b_coeffs.tolist() if hasattr(self.b_coeffs, "tolist") else list(self.b_coeffs)
        return {
            "axis": self.axis,
            "arx_model": {
                "na": self.na,
                "nb": self.nb,
                "delay_samples": self.delay_samples,
                "a_coeffs": a_list,
                "b_coeffs": b_list,
            },
            "continuous_approximation": {
                "natural_freq_hz": round(self.natural_freq_hz, 2),
                "damping_ratio": round(self.damping_ratio, 3),
                "dc_gain": round(self.dc_gain, 3),
            },
            "pid_recommendations": {
                "suggested_bandwidth_hz": round(self.suggested_bandwidth_hz, 1),
                "suggested_p_gain": round(self.suggested_p_gain, 4),
            },
            "fit_quality": {
                "fit_percent": round(self.fit_quality_percent, 1),
                "sample_rate_hz": round(self.sample_rate_hz, 1),
                "data_points": self.data_points,
            },
        }


# ---------------------------------------------------------------------------
# 硬件报告
# ---------------------------------------------------------------------------

@dataclass
class HardwareReport:
    """硬件配置报告 — 包含飞控、传感器、参数概要。"""
    firmware_version: str = ""
    board_name: str = ""
    imu_configs: List[Dict[str, Any]] = field(default_factory=list)
    compass_configs: List[Dict[str, Any]] = field(default_factory=list)
    filter_config: Dict[str, Any] = field(default_factory=dict)
    pid_params: Dict[str, Dict[str, float]] = field(default_factory=dict)
    battery_reports: List[Dict[str, Any]] = field(default_factory=list)
    integrity_issues: List[str] = field(default_factory=list)
    extras: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 综合分析结果
# ---------------------------------------------------------------------------

@dataclass
class FullAnalysisResult:
    """综合分析结果容器。"""
    platform: str = ""
    log_file: str = ""
    pid: Optional[PIDAnalysisResult] = None
    fft: Optional[FFTAnalysisResult] = None
    filter: Optional[FilterAnalysisResult] = None
    magfit: Optional[MagFitResult] = None
    sysid: Dict[str, "SysIDResult"] = field(default_factory=dict)
    hardware: Optional[HardwareReport] = None

    @property
    def all_recommendations(self) -> List[ParamRecommendation]:
        """收集所有模块的参数建议。"""
        recs: List[ParamRecommendation] = []
        if self.pid:
            for ax_result in self.pid.axes.values():
                recs.extend(ax_result.recommendations)
        if self.fft:
            recs.extend(self.fft.recommendations)
        if self.filter:
            recs.extend(self.filter.recommendations)
        if self.magfit:
            recs.extend(self.magfit.recommendations)
        return recs
