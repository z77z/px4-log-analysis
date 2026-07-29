"""
smarttune/cli.py

SmartTune CLI 入口 — 多平台飞行日志分析与调参顾问。
"""

import sys
from pathlib import Path
from typing import NoReturn, Optional

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.table import Table

from smarttune import __version__
from smarttune.errors import SmartTuneError
from smarttune.platform.registry import resolve_adapter, list_platforms

_console = Console(stderr=True)


def _print_error(exc: SmartTuneError) -> None:
    _console.print(exc.rich_render())


def _fail_in_progress(progress: Progress, exc: SmartTuneError) -> NoReturn:
    """在 Progress 上下文内遇到致命错误时的统一退出路径。"""
    progress.stop()
    _print_error(exc)
    sys.exit(1)


# ---------------------------------------------------------------------------
# 入口组
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(version=__version__, prog_name="smarttune", message="%(version)s")
def main():
    """SmartTune — PX4 飞行日志分析与调参顾问。

    \b
    支持的平台：
      PX4  (.ulg)  — PID / FFT / SysID / Quality / Filter / Hardware
                     支持多旋翼 (MC)、固定翼 (FW)、VTOL 三种机型

    \b
    工作流程：
      1. stune analyze -i log.ulg           # 综合分析
      2. stune pid -i log.ulg --visual      # PID 阶跃响应
      3. stune fft -i log.ulg --visual      # 振动频谱
      4. stune filter -i log.ulg --visual   # 滤波器波特图
      5. stune quality -i log.ulg           # 日志质量评分
      6. stune hardware -i log.ulg          # 硬件配置报告

    \b
    命令：
      analyze   综合分析（PID + FFT + Filter + Hardware + SysID）
      quality   日志质量评分（数据完整性 / 激励 / 采样率）
      pid       PID 阶跃响应分析（自动识别 MC/FW 机型）
      fft       FFT 振动频谱分析
      filter    滤波器传递函数分析（波特图）
      sysid     ARX 系统辨识
      hardware  硬件配置报告

    \b
    平台根据日志文件格式自动检测。
    使用 --platform 覆盖：stune analyze -i log.ulg --platform px4
    """
    pass


# ---------------------------------------------------------------------------
# platforms — 列出支持的平台
# ---------------------------------------------------------------------------

@main.command()
def platforms():
    """列出所有支持的飞控平台。"""
    table = Table(title="支持的平台")
    table.add_column("平台", style="cyan")
    table.add_column("显示名称")
    table.add_column("扩展名")
    table.add_column("功能")

    for p in list_platforms():
        table.add_row(p["name"], p["display_name"], p["extensions"], p["capabilities"])

    _console.print(table)


# ---------------------------------------------------------------------------
# analyze — 综合分析
# ---------------------------------------------------------------------------

@main.command()
@click.option("-i", "--input", "log_file", required=True,
              type=click.Path(exists=True, path_type=Path), help="飞行日志文件")
@click.option("--platform", "platform_name", default="auto",
              help="平台：auto 或 px4（默认：auto）")
@click.option("-o", "--output", "output_file", type=click.Path(path_type=Path),
              default=None, help="输出报告文件")
@click.option("--report", "report_format",
              type=click.Choice(["md", "html"], case_sensitive=False),
              default=None, help="报告格式：md（Markdown）或 html（自包含 HTML）")
@click.option("--visual/--no-visual", default=False, help="生成图表")
@click.option("--axis", type=click.Choice(["roll", "pitch", "yaw", "all"], case_sensitive=False),
              default="all", help="要分析的轴")
@click.option("--theme", type=click.Choice(["light", "dark"], case_sensitive=False),
              default="light", help="图表主题：light（默认）或 dark")
def analyze(log_file: Path, platform_name: str, output_file: Optional[Path],
            report_format: Optional[str], visual: bool, axis: str, theme: str):
    """综合日志分析 — PID + FFT + 滤波器 + 磁力计建议。"""
    try:
        adapter = resolve_adapter(platform_name, log_file)
    except SmartTuneError as exc:
        _print_error(exc)
        sys.exit(1)

    # 收集各模块的失败信息
    module_failures: list[tuple[str, Exception]] = []

    from smarttune.knowledge import KnowledgeBase
    from smarttune.output.formatter import OutputFormatter
    from smarttune.models.analysis_result import FullAnalysisResult

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
        console=_console,
        transient=False,
    ) as progress:
        # 阶段 1：解析日志
        p_parse = progress.add_task("[cyan]解析日志...", total=None)
        try:
            flight_data = adapter.parse(log_file)
            progress.update(p_parse, completed=True,
                            description=f"[green]✓ 已解析 {flight_data.duration_s:.0f}s ({adapter.display_name})")
        except SmartTuneError as exc:
            _fail_in_progress(progress, exc)

        capabilities = adapter.capabilities()
        kb = KnowledgeBase(platform=adapter.name)
        fmt = OutputFormatter(adapter=adapter, output_file=output_file, theme=theme)
        full_result = FullAnalysisResult(platform=adapter.name, log_file=str(log_file))

        # 阶段 2：PID 分析
        # A1 修复：分析器接线现在只存在于 services.run_module ——
        # CLI 只负责渲染。（之前：这里重复实例化了 PIDReviewer/FFTAnalyzer/MAGFit，
        # 与 services 层不一致。）
        from smarttune.services.analysis import run_module

        pid_result = None
        if "pid" in capabilities and flight_data.pid:
            p_pid = progress.add_task("[cyan]PID 分析...", total=None)
            try:
                pid_result = run_module("pid", adapter, flight_data, kb=kb, axis=axis)
                full_result.pid = pid_result
                progress.update(p_pid, completed=True, description="[green]✓ PID 分析完成")
            except Exception as exc:
                module_failures.append(("PID", exc))
                progress.update(p_pid, completed=True, description=f"[yellow]! PID 已跳过: {exc}")

        # 阶段 3：FFT 分析
        fft_result = None
        if "fft" in capabilities and flight_data.gyro is not None:
            p_fft = progress.add_task("[cyan]FFT 分析...", total=None)
            try:
                fft_result = run_module("fft", adapter, flight_data, kb=kb)
                full_result.fft = fft_result  # B2 修复：之前未赋值
                progress.update(p_fft, completed=True, description="[green]✓ FFT 分析完成")
            except Exception as exc:
                module_failures.append(("FFT", exc))
                progress.update(p_fft, completed=True, description=f"[yellow]! FFT 已跳过: {exc}")

        # 阶段 4：磁力计校准
        magfit_result = None
        if "magfit" in capabilities and flight_data.has_mag:
            p_mag = progress.add_task("[cyan]磁力计分析...", total=None)
            try:
                magfit_result = run_module("magfit", adapter, flight_data, kb=kb)
                full_result.magfit = magfit_result
                progress.update(p_mag, completed=True, description="[green]✓ 磁力计分析完成")
            except Exception as exc:
                module_failures.append(("Magnetometer", exc))
                progress.update(p_mag, completed=True, description=f"[yellow]! 磁力计已跳过: {exc}")

        # 检查：至少一个模块必须成功
        if pid_result is None and fft_result is None and magfit_result is None:
            progress.stop()
            for mod_name, exc in module_failures:
                _console.print(f"\n[bold red]✗ {mod_name} 失败:[/bold red]")
                if isinstance(exc, SmartTuneError):
                    _print_error(exc)
                else:
                    _console.print(f"  {exc}")
            _console.print("\n[bold red]✗ 分析失败: 所有模块均无法处理此日志[/bold red]")
            sys.exit(1)

        # ── 确定报告格式 ──────────────────────────────────────
        effective_report_format = report_format
        if effective_report_format is None and output_file is not None:
            if output_file.suffix.lower() == ".html":
                effective_report_format = "html"
            elif output_file.suffix.lower() in (".md", ".txt"):
                effective_report_format = "md"

        # ── HTML 报告路径 ─────────────────────────────────────
        if effective_report_format == "html":
            p_html = progress.add_task("[cyan]生成 HTML 报告...", total=None)
            try:
                from smarttune.output.html_report import generate_html_report, save_html_report

                html_out = generate_html_report(
                    pid_results=pid_result,
                    fft_results=fft_result,
                    magfit_results=magfit_result,
                    log_path=str(log_file),
                )

                html_path = output_file if output_file else Path(log_file.stem + "_report.html")
                if html_path.suffix.lower() != ".html":
                    html_path = html_path.with_suffix(".html")

                save_html_report(html_out, str(html_path))
                progress.update(p_html, completed=True, description="[green]✓ HTML 报告已生成")
                _console.print(f"\n[bold green]✓[/bold green] HTML 报告已保存: [cyan]{html_path}[/cyan]")
            except Exception as exc:
                progress.update(p_html, completed=True,
                                description=f"[yellow]! HTML 报告失败: {exc}")
        else:
            # 终端 + 可选 Markdown 输出
            if pid_result is not None:
                fmt.format_pid(pid_result)
            if fft_result is not None:
                fmt.format_fft(fft_result)
            if magfit_result is not None:
                fmt.format_magfit(magfit_result)

            # ── Markdown 报告 ──
            if effective_report_format == "md" and output_file:
                md = fmt.to_markdown(full_result)
                output_file.write_text(md, encoding="utf-8")
                _console.print(f"\n[green]✓[/green] 报告已保存: [cyan]{output_file}[/cyan]")

        # ── 可视化图表 ─────────────────────────────────────────────────
        if visual:
            p_vis = progress.add_task("[cyan]生成图表...", total=None)
            try:
                fmt.generate_all_plots(pid_result, fft_result)
                progress.update(p_vis, completed=True, description="[green]✓ 图表已生成")
            except Exception as exc:
                progress.update(p_vis, completed=True,
                                description=f"[yellow]! 图表生成失败: {exc}")

    # ── 进度结束后：渲染失败信息 + 汇总 ─────────────────────────
    if module_failures:
        _console.print()
        for mod_name, exc in module_failures:
            _console.print(f"[bold yellow]! {mod_name} 已跳过:[/bold yellow]")
            if isinstance(exc, SmartTuneError):
                _print_error(exc)
            else:
                _console.print(f"  {exc}")

        succeeded = []
        if pid_result is not None:
            succeeded.append("PID")
        if fft_result is not None:
            succeeded.append("FFT")
        if magfit_result is not None:
            succeeded.append("磁力计")
        failed = [name for name, _ in module_failures]
        _console.print(
            f"\n[bold yellow]✓ 分析部分完成[/bold yellow] - "
            f"成功: {', '.join(succeeded) or '无'} | "
            f"失败: {', '.join(failed)}"
        )
    else:
        _console.print("\n[bold green]✓ 分析完成！[/bold green]")


# ---------------------------------------------------------------------------
# pid — PID 分析
# ---------------------------------------------------------------------------

@main.command()
@click.option("-i", "--input", "log_file", required=True,
              type=click.Path(exists=True, path_type=Path), help="飞行日志文件")
@click.option("--platform", "platform_name", default="auto",
              help="平台：auto 或 px4（默认：auto）")
@click.option("-a", "--axis", type=click.Choice(["roll", "pitch", "yaw", "all"],
              case_sensitive=False), default="all",
              help="要分析的轴（默认：all）")
@click.option("--visual/--no-visual", default=False,
              help="生成阶跃响应图表")
@click.option("--theme", type=click.Choice(["light", "dark"], case_sensitive=False),
              default="light", help="图表主题：light（默认）或 dark")
def pid(log_file: Path, platform_name: str, axis: str, visual: bool, theme: str):
    """PID 阶跃响应分析。

    \b
    从飞行数据中检测摇杆输入的阶跃响应并评估：
      · 上升时间、超调、调节时间、振荡次数
      · 逐轴诊断及调参建议

    \b
    示例：
      stune pid -i flight.ulg                  # 所有轴
      stune pid -i flight.ulg -a roll          # 仅横滚轴
      stune pid -i flight.ulg -a roll --visual # 横滚轴带图表
    """
    _run_single_analysis("pid", log_file, platform_name, axis, visual, theme=theme)


# ---------------------------------------------------------------------------
# fft — FFT 分析
# ---------------------------------------------------------------------------

@main.command()
@click.option("-i", "--input", "log_file", required=True,
              type=click.Path(exists=True, path_type=Path), help="飞行日志文件")
@click.option("--platform", "platform_name", default="auto",
              help="平台：auto 或 px4（默认：auto）")
@click.option("--visual/--no-visual", default=False,
              help="生成 FFT 频谱图")
@click.option("--theme", type=click.Choice(["light", "dark"], case_sensitive=False),
              default="light", help="图表主题：light（默认）或 dark")
def fft(log_file: Path, platform_name: str, visual: bool, theme: str):
    """FFT 振动频谱分析。

    \b
    分析陀螺仪数据以识别振动频率，并建议：
      · 振动严重程度评级（EXCELLENT/GOOD/MARGINAL/POOR）
      · 陷波滤波器参数（IMU_GYRO_NF0_FRQ, IMU_GYRO_NF0_BW）

    \b
    示例：
      stune fft -i flight.ulg          # 基本分析
      stune fft -i flight.ulg --visual # 带频谱图
    """
    _run_single_analysis("fft", log_file, platform_name, "all", visual, theme=theme)


# ---------------------------------------------------------------------------
# magfit — 磁力计
# ---------------------------------------------------------------------------

@main.command()
@click.option("-i", "--input", "log_file", required=True,
              type=click.Path(exists=True, path_type=Path), help="飞行日志文件")
@click.option("--platform", "platform_name", default="auto",
              help="平台：auto 或 px4（默认：auto）")
def magfit(log_file: Path, platform_name: str):
    """磁力计校准分析。

    \b
    评估罗盘校准质量：
      · 拟合度评分（mGauss）— 越低越好
      · 硬铁 / 软铁干扰诊断
      · 飞行覆盖范围检查（偏航/俯仰/横滚范围）

    \b
    示例：
      stune magfit -i flight.ulg
    """
    _run_single_analysis("magfit", log_file, platform_name, "all", False)


# ---------------------------------------------------------------------------
# sysid — 系统辨识
# ---------------------------------------------------------------------------

@main.command()
@click.option("-i", "--input", "log_file", required=True,
              type=click.Path(exists=True, path_type=Path), help="飞行日志文件")
@click.option("--platform", "platform_name", default="auto",
              help="平台：auto 或 px4（默认：auto）")
@click.option("-a", "--axis", type=click.Choice(["roll", "pitch", "yaw", "all"],
              case_sensitive=False), default="all",
              help="要分析的轴（默认：all）")
@click.option("--na", type=int, default=3, help="ARX 模型 A 多项式阶数（默认：3）")
@click.option("--nb", type=int, default=2, help="ARX 模型 B 多项式阶数（默认：2）")
def sysid(log_file: Path, platform_name: str, axis: str, na: int, nb: int):
    """系统辨识 — ARX 模型参数估计。

    \b
    从飞行数据估计传递函数：
      · 自然频率、阻尼比、时间常数
      · PID 带宽建议

    \b
    示例：
      stune sysid -i flight.ulg                  # 所有轴（na=3, nb=2）
      stune sysid -i flight.ulg -a roll --na 4   # 自定义 ARX 阶数
    """
    _run_single_analysis("sysid", log_file, platform_name, axis, False, na=na, nb=nb)


# ---------------------------------------------------------------------------
# hardware — 硬件报告
# ---------------------------------------------------------------------------

@main.command()
@click.option("-i", "--input", "log_file", required=True,
              type=click.Path(exists=True, path_type=Path), help="飞行日志文件")
@click.option("--platform", "platform_name", default="auto",
              help="平台：auto 或 px4（默认：auto）")
def hardware(log_file: Path, platform_name: str):
    """硬件配置报告。

    \b
    显示传感器配置和活动参数：
      · IMU 设置（陀螺仪/加速度计 ID、校准状态）
      · 罗盘配置
      · 活动滤波器设置
      · 角速率 PID 参数

    \b
    示例：
      stune hardware -i flight.ulg
    """
    _run_single_analysis("hardware", log_file, platform_name, "all", False)


# ---------------------------------------------------------------------------
# filter — 滤波器传递函数分析 (#8)  [D1 修复：现在调用 services 层]
# ---------------------------------------------------------------------------

@main.command("filter")
@click.option("-i", "--input", "log_file", required=True,
              type=click.Path(exists=True, path_type=Path), help="飞行日志文件")
@click.option("--platform", "platform_name", default="auto",
              help="平台：auto 或 px4（默认：auto）")
@click.option("--gyro-filter", type=float, default=None,
              help="覆盖 GYRO_FILTER 截止频率（Hz）")
@click.option("--notch-freq", type=float, default=None,
              help="指定陷波中心频率（Hz）")
@click.option("--auto/--no-auto", default=True,
              help="从日志参数自动推导滤波器配置（默认：开启）")
@click.option("--visual/--no-visual", default=False, help="生成波特图可视化")
@click.option("--theme", type=click.Choice(["light", "dark"], case_sensitive=False),
              default="light", help="图表主题：light（默认）或 dark")
def filter_cmd(log_file: Path, platform_name: str, gyro_filter: Optional[float],
               notch_freq: Optional[float], auto: bool, visual: bool, theme: str):
    """滤波器传递函数分析（波特图）。

    \b
    两种模式：
      - 自动模式（默认）：从日志参数推导滤波器配置
        （PX4 使用 IMU_GYRO_CUTOFF / IMU_GYRO_NF0_FRQ 等参数）
      - 手动模式：直接指定 --gyro-filter/--notch-freq

    \b
    示例：
      stune filter -i flight.ulg                         # 自动推导
      stune filter -i flight.ulg --no-auto --gyro-filter 20 --visual
      stune filter -i flight.ulg --notch-freq 80 --visual
    """
    from smarttune.services.analysis import analyze_filter  # D1 修复：调用 services 层

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=_console,
        transient=True,
    ) as progress:
        task = progress.add_task("[cyan]分析滤波器...", total=None)
        try:
            result = analyze_filter(
                log_file,
                platform=platform_name,
                gyro_filter_hz=gyro_filter,
                notch_freq_hz=notch_freq,
                auto_derive=auto,
                _include_bode_data=visual,
            )
            progress.update(task, completed=True, description="[green]✓ 滤波器分析完成")
        except SmartTuneError as exc:
            progress.stop()
            _print_error(exc)
            sys.exit(1)

    # ── 文本报告 ──
    _console.print(Panel("滤波器传递函数分析", style="bold cyan"))
    mode_label = "[yellow]手动[/yellow]" if result["mode"] == "manual" else "[green]自动推导[/green]"
    _console.print(f"\n[bold]模式:[/bold] {mode_label}")
    _console.print(f"[bold]配置:[/bold] {result['config_summary']}")

    key_pts = result.get("key_frequency_response", [])
    if key_pts:
        table = Table(title="关键频率响应")
        table.add_column("频率 (Hz)", style="cyan")
        table.add_column("幅度 (dB)", justify="right")
        table.add_column("相位 (°)", justify="right")
        for pt in key_pts:
            table.add_row(
                str(pt["frequency_hz"]),
                f"{pt['magnitude_db']:.1f}",
                f"{pt['phase_deg']:.1f}",
            )
        _console.print(table)

    if result.get("cutoff_3db_hz"):
        _console.print(f"\n[yellow]-3dB 截止频率 ≈ {result['cutoff_3db_hz']:.1f} Hz[/yellow]")

    filter_chain = result.get("filter_chain")
    if filter_chain and result["mode"] == "auto":
        _console.print("\n[bold]滤波器链:[/bold]")
        for line in filter_chain:
            _console.print(line)

    # ── 可视化 ──
    if visual:
        bode = result.get("bode_data")
        if bode:
            try:
                import numpy as np
                from smarttune.output.filter_visualization import plot_bode
                out_path = Path.cwd() / "output"
                out_path.mkdir(parents=True, exist_ok=True)
                img_path = out_path / "filter_bode.png"
                plot_bode(
                    np.array(bode["freqs"]),
                    np.array(bode["magnitude_db"]),
                    np.array(bode["phase_deg"]),
                    str(img_path),
                    title=f"滤波器波特图 ({result['config_summary']})",
                )
                _console.print(f"\n[green]✓[/green] 波特图已保存: {img_path}")
            except Exception as exc:
                _console.print(f"[yellow]可视化失败: {exc}[/yellow]")

    _console.print("\n[bold green]✓ 滤波器分析完成！[/bold green]")


# ---------------------------------------------------------------------------
# quality — 日志质量评分 (#9)  [B3 修复：调用 services 层，移除重复逻辑]
# ---------------------------------------------------------------------------

@main.command()
@click.option("-i", "--input", "log_file", required=True,
              type=click.Path(exists=True, path_type=Path), help="飞行日志文件")
@click.option("--platform", "platform_name", default="auto",
              help="平台：auto 或 px4（默认：auto）")
@click.option("-o", "--output", "output_file", type=click.Path(path_type=Path),
              default=None, help="输出质量报告文件（可选）")
def quality(log_file: Path, platform_name: str, output_file: Optional[Path]):
    """评估日志质量 — 数据完整性、激励和采样率评分。

    \b
    评估维度：
      · 数据完整性 — 关键消息类型是否齐全
      · 时长          — 是否足够进行分析
      · 激励        — 是否有足够的 PID 阶跃响应窗口
      · 采样率       — RATE/IMU 采样一致性与丢包率

    \b
    示例：
      stune quality -i flight.ulg
      stune quality -i flight.ulg -o quality_report.txt
    """
    from smarttune.services.analysis import get_log_quality

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=_console,
        transient=True,
    ) as progress:
        task = progress.add_task("[cyan]评估日志质量...", total=None)
        try:
            result = get_log_quality(log_file, platform=platform_name)
            progress.update(task, completed=True, description="[green]✓ 质量评估完成")
        except SmartTuneError as exc:
            progress.stop()
            _print_error(exc)
            sys.exit(1)

    q = result["quality"]
    score = q["score"]
    rating = q["rating"]
    advice = q["advice"]
    duration_s = result["duration_s"]
    duration_min = duration_s / 60.0

    if score >= 90:
        ov_color = "bold green"
    elif score >= 75:
        ov_color = "green"
    elif score >= 55:
        ov_color = "yellow"
    else:
        ov_color = "bold red"

    lines = [
        "=" * 60,
        "  日志质量报告",
        "=" * 60,
        f"  日志文件:  {result['log_file']}",
        f"  平台:      {result['display_name']}",
        f"  文件大小:  {result['file_size_mb']:.1f} MB",
        f"  时长:      {duration_min:.1f} 分钟 ({duration_s:.0f}s)",
        "",
        f"  评分: {score}/100  [{rating}]",
        "",
        "── 数据完整性 ────────────────────────────────────────────",
    ]

    for item in result.get("data_completeness", []):
        status = "✓" if item["ok"] else ("❌" if item["required"] else "⚠")
        lines.append(f"  {status} {item['name']:<15} {item['samples']:>8} 样本")

    step_counts = result.get("step_counts")
    if step_counts:
        lines += ["", "── 激励（阶跃窗口）─────────────────────────────────────"]
        for ax, cnt in step_counts.items():
            bar = "█" * min(cnt, 20) + "░" * max(0, 20 - cnt)
            qual = "✓" if cnt >= 5 else ("⚠" if cnt >= 2 else "❌")
            lines.append(f"  {qual} {ax.capitalize():<8} {bar} {cnt:>3}")

    rate_consistency = result.get("rate_consistency")
    if rate_consistency:
        lines += ["", "── 采样率一致性 ─────────────────────────────────────────"]
        for rc in rate_consistency:
            lines.append(
                f"  {rc['source']:<12} 采样率: {rc['sample_rate_hz']:.0f} Hz  "
                f"抖动: {rc['jitter_percent']:.1f}%  丢包: {rc['drop_rate_percent']:.1f}%"
            )

    issues = result.get("validation_issues", [])
    if issues:
        lines += ["", "── 验证问题 ──────────────────────────────────────────────"]
        for issue in issues:
            lines.append(f"  ⚠ {issue}")

    lines += [
        "",
        "=" * 60,
        f"  建议: {advice}",
        "=" * 60,
    ]

    report_text = "\n".join(lines)

    _console.print(f"\n[{ov_color}]评分: {score}/100 [{rating}][/{ov_color}]")
    for line in lines:
        _console.print(line)

    if output_file:
        output_file.write_text(report_text + "\n", encoding="utf-8")
        _console.print(f"\n[green]✓[/green] 质量报告已保存: [cyan]{output_file}[/cyan]")


# ---------------------------------------------------------------------------
# 通用单项分析流程
# ---------------------------------------------------------------------------

def _run_single_analysis(capability: str, log_file: Path, platform_name: str,
                         axis: str, visual: bool, theme: str = "light",
                         na: int = 3, nb: int = 2):
    try:
        adapter = resolve_adapter(platform_name, log_file)
    except SmartTuneError as exc:
        _print_error(exc)
        sys.exit(1)

    if capability not in adapter.capabilities():
        from smarttune.errors import CapabilityNotSupportedError
        _print_error(CapabilityNotSupportedError(
            message=f"{adapter.display_name} 不支持 '{capability}'",
            hint=f"支持: {', '.join(sorted(adapter.capabilities()))}",
        ))
        sys.exit(1)

    _console.print(f"[cyan]平台:[/cyan] {adapter.display_name}")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=_console,
        transient=True,
    ) as progress:
        task = progress.add_task("[cyan]解析日志...", total=None)
        try:
            flight_data = adapter.parse(log_file)
            progress.update(task, completed=True, description="[green]✓ 日志已解析")
        except SmartTuneError as exc:
            _fail_in_progress(progress, exc)

        task2 = progress.add_task(f"[cyan]{capability.upper()} 分析中...", total=None)
        from smarttune.knowledge import KnowledgeBase
        from smarttune.output.formatter import OutputFormatter
        from smarttune.services.analysis import run_module  # A1 修复：共享接线
        kb = KnowledgeBase(platform=adapter.name)
        fmt = OutputFormatter(adapter=adapter, theme=theme)

        try:
            if capability == "pid":
                pid_result = run_module("pid", adapter, flight_data, kb=kb, axis=axis)
                progress.update(task2, completed=True, description=f"[green]✓ {capability.upper()} 分析完成")
                fmt.format_pid(pid_result, visual=visual)
            elif capability == "fft":
                fft_result = run_module("fft", adapter, flight_data, kb=kb)
                progress.update(task2, completed=True, description=f"[green]✓ {capability.upper()} 分析完成")
                fmt.format_fft(fft_result, visual=visual)
            elif capability == "magfit":
                result = run_module("magfit", adapter, flight_data, kb=kb)
                progress.update(task2, completed=True, description=f"[green]✓ {capability.upper()} 分析完成")
                fmt.format_magfit(result)
            elif capability == "sysid":
                # (既有 bug 修复：--na/--nb 选项之前从未被传递，恒用默认阶数)
                results = run_module("sysid", adapter, flight_data, kb=kb, axis=axis, na=na, nb=nb)
                progress.update(task2, completed=True, description=f"[green]✓ {capability.upper()} 分析完成")
                fmt.format_sysid(results)
            elif capability == "hardware":
                report = run_module("hardware", adapter, flight_data, kb=kb)
                progress.update(task2, completed=True, description=f"[green]✓ {capability.upper()} 分析完成")
                fmt.format_hardware(report, visual=visual)
            else:
                progress.update(task2, completed=True,
                                description=f"[yellow]{capability} 集成待完善")
        except SmartTuneError as exc:
            _fail_in_progress(progress, exc)

    _console.print(f"\n[bold green]✓ {capability.upper()} 分析完成[/bold green]")


if __name__ == "__main__":
    main()
