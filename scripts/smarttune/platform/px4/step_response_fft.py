"""
阶跃响应估计 — PX4 分派模块。

PX4 vehicle_angular_velocity 与 ArduPilot RATE 同为高采样率均匀时间序列，
直接复用平台无关实现（Hanning 窗 + 93.75% 重叠 +
高斯 CDF 正则化 Wiener 反卷积）。

pid_reviewer 按 `smarttune.platform.px4.step_response_fft` 动态分派到此。
"""

from smarttune.analyzers.step_response_fft import (  # noqa: F401
    estimate_step_response,
    compute_step_response_for_axis,
)
