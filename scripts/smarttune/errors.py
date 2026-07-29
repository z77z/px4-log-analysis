"""
smarttune/errors.py

SmartTune 统一异常体系。

错误码约定:
  E10xx  文件/日志相关
  E20xx  解析相关
  E30xx  数据不足相关
  E40xx  参数/输入相关
  E50xx  分析模块相关
  E90xx  平台/功能未实现
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.text import Text


# ---------------------------------------------------------------------------
# 基类
# ---------------------------------------------------------------------------

class SmartTuneError(Exception):
    """所有 SmartTune 自定义异常的基类。"""

    code = "E0000"
    message = "未知错误"
    hint = ""

    def __init__(
        self,
        message: str | None = None,
        hint: str | None = None,
        code: str | None = None,
    ) -> None:
        self.message = message or self.__class__.message
        self.hint = hint or self.__class__.hint
        self.code = code or self.__class__.code
        super().__init__(self.message)

    def rich_render(self) -> Panel:
        lines = [Text(self.message, style="bold red")]
        if self.hint:
            lines.append(Text(f"\n💡 提示: {self.hint}", style="dim"))
        lines.append(Text(f"\n[错误] {self.code}", style="dim"))
        return Panel(
            "\n".join(str(l) for l in lines),
            title="[red]错误[/red]",
            border_style="red",
            expand=False,
        )

    def print(self) -> None:
        Console(stderr=True).print(self.rich_render())


# ---------------------------------------------------------------------------
# E10xx - 文件/日志相关
# ---------------------------------------------------------------------------

class LogFileError(SmartTuneError):
    code = "E1000"
    message = "日志文件操作失败"

class LogFileNotFoundError(LogFileError):
    code = "E1001"
    message = "未找到日志文件"
    hint = "请检查文件路径并确保文件存在。"

class LogFileCorruptError(LogFileError):
    code = "E1002"
    message = "日志文件损坏或格式不兼容"
    hint = "请确保文件是有效的飞行日志且未在写入时被截断。"


# ---------------------------------------------------------------------------
# E20xx - 解析相关
# ---------------------------------------------------------------------------

class ParseError(SmartTuneError):
    code = "E2000"
    message = "日志解析失败"

class LogFormatError(ParseError):
    code = "E2001"
    message = "无法识别的日志格式"
    hint = "SmartTune 支持 PX4 (.ulg) 日志。"

class LogVersionError(ParseError):
    code = "E2002"
    message = "日志固件版本可能不兼容"

class ParseIncompleteError(ParseError):
    code = "E2003"
    message = "日志解析不完整 — 记录可能已被中断"


# ---------------------------------------------------------------------------
# E30xx - 数据不足
# ---------------------------------------------------------------------------

class InsufficientDataError(SmartTuneError):
    code = "E3000"
    message = "数据不足以进行分析"

class InsufficientIMUDataError(InsufficientDataError):
    code = "E3001"
    message = "日志中 IMU 数据不足"

class InsufficientPIDDataError(InsufficientDataError):
    code = "E3002"
    message = "日志中 PID 数据不足"

class InsufficientCompassDataError(InsufficientDataError):
    code = "E3004"
    message = "日志中罗盘数据不足"

class InsufficientAttitudeDataError(InsufficientDataError):
    code = "E3005"
    message = "日志中姿态数据不足"


# ---------------------------------------------------------------------------
# E40xx - 参数/输入
# ---------------------------------------------------------------------------

class InvalidParameterError(SmartTuneError):
    code = "E4000"
    message = "无效参数"

class InvalidAxisError(InvalidParameterError):
    code = "E4001"
    message = "无效轴 — 预期为 roll、pitch 或 yaw"

class UnsupportedPlatformError(InvalidParameterError):
    code = "E4010"
    message = "不支持的平台"


# ---------------------------------------------------------------------------
# E50xx - 分析模块
# ---------------------------------------------------------------------------

class AnalysisError(SmartTuneError):
    code = "E5000"
    message = "分析失败"

class FFTAnalysisError(AnalysisError):
    code = "E5001"
    message = "FFT 分析失败"

class PIDAnalysisError(AnalysisError):
    code = "E5002"
    message = "PID 分析失败"

class MAGFitError(AnalysisError):
    code = "E5003"
    message = "磁力计校准分析失败"

class CapabilityNotSupportedError(AnalysisError):
    code = "E5010"
    message = "当前平台不支持此分析"
