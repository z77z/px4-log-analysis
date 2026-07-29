# -*- coding: utf-8 -*-
"""
PX4 ULog 飞行日志摘要提取脚本
用法: python px4_log_summary.py <flight.ulg> [输出文件.txt]

输出结构化摘要, 供 AI 进一步判读并生成飞行分析与调参建议。
依赖: pip install pyulog numpy
"""
import sys
import io
import re
import inspect

import numpy as np
from pyulog import ULog

# 输出 utf-8, 避免 Windows 控制台乱码
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 缓存 pyulog get_dataset 签名, 避免每次调用都执行 inspect.signature
_GET_DATASET_HAS_MULTI_INSTANCE = None


def get_data(ulog, topic, multi_id=0):
    """安全获取数据集, 不存在返回 None"""
    global _GET_DATASET_HAS_MULTI_INSTANCE
    if _GET_DATASET_HAS_MULTI_INSTANCE is None:
        sig = inspect.signature(ulog.get_dataset)
        _GET_DATASET_HAS_MULTI_INSTANCE = "multi_instance" in sig.parameters
    try:
        if _GET_DATASET_HAS_MULTI_INSTANCE:
            d = ulog.get_dataset(topic, multi_instance=multi_id)
        else:
            d = ulog.get_dataset(topic)
        return d.data
    except Exception:
        return None


def ts(data):
    """时间戳转相对秒"""
    t = np.asarray(data["timestamp"], dtype=np.float64)
    return (t - t[0]) / 1e6


# MAV_TYPE 分类
VTOL_TYPES = {19, 20, 21, 22, 23, 24}      # 各类垂起固定翼
MC_TYPES = {2, 3, 4, 13, 14, 15}           # 四/六/八旋翼、直升机等旋翼类
FW_TYPE = 1                                # 固定翼


def vehicle_kind(ulog):
    """判断机型: 返回 (kind, mav_type), kind ∈ {fixed_wing, vtol, multicopter, unknown}"""
    mav_type = ulog.initial_parameters.get("MAV_TYPE")
    if mav_type is not None:
        mav_type = int(mav_type)
        if mav_type == FW_TYPE:
            return "fixed_wing", mav_type
        if mav_type in VTOL_TYPES:
            return "vtol", mav_type
        if mav_type in MC_TYPES:
            return "multicopter", mav_type
    # 参数缺失时按数据集推断
    try:
        ulog.get_dataset("vtol_vehicle_status")
        return "vtol", mav_type
    except Exception:
        pass
    return "unknown", mav_type


def state_at(t_src, state, t_dst):
    """把离散状态量按阶梯(最近前值)映射到目标时间轴"""
    idx = np.searchsorted(t_src, t_dst, side="right") - 1
    idx = np.clip(idx, 0, len(state) - 1)
    return np.asarray(state)[idx]


def rms(x):
    x = np.asarray(x, dtype=np.float64)
    return float(np.sqrt(np.mean(x ** 2))) if len(x) else float("nan")


def section(title):
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def main(path):
    ulog = ULog(path)

    # ---------- 1. 基础信息 ----------
    section("1. 平台与日志基础信息")
    SKIP_KEYS = ("metadata_", "perf_", "boot_console_output", "excluded_optional_topics")
    for k, v in sorted(ulog.msg_info_dict.items()):
        if any(k.startswith(s) or s in k for s in SKIP_KEYS):
            continue
        s = str(v)
        print(f"  {k}: {s[:200]}{'...' if len(s) > 200 else ''}")
    for k, v in sorted(ulog.msg_info_multiple_dict.items()):
        if any(k.startswith(s) or s in k for s in SKIP_KEYS):
            continue
        s = str(v)
        print(f"  {k}: {s[:200]}{'...' if len(s) > 200 else ''}")
    t0, t1 = ulog.start_timestamp / 1e6, ulog.last_timestamp / 1e6
    print(f"  日志时长: {t1 - t0:.1f} s")
    kind, mav_type = vehicle_kind(ulog)
    KIND_CN = {"fixed_wing": "固定翼", "vtol": "垂起固定翼(VTOL)",
               "multicopter": "旋翼(多旋翼/直升机)", "unknown": "未知"}
    print(f"  机型: {KIND_CN[kind]} (MAV_TYPE={mav_type})")
    if ulog.dropouts:
        durs = [d.duration for d in ulog.dropouts]
        print(f"  Dropouts: {len(durs)} 次, 总时长 {sum(durs):.0f} ms, 最大 {max(durs)} ms")
    else:
        print("  Dropouts: 无")
    print("  已记录话题数:", len(ulog.data_list))

    # ---------- 2. 日志消息(事件/错误) ----------
    section("2. 日志消息与事件 (INFO/WARN/ERROR)")
    msgs = ulog.logged_messages
    if not msgs:
        print("  (无)")
    LVLS = ("EMERG", "ALERT", "CRIT", "ERR", "WARN", "NOTICE", "INFO", "DEBUG")
    for m in msgs:
        lvl = getattr(m, "log_level", 7)
        if isinstance(lvl, int) and 48 <= lvl <= 55:  # 旧格式为 ASCII '0'-'7'
            lvl -= 48
        name = LVLS[lvl] if isinstance(lvl, int) and 0 <= lvl < 8 else str(lvl)
        t = m.timestamp / 1e6
        print(f"  [{t:8.1f}s] [{name:6}] {m.message.strip()}")

    # ---------- 3. 飞行模式时间线 ----------
    section("3. 飞行模式 / 状态时间线")
    d = get_data(ulog, "vehicle_status")
    if d:
        t = ts(d)
        nav = d["nav_state"]
        arm = d["arming_state"]
        last_nav, last_arm = None, None
        # vehicle_status.nav_state 枚举 (v1.14 及以后; 7-9/11/16 为空槽位, 23+ 为 EXTERNAL)
        NAV = {0: "MANUAL", 1: "ALTCTL", 2: "POSCTL", 3: "AUTO_MISSION", 4: "AUTO_LOITER",
                5: "AUTO_RTL", 6: "POSITION_SLOW", 10: "ACRO", 12: "DESCEND", 13: "TERMINATION",
                14: "OFFBOARD", 15: "STAB", 17: "AUTO_TAKEOFF", 18: "AUTO_LAND",
                19: "AUTO_FOLLOW_TARGET", 20: "AUTO_PRECLAND", 21: "ORBIT",
                22: "AUTO_VTOL_TAKEOFF", 23: "EXTERNAL1", 24: "EXTERNAL2"}
        for i in range(len(t)):
            n, a = int(nav[i]), int(arm[i])
            if n != last_nav or a != last_arm:
                print(f"  {t[i]:8.1f}s  nav={NAV.get(n, n)!s:<16} arming={a}")
                last_nav, last_arm = n, a

    # ---------- 4. 振动水平 ----------
    section("4. 振动水平 (sensor_combined / IMU)")
    d = get_data(ulog, "sensor_combined")
    acc_keys = ("accelerometer_m_s2[0]", "accelerometer_m_s2[1]", "accelerometer_m_s2[2]")
    if d is None:
        d = get_data(ulog, "sensor_accel")
        acc_keys = ("x", "y", "z")  # sensor_accel 的加速度字段为 x/y/z
    if d:
        ax = np.asarray(d.get(acc_keys[0], []), dtype=np.float64)
        ay = np.asarray(d.get(acc_keys[1], []), dtype=np.float64)
        az = np.asarray(d.get(acc_keys[2], []), dtype=np.float64)
        if len(ax):
            # 高通近似: 去趋势后看波动幅度
            for name, a in (("X", ax), ("Y", ay), ("Z", az)):
                # 去趋势: 用滑动均值, 裁剪边缘避免卷积 padding 效应
                kernel = np.ones(50) / 50
                trend = np.convolve(a, kernel, mode="valid")
                pad = (len(a) - len(trend)) // 2
                dev = a[pad:pad + len(trend)] - trend
                print(f"  加速度{name}: std={np.std(dev):.2f} m/s^2, 峰峰值(去趋势)={np.ptp(dev):.1f} m/s^2")
            print("  判读参考: 高频波动 std < 1 优秀; 1-3 可接受; >3 偏大; 去趋势峰峰值持续 >30 可能 clipping")
    d = get_data(ulog, "sensor_gyro")
    if d:
        try:
            gx = np.asarray(d.get("x", d.get("gyro_rad[0]", [])), dtype=np.float64)  # sensor_gyro 字段为 x/y/z
            if len(gx):
                for name, key, idx in (("X", "x", 0), ("Y", "y", 1), ("Z", "z", 2)):
                    g = np.asarray(d.get(key, d.get(f"gyro_rad[{idx}]", [])), dtype=np.float64)
                    if len(g) == 0:
                        continue
                    kernel = np.ones(50) / 50
                    trend = np.convolve(g, kernel, mode="valid")
                    pad = (len(g) - len(trend)) // 2
                    dev_g = g[pad:pad + len(trend)] - trend
                    print(f"  陀螺仪{name}: std={np.std(dev_g):.3f} rad/s")

        except Exception:
            pass
    # clipping 计数
    d = get_data(ulog, "sensor_accel_status") or get_data(ulog, "vehicle_imu_status")
    if d:
        for key in d.keys():
            if "clipping" in key:
                print(f"  {key}: 末值 {d[key][-1]}")

    # ---------- 5. 角速率与姿态跟踪 ----------
    section("5. 角速率与姿态跟踪质量")
    sp = get_data(ulog, "vehicle_rates_setpoint")
    if sp is not None:
        # 角速率跟踪: 重采样到同一时基
        try:
            roll_sp = np.asarray(sp.get("roll", sp.get("roll_body", [])), dtype=np.float64)
            pitch_sp = np.asarray(sp.get("pitch", sp.get("pitch_body", [])), dtype=np.float64)
            yaw_sp = np.asarray(sp.get("yaw", sp.get("yaw_body", [])), dtype=np.float64)
            est = get_data(ulog, "vehicle_angular_velocity")
            if est is not None and len(roll_sp):
                ek = est.keys()
                r = np.asarray(est.get("xyz[0]", est.get("rollspeed", [])), dtype=np.float64)
                p = np.asarray(est.get("xyz[1]", est.get("pitchspeed", [])), dtype=np.float64)
                y = np.asarray(est.get("xyz[2]", est.get("yawspeed", [])), dtype=np.float64)
                t_est = ts(est)
                t_sp_abs = np.asarray(sp["timestamp"], dtype=np.float64)
                t_est_abs = np.asarray(est["timestamp"], dtype=np.float64)
                for axis, s, e in (("Roll", roll_sp, r), ("Pitch", pitch_sp, p), ("Yaw", yaw_sp, y)):
                    if len(s) and len(e):
                        e_i = np.interp(t_sp_abs, t_est_abs, e)
                        err = e_i - s
                        print(f"  {axis} 角速率跟踪: RMS误差={rms(err):.3f} rad/s ({np.degrees(rms(err)):.1f} deg/s), "
                                f"平均偏差={np.mean(err):+.3f}, 设定值RMS={rms(s):.3f}")
                print("  判读参考: 跟踪 RMS 误差应远小于设定值 RMS; 平均偏差大=积分/配平问题; "
                        "误差随设定值增大=P/D 不足或过驱")
        except Exception as ex:
            print("  角速率跟踪计算失败:", ex)

    # 姿态跟踪: vehicle_attitude_setpoint vs vehicle_attitude
    att_sp = get_data(ulog, "vehicle_attitude_setpoint")
    att = get_data(ulog, "vehicle_attitude")
    if att_sp is not None and att is not None:
        try:
            sp_roll = np.asarray(att_sp.get("roll_body", att_sp.get("roll", [])), dtype=np.float64)
            sp_pitch = np.asarray(att_sp.get("pitch_body", att_sp.get("pitch", [])), dtype=np.float64)
            sp_yaw = np.asarray(att_sp.get("yaw_body", att_sp.get("yaw", [])), dtype=np.float64)
            # vehicle_attitude 可能有 roll/pitch/yaw 字段, 或只有四元数 q[0..3]
            if "roll" in att and "pitch" in att and "yaw" in att:
                est_roll = np.asarray(att["roll"], dtype=np.float64)
                est_pitch = np.asarray(att["pitch"], dtype=np.float64)
                est_yaw = np.asarray(att["yaw"], dtype=np.float64)
            else:
                # 四元数转欧拉角 (PX4: q=[w,x,y,z])
                qw = np.asarray(att.get("q[0]", []), dtype=np.float64)
                qx = np.asarray(att.get("q[1]", []), dtype=np.float64)
                qy = np.asarray(att.get("q[2]", []), dtype=np.float64)
                qz = np.asarray(att.get("q[3]", []), dtype=np.float64)
                # 归一化
                norm = np.sqrt(qw**2 + qx**2 + qy**2 + qz**2)
                norm[norm == 0] = 1.0
                qw, qx, qy, qz = qw / norm, qx / norm, qy / norm, qz / norm
                # 四元数转欧拉角 (ZYX 顺序, 即 yaw-pitch-roll)
                est_roll = np.arctan2(2.0 * (qw * qx + qy * qz), 1.0 - 2.0 * (qx**2 + qy**2))
                est_pitch = np.arcsin(np.clip(2.0 * (qw * qy - qz * qx), -1.0, 1.0))
                est_yaw = np.arctan2(2.0 * (qw * qz + qx * qy), 1.0 - 2.0 * (qy**2 + qz**2))
            if len(sp_roll) and len(est_roll):
                t_sp_abs = np.asarray(att_sp["timestamp"], dtype=np.float64)
                t_est_abs = np.asarray(att["timestamp"], dtype=np.float64)
                for axis, s, e in (("Roll", sp_roll, est_roll), ("Pitch", sp_pitch, est_pitch), ("Yaw", sp_yaw, est_yaw)):
                    if len(s) and len(e):
                        e_i = np.interp(t_sp_abs, t_est_abs, e)
                        err = e_i - s
                        print(f"  {axis} 姿态跟踪: RMS误差={rms(err):.3f} rad ({np.degrees(rms(err)):.1f} deg), "
                                f"平均偏差={np.degrees(np.mean(err)):+.2f} deg, 设定值RMS={np.degrees(rms(s)):.1f} deg")
                print("  判读参考: 姿态RMS误差 <2 deg 优秀; 2-5 可接受; >5 偏大; 偏差大=外环P/配平问题")
        except Exception as ex:
            print("  姿态跟踪计算失败:", ex)

    # ---------- 6. 执行器输出饱和度 ----------
    section("6. 执行器输出 (actuator_outputs) 饱和度")
    d = get_data(ulog, "actuator_outputs")
    if d:
        outs = [k for k in d.keys() if k.startswith("output[")]
        n_sat_hi, n_total = 0, 0
        for k in outs:
            v = np.asarray(d[k], dtype=np.float64)
            if np.ptp(v) < 1:  # 未使用通道
                continue
            mx = np.nanmax(v)
            n_total += 1
            # PWM 输出范围一般 1000-2000, 归一化输出 0-1
            hi_thr = 1950 if mx > 100 else 0.97
            sat = np.mean(v > hi_thr) * 100
            if sat > 0.5:
                n_sat_hi += 1
                print(f"  {k}: max={mx:.0f}, 高端饱和时间占比 {sat:.1f}%  <-- 偏高")
        print(f"  使用通道数: {n_total}, 存在明显饱和的通道数: {n_sat_hi}")
        print("  判读参考: 持续饱和=推力余量不足/电机过驱, 会限制姿态控制能力")

    # ---------- 7. 电池 / 电源 ----------
    section("7. 电池与电源")
    d = get_data(ulog, "battery_status")
    if d:
        v = np.asarray(d.get("voltage_v", d.get("voltage_filtered_v", [])), dtype=np.float64)
        c = np.asarray(d.get("current_a", []), dtype=np.float64)
        rem = np.asarray(d.get("remaining", []), dtype=np.float64)
        t_bat = np.asarray(d["timestamp"], dtype=np.float64)
        if len(v):
            print(f"  电压: 起始 {v[0]:.2f} V, 最低 {np.nanmin(v):.2f} V, 结束 {v[-1]:.2f} V, "
                  f"最大跌落 {v[0] - np.nanmin(v):.2f} V")
        n_cells = ulog.initial_parameters.get("BAT_N_CELLS") or ulog.initial_parameters.get("BAT1_N_CELLS")
        if n_cells and len(v):
            n_cells = int(n_cells)
            print(f"  单芯电压({n_cells}S): 起始 {v[0]/n_cells:.2f} V, 最低 {np.nanmin(v)/n_cells:.2f} V, "
                  f"结束 {v[-1]/n_cells:.2f} V")
        if len(c):
            if np.nanmax(c) > 0:
                print(f"  电流: 平均 {np.nanmean(c):.1f} A, 峰值 {np.nanmax(c):.1f} A")
            else:
                print("  电流: 0 A (可能未接电流传感器, 功率统计不可用)")
        mah = np.asarray(d.get("discharged_mah", []), dtype=np.float64)
        if len(mah):
            print(f"  消耗电量: {mah[-1] - mah[0]:.0f} mAh")
        if len(rem):
            print(f"  剩余电量: {rem[0]*100:.0f}% -> {rem[-1]*100:.0f}%")
        warn = np.asarray(d.get("warning", []), dtype=np.float64) if "warning" in d else None
        if warn is not None and np.nanmax(warn) > 0:
            print(f"  电池告警等级最高: {int(np.nanmax(warn))} (1=LOW 2=CRITICAL 3=EMERGENCY)")

        # ---- 功率与机型分项统计 ----
        has_current = len(c) and np.nanmax(c) > 0
        if len(v) and len(c) and len(v) == len(c):
            p = v * c  # 瞬时功率 W

            # 空中掩码 (landed_state: 0=UNDEFINED 1=ON_GROUND 2=IN_AIR 3=TAKEOFF 4=LANDING)
            airborne = np.ones(len(t_bat), dtype=bool)
            ld = get_data(ulog, "vehicle_land_detected")
            if ld is not None and "landed_state" in ld:
                airborne = state_at(np.asarray(ld["timestamp"], dtype=np.float64),
                                    np.asarray(ld["landed_state"], dtype=np.float64), t_bat) > 1
            if has_current:
                if np.any(airborne):
                    print(f"  功率(空中): 平均 {np.nanmean(p[airborne]):.0f} W, "
                          f"最大 {np.nanmax(p[airborne]):.0f} W")
                else:
                    airborne = np.ones(len(t_bat), dtype=bool)
                    print(f"  功率(全程): 平均 {np.nanmean(p):.0f} W, 最大 {np.nanmax(p):.0f} W")

            # 归一化油门序列 (悬停油门用)
            thr_bat = None
            ac = get_data(ulog, "actuator_controls_0")
            if ac is not None and "control[3]" in ac:
                thr_bat = np.interp(t_bat, np.asarray(ac["timestamp"], dtype=np.float64),
                                    np.asarray(ac["control[3]"], dtype=np.float64))
            else:
                mot = get_data(ulog, "actuator_motors")
                if mot is not None:
                    used = [np.asarray(mot[k], dtype=np.float64) for k in mot.keys()
                            if k.startswith("control[")
                            and np.ptp(np.asarray(mot[k], dtype=np.float64)) > 0.05]
                    if used:
                        thr_bat = np.interp(t_bat, np.asarray(mot["timestamp"], dtype=np.float64),
                                            np.mean(used, axis=0))

            # 水平速度 (悬停判定: 空中且水平速度 < 3 m/s)
            hover_base = airborne.copy()
            lp = get_data(ulog, "vehicle_local_position")
            if lp is not None and "vx" in lp and "vy" in lp:
                spd = np.hypot(np.asarray(lp["vx"], dtype=np.float64),
                               np.asarray(lp["vy"], dtype=np.float64))
                hover_base &= np.interp(t_bat, np.asarray(lp["timestamp"], dtype=np.float64), spd) < 3.0

            def seg_seconds(mask):
                tt = t_bat[mask]
                if len(tt) < 2:
                    return 0.0
                return float(np.sum(np.diff(tt))) / 1e6

            def cruise_stats(mask, label):
                if np.sum(mask) < 5:
                    print(f"  {label}: 样本不足")
                    return
                parts = [f"时长 {seg_seconds(mask):.0f} s"]
                if has_current:
                    parts.append(f"巡航平均功率 {np.nanmean(p[mask]):.0f} W")
                    parts.append(f"段内最大功率 {np.nanmax(p[mask]):.0f} W")
                    parts.append(f"平均电流 {np.nanmean(c[mask]):.1f} A")
                parts.append(f"平均电压 {np.nanmean(v[mask]):.2f} V")
                print(f"  {label}: " + ", ".join(parts))

            def hover_stats(mask, label):
                if np.sum(mask) < 5:
                    print(f"  {label}: 样本不足")
                    return
                parts = [f"时长 {seg_seconds(mask):.0f} s"]
                if has_current:
                    parts.append(f"悬停功率 {np.nanmean(p[mask]):.0f} W")
                    parts.append(f"平均电流 {np.nanmean(c[mask]):.1f} A")
                parts.append(f"平均电压 {np.nanmean(v[mask]):.2f} V")
                if thr_bat is not None:
                    parts.append(f"悬停油门 {np.nanmean(thr_bat[mask]):.2f} (归一化0~1)")
                print(f"  {label}: " + ", ".join(parts))

            vt = get_data(ulog, "vtol_vehicle_status")
            if kind == "vtol" and vt is not None and "vehicle_vtol_state" in vt:
                # vehicle_vtol_state: 0=UNDEFINED 1=TRANSITION_TO_FW 2=TRANSITION_TO_MC 3=MC 4=FW
                st = state_at(np.asarray(vt["timestamp"], dtype=np.float64),
                              np.asarray(vt["vehicle_vtol_state"], dtype=np.float64), t_bat)
                print("  -- 垂起固定翼分段功率 --")
                hover_stats(hover_base & (st == 3), "旋翼悬停段")
                cruise_stats(airborne & (st == 4), "固定翼巡航段")
            elif kind == "fixed_wing":
                cruise_stats(airborne, "固定翼巡航")
            elif kind == "multicopter":
                hover_stats(hover_base, "旋翼悬停")
            print("  判读参考: 悬停油门 <0.5 推力余量充足, 0.5~0.7 可接受, >0.7 余量不足; "
                  "巡航/悬停功率可用于估算续航")

    # ---------- 8. GPS 质量 ----------
    section("8. GPS / 定位质量")
    d = get_data(ulog, "vehicle_gps_position")
    if d:
        nsat = np.asarray(d.get("satellites_used", []), dtype=np.float64)
        eph = np.asarray(d.get("eph", []), dtype=np.float64)
        epv = np.asarray(d.get("epv", []), dtype=np.float64)
        fix = np.asarray(d.get("fix_type", []), dtype=np.float64)
        jam = np.asarray(d.get("jamming_indicator", []), dtype=np.float64)
        if len(fix):
            print(f"  Fix 类型: 最低 {int(np.nanmin(fix))} (3=3D, 4=RTCM差分, 5=RTK float, 6=RTK fixed)")
        if len(nsat):
            print(f"  卫星数: 最少 {int(np.nanmin(nsat))}, 平均 {np.nanmean(nsat):.0f}")
        if len(eph):
            print(f"  水平精度 EPH: 平均 {np.nanmean(eph):.2f} m, 最差 {np.nanmax(eph):.2f} m")
        if len(epv):
            print(f"  垂直精度 EPV: 平均 {np.nanmean(epv):.2f} m")
        if len(jam) and np.nanmax(jam) > 40:
            print(f"  干扰指示最大: {np.nanmax(jam):.0f} (>40 偏高, 注意 GPS 干扰)")
    d = get_data(ulog, "vehicle_local_position")
    if d:
        try:
            eph = np.asarray(d["eph"], dtype=np.float64)
            print(f"  EKF 水平位置不确定度: 平均 {np.nanmean(eph):.2f} m, 最大 {np.nanmax(eph):.2f} m")
        except Exception:
            pass

    # ---------- 9. EKF2 创新量 ----------
    section("9. EKF2 创新量 (ekf2_innovations / estimator_innovations)")
    d = get_data(ulog, "ekf2_innovations")
    if d is not None:
        checks = [
            ("vel_pos_innov[0]", "北向位置", 1.0), ("vel_pos_innov[1]", "东向位置", 1.0),
            ("vel_pos_innov[3]", "X速度", 1.0), ("vel_pos_innov[4]", "Y速度", 1.0),
            ("vel_pos_innov[5]", "Z速度", 0.5), ("mag_innov[0]", "磁力计X", 0.5),
        ]
    else:
        # v1.16+ 话题改名为 estimator_innovations, 字段名也不同
        d = get_data(ulog, "estimator_innovations")
        checks = [
            ("gps_hpos[0]", "北向位置", 1.0), ("gps_hpos[1]", "东向位置", 1.0),
            ("gps_vpos", "垂直位置", 1.0),
            ("gps_hvel[0]", "X速度", 1.0), ("gps_hvel[1]", "Y速度", 1.0),
            ("gps_vvel", "Z速度", 0.5), ("mag_field[0]", "磁力计X(Gauss)", 0.5),
        ] if d is not None else []
    if d:
        for k, label, thr in checks:
            if k in d:
                v = np.asarray(d[k], dtype=np.float64)
                r = rms(v)
                flag = "  <-- 偏大" if r > thr else ""
                print(f"  {label}: RMS={r:.3f}{flag}")
        print("  判读参考: 创新量持续偏大=估计器不信任该传感器(校准/干扰/振动问题)")
    else:
        print("  (无 ekf2_innovations / estimator_innovations 话题)")
    d = get_data(ulog, "estimator_status")
    if d:
        try:
            flags = np.asarray(d["filter_fault_flags"], dtype=np.float64)
            if np.nanmax(flags) > 0:
                print(f"  EKF filter_fault_flags 非零! 最大 {int(np.nanmax(flags))}")
        except Exception:
            pass

    # ---------- 10. 高度/速度概况 ----------
    section("10. 飞行剖面概况")
    d = get_data(ulog, "vehicle_local_position")
    if d:
        z = np.asarray(d.get("z", []), dtype=np.float64)
        vz = np.asarray(d.get("vz", []), dtype=np.float64)
        if len(z):
            print(f"  相对高度: 最高 {-np.nanmin(z):.1f} m")
        if len(vz):
            print(f"  垂直速度: 最大爬升 {-np.nanmin(vz):.1f} m/s, 最大下降 {np.nanmax(vz):.1f} m/s")
    d = get_data(ulog, "airspeed_validated") or get_data(ulog, "airspeed")
    if d:
        try:
            ias = np.asarray(d.get("calibrated_airspeed_m_s", d.get("indicated_airspeed_m_s", [])), dtype=np.float64)
            if len(ias):
                print(f"  空速: 平均 {np.nanmean(ias):.1f} m/s, 最大 {np.nanmax(ias):.1f} m/s")
        except Exception:
            pass

    # ---- 固定翼空速专项分析 (固定翼 / 垂起固定翼) ----
    if kind in ("fixed_wing", "vtol") and d:
        try:
            t_as = np.asarray(d["timestamp"], dtype=np.float64)
            ias = np.asarray(d.get("calibrated_airspeed_m_s",
                                   d.get("indicated_airspeed_m_s", [])), dtype=np.float64)
            if len(ias):
                # 固定翼飞行段掩码: 空中; VTOL 再限定 FW 巡航状态
                ld = get_data(ulog, "vehicle_land_detected")
                if ld is not None and "landed_state" in ld:
                    fw_mask = state_at(np.asarray(ld["timestamp"], dtype=np.float64),
                                       np.asarray(ld["landed_state"], dtype=np.float64), t_as) > 1
                else:
                    fw_mask = np.ones(len(t_as), dtype=bool)
                vt = get_data(ulog, "vtol_vehicle_status")
                if kind == "vtol" and vt is not None and "vehicle_vtol_state" in vt:
                    st = state_at(np.asarray(vt["timestamp"], dtype=np.float64),
                                  np.asarray(vt["vehicle_vtol_state"], dtype=np.float64), t_as)
                    fw_mask &= st == 4  # 4=FW 巡航
                # 剔除非物理/无效样本 (空速 < 0 或传感器标记无效)
                n_fw0 = int(np.sum(fw_mask))
                fw_mask &= ias >= 0
                val = np.asarray(d.get("airspeed_sensor_measurement_valid", []), dtype=np.float64)
                if len(val):
                    fw_mask &= val > 0.5
                    n_bad = n_fw0 - int(np.sum(fw_mask))
                    if n_fw0 and n_bad / n_fw0 > 0.01:
                        print(f"  空速计无效样本占比(巡航段): {n_bad / n_fw0 * 100:.1f}%  "
                              f"<-- 检查空速计/皮托管")
                fw = ias[fw_mask]
                if len(fw) >= 5:
                    p05 = float(np.percentile(fw, 5))  # 低速 5 分位, 抗离群
                    print(f"  -- 固定翼空速(巡航段, {len(fw)} 样本) --")
                    print(f"  指示/校准空速: 平均 {np.nanmean(fw):.1f} m/s, "
                          f"中位 {np.nanmedian(fw):.1f} m/s, 最大 {np.nanmax(fw):.1f} m/s, "
                          f"低速5分位 {p05:.1f} m/s")
                    # 真空速 (地速对比判断风)
                    tas = np.asarray(d.get("true_airspeed_m_s",
                                           d.get("calibrated_true_airspeed_m_s", [])), dtype=np.float64)
                    if len(tas):
                        print(f"  真空速: 巡航段平均 {np.nanmean(tas[fw_mask]):.1f} m/s")
                    # 与参数对比: 配平/最小/失速空速
                    prm = ulog.initial_parameters
                    trim = prm.get("FW_AIRSPD_TRIM")
                    fmin = prm.get("FW_AIRSPD_MIN")
                    stall = prm.get("FW_AIRSPD_STALL")
                    if trim is not None:
                        print(f"  参数基准: FW_AIRSPD_TRIM={trim} m/s"
                              + (f", FW_AIRSPD_MIN={fmin}" if fmin is not None else "")
                              + (f", FW_AIRSPD_STALL={stall}" if stall is not None else ""))
                        print(f"  巡航空速偏差(均值-配平): {np.nanmean(fw) - float(trim):+.1f} m/s")
                    stall_ref = float(stall) if stall else (float(fmin) * 0.8 if fmin else None)
                    if fmin is not None:
                        below = np.mean(fw < float(fmin)) * 100
                        flag = "  <-- 占比偏高, 查失速风险" if below > 5 else ""
                        print(f"  低于 FW_AIRSPD_MIN({fmin} m/s) 的巡航样本占比: {below:.1f}%{flag}")
                    if stall_ref:
                        margin = p05 - stall_ref
                        flag = "  <-- 失速裕度不足!" if margin < stall_ref * 0.1 else ""
                        print(f"  失速裕度(低速5分位-失速空速): {margin:.1f} m/s{flag}")
                    # TECS 空速跟踪 (设定值 vs 实际)
                    tecs = get_data(ulog, "tecs_status")
                    if tecs is not None:
                        sp = next((np.asarray(tecs[k], dtype=np.float64) for k in
                                   ("equivalent_airspeed_sp", "equivalent_airspeed_setpoint",
                                    "true_airspeed_sp", "true_airspeed_setpoint",
                                    "airspeed_sp") if k in tecs), None)
                        if sp is not None and len(sp):
                            sp_i = np.interp(t_as, np.asarray(tecs["timestamp"], dtype=np.float64), sp)
                            err = sp_i - ias
                            print(f"  TECS 空速跟踪(巡航段): RMS误差 {rms(err[fw_mask]):.2f} m/s, "
                                  f"平均偏差 {np.nanmean(err[fw_mask]):+.2f} m/s "
                                  f"(正值=飞得偏慢, 负值=飞得偏快)")
                            print("  判读参考: 持续偏慢且俯角大=推力/升力不足或配平空速偏高; "
                                  "误差大且振荡=TECS 时间常数/增益问题")
                        if "underspeed_ratio" in tecs:
                            us = np.asarray(tecs["underspeed_ratio"], dtype=np.float64)
                            us_m = np.interp(t_as, np.asarray(tecs["timestamp"], dtype=np.float64), us)
                            us_fw = us_m[fw_mask]
                            if len(us_fw):
                                print(f"  TECS 欠速比 underspeed_ratio(巡航段): 平均 {np.nanmean(us_fw):.3f}, "
                                      f"最大 {np.nanmax(us_fw):.3f} (>0 即欠速, 持续>0.1 需警惕)")
                else:
                    print("  固定翼巡航段空速样本不足(可能本次无 FW 巡航)")
        except Exception as ex:
            print("  固定翼空速分析失败:", ex)

    # ---------- 11. 参数 ----------
    section("11. 参数快照")
    prm = ulog.initial_parameters
    print("  初始参数数量:", len(prm))
    # 1) 调参核心前缀全量输出; EKF2_/SENS_/PWM_/BAT/CA_ 数量大, 见下方专项摘要;
    #    所有被省略的参数可用 pyulog 直接查 ulog.initial_parameters
    KEEP = ("MC_", "MPC_", "FW_", "TECS_", "NPFG_", "IMU_", "VT_",
            "SDLOG_", "CAL_AIR", "ASPD_", "GPS_")
    for k, v in sorted(prm.items()):
        if any(k.startswith(p) for p in KEEP):
            print(f"  {k} = {v}")

    # 2) EKF2_: 省略高级融合内部/静态学习限制等极少用于飞行诊断的子组
    EKF2_DROP = ("EKF2_ABIAS_", "EKF2_ABL_", "EKF2_ANGERR_", "EKF2_BCOEF_",
                 "EKF2_DECL_", "EKF2_DELAY_", "EKF2_EAS_", "EKF2_GBIAS_",
                 "EKF2_GND_", "EKF2_GRAV_", "EKF2_GSF_", "EKF2_HDG_",
                 "EKF2_HEAD_", "EKF2_MCOEF_", "EKF2_MIN_", "EKF2_MULTI_",
                 "EKF2_NOAID_", "EKF2_PCOEF_", "EKF2_PREDICT_", "EKF2_SEL_",
                 "EKF2_TAU_", "EKF2_TERR_")
    ekf2 = {k: v for k, v in prm.items() if k.startswith("EKF2_")}
    if ekf2:
        # 日志中没有对应传感器话题时, 其融合参数无诊断价值, 一并省略
        def _has_topic(name):
            try:
                return any(getattr(d, "name", "") == name for d in ulog.data_list)
            except Exception:
                return True  # 查询失败则保守保留
        ekf2_drop = list(EKF2_DROP)
        if not _has_topic("distance_sensor"):
            ekf2_drop.append("EKF2_RNG_")
        if not _has_topic("optical_flow"):
            ekf2_drop.append("EKF2_OF_")
        if not (_has_topic("vehicle_visual_odometry") or _has_topic("vehicle_mocap_odometry")):
            ekf2_drop += ("EKF2_EV_", "EKF2_EVA_", "EKF2_EVP_", "EKF2_EVV_")
        ekf2_drop = tuple(ekf2_drop)
        dropped = sum(1 for k in ekf2 if k.startswith(ekf2_drop))
        print(f"  -- EKF2_ 估计器参数 (省略 {dropped} 个高级/静态/无传感器项) --")
        for k, v in sorted(ekf2.items()):
            if not k.startswith(ekf2_drop):
                print(f"  {k} = {v}")

    # 3) 电池: BAT_/BAT1_ 全量; BAT2_/BAT3_ 等仅在日志含对应 battery_status 实例时输出
    bat_ids = {1}
    try:
        bat_ids |= {getattr(d, "multi_id", 0) + 1 for d in ulog.data_list
                    if getattr(d, "name", "") == "battery_status"}
    except Exception as ex:
        print(f"  (警告: 扫描 battery_status 实例失败: {ex}, 仅输出 BAT_/BAT1_ 参数)")
    for k, v in sorted(prm.items()):
        m = re.match(r"BAT(\d)_", k)
        if k.startswith("BAT_") or (m and int(m.group(1)) in bat_ids):
            print(f"  {k} = {v}")

    # 4) SENS_: 保留安装朝向/校准类; 使能位只输出非零项, 各测距仪驱动配置省略
    SENS_KEEP = ("SENS_BOARD_", "SENS_IMU_", "SENS_MAG_", "SENS_BARO_",
                 "SENS_DPRES_", "SENS_GPS_")
    for k, v in sorted(prm.items()):
        if k.startswith(SENS_KEEP) or (k.startswith("SENS_EN_") and v):
            print(f"  {k} = {v}")

    # 5) PWM_: 逐通道参数按「多数值+例外」聚合为一行; FAIL 全 -1 与 FUNC=0(禁用) 不输出
    pwm = {k: v for k, v in prm.items() if k.startswith("PWM_")}
    if pwm:
        print("  -- PWM_ 输出配置 (逐通道聚合) --")
        for k in sorted(k for k in pwm if not re.search(r"\d$", k)):
            print(f"  {k} = {pwm[k]}")
        chan = {}
        for k, v in pwm.items():
            m = re.match(r"(PWM_\w+?)(\d+)$", k)
            if m:
                chan.setdefault(m.group(1), {})[int(m.group(2))] = v
        for g, chans in sorted(chan.items()):
            if g.endswith("_FAIL") and all(v == -1 for v in chans.values()):
                continue
            if g.endswith("_FUNC"):
                act = {i: int(v) for i, v in sorted(chans.items()) if v}
                print(f"  {g}: " + (", ".join(f"ch{i}={v}" for i, v in act.items()) or "全部禁用"))
                continue
            vals = list(chans.values())
            maj = max(set(vals), key=vals.count)
            exc = {i: v for i, v in sorted(chans.items()) if v != maj}
            print(f"  {g} = {maj}" + ("" if not exc else "  例外: " +
                  ", ".join(f"ch{i}={v}" for i, v in exc.items())))

    # CA_ 控制分配: 每旋翼 8 个几何参数(PX/PY/PZ/AX/AY/AZ/CT/KM)是静态机架定义且含大量
    # 未启用槽位, 不逐条输出; 仅摘要构型/计数、非零 slew 与启用舵面的配平, 需要几何时用 pyulog 查
    ca = {k: v for k, v in prm.items() if k.startswith("CA_")}
    if ca:
        print("  -- CA_ 控制分配摘要 (旋翼/舵面几何参数已省略) --")
        for k in ("CA_AIRFRAME", "CA_METHOD", "CA_FAILURE_MODE", "CA_R_REV",
                  "CA_ROTOR_COUNT", "CA_SV_CS_COUNT"):
            if k in ca:
                print(f"  {k} = {ca[k]}")
        for k in sorted(ca):
            if k.endswith("_SLEW") and ca[k]:
                print(f"  {k} = {ca[k]}  (非零, 限制执行器响应速度)")
        n_cs = int(ca.get("CA_SV_CS_COUNT") or 0)
        for i in range(n_cs):
            p = f"CA_SV_CS{i}_"
            if p + "TYPE" in ca or p + "TRIM" in ca:
                line = f"  {p}TYPE = {ca.get(p + 'TYPE')}, {p}TRIM = {ca.get(p + 'TRIM')}"
                if ca.get(p + "FLAP"):
                    line += f", {p}FLAP = {ca[p + 'FLAP']}"
                if ca.get(p + "SPOIL"):
                    line += f", {p}SPOIL = {ca[p + 'SPOIL']}"
                print(line)
    if ulog.changed_parameters:
        print("  -- 飞行中修改的参数 --")
        for t, k, v in ulog.changed_parameters:
            print(f"  [{t/1e6:.1f}s] {k} = {v}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    _saved_stdout = sys.stdout
    _fh = None
    try:
        if len(sys.argv) > 2:
            _fh = open(sys.argv[2], "wb")
            sys.stdout = io.TextIOWrapper(_fh, encoding="utf-8", errors="replace")
        main(sys.argv[1])
    except FileNotFoundError:
        print(f"错误: 文件未找到: {sys.argv[1]}", file=_saved_stdout)
        sys.exit(1)
    except Exception as ex:
        print(f"错误: {ex}", file=_saved_stdout)
        sys.exit(1)
    finally:
        if _fh:
            sys.stdout.flush()
            sys.stdout = _saved_stdout
            _fh.close()
