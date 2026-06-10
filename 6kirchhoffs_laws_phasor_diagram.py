import math

import matplotlib.pyplot as plt
import numpy as np

from pbl_common import (
    MeasurementStopped,
    init_realtime_csv,
    emit_progress,
    clear_stop_request,
    check_stop_requested,
    append_csv_row,
    IMAGE_DIR,
    VECTOR_JSON,
    append_run_log,
    image_path,
    load_json,
    save_csv,
    save_representative_values,
    timestamp,
    frequency_tag,
    pbl_progress,
    check_stop_requested,
    clear_stop_request,
    raise_if_stop_requested,
    print_progress,
)

# 4 → 5 → 6 の順に実行してください。

# ============================
# 理論値用パラメータ
# ============================
R_THEORY = 56.0
L_THEORY = 2.8e-3
C_THEORY = 4.7e-6


def load_complex(data, name):
    return complex(data[f"{name}_real"], data[f"{name}_imag"])


def phase_deg(z):
    return math.degrees(math.atan2(z.imag, z.real))


def rotate_by_reference(z, ref):
    return z * np.exp(-1j * np.angle(ref))


def draw_phasor_diagram(ax, phasors, title):
    max_len = max(abs(z) for z in phasors.values())
    lim = 1.4 * max_len if max_len > 0 else 1

    for name, z in phasors.items():
        ax.arrow(
            0,
            0,
            z.real,
            z.imag,
            length_includes_head=True,
            head_width=0.04 * lim,
            head_length=0.06 * lim,
        )
        ax.text(1.08 * z.real, 1.08 * z.imag, name, fontsize=11)

    ax.axhline(0)
    ax.axvline(0)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)
    ax.set_xlabel("Real component [V rms]")
    ax.set_ylabel("Imaginary component [V rms]")
    ax.set_title(title)
    ax.grid(True)


def print_phasors(title, phasors):
    print(f"\n--- {title} ---")
    for name, z in phasors.items():
        print(
            f"{name:18s}: RMS = {abs(z):.6f} V, "
            f"phase = {phase_deg(z):.3f} deg, "
            f"real = {z.real:.6f}, imag = {z.imag:.6f}"
        )


def get_std(data, key):
    return data.get(key, "")


def to_float_or_nan(value):
    """数値に変換できない値は nan にする。"""
    try:
        if value == "":
            return float("nan")
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def format_pm(value, uncertainty, value_digits=4, uncertainty_digits=4):
    """値 ± 不確かさ の文字列を作る。不確かさが無い場合は値だけを返す。"""
    if uncertainty is None or not np.isfinite(uncertainty):
        return f"{value:.{value_digits}f}"
    return f"{value:.{value_digits}f} ± {uncertainty:.{uncertainty_digits}f}"


def estimate_sum_uncertainty(components):
    """
    複素フェーザの和の振幅・位相の標準不確かさを一次近似で求める。

    components は (z, u_rms, u_phase_deg) のリスト。
    u_rms または u_phase_deg が不足している成分は不確かさ0として扱う。
    """
    cov = np.zeros((2, 2), dtype=float)
    z_sum = sum(z for z, _, _ in components)

    for z, u_rms, u_phase_deg in components:
        amp = abs(z)
        phi = np.angle(z)
        u_amp = 0.0 if not np.isfinite(u_rms) else float(u_rms)
        u_phi = 0.0 if not np.isfinite(u_phase_deg) else math.radians(float(u_phase_deg))

        c = math.cos(phi)
        s = math.sin(phi)

        # z = A(cosφ + i sinφ) の A, φ に関する一次誤差伝播
        j = np.array(
            [
                [c, -amp * s],
                [s, amp * c],
            ],
            dtype=float,
        )
        cov_local = j @ np.diag([u_amp ** 2, u_phi ** 2]) @ j.T
        cov += cov_local

    amp_sum = abs(z_sum)
    if amp_sum <= 0:
        return float("nan"), float("nan")

    theta = np.angle(z_sum)
    grad_amp = np.array([math.cos(theta), math.sin(theta)], dtype=float)
    grad_phase = np.array([-math.sin(theta) / amp_sum, math.cos(theta) / amp_sum], dtype=float)

    u_amp_sum = math.sqrt(max(float(grad_amp @ cov @ grad_amp.T), 0.0))
    u_phase_sum = math.degrees(math.sqrt(max(float(grad_phase @ cov @ grad_phase.T), 0.0)))
    return u_amp_sum, u_phase_sum


def build_measured_annotation(phasors, std_map):
    """Measured Phasor Diagram内に表示する測定値と不確かさの文字列を作る。"""
    label_map = {
        r"$V_R$": "VR",
        r"$V_L$": "VL",
        r"$V_C$": "VC",
        r"$V_E$": "VE",
    }

    lines = ["Measured values (mean ± SD)"]
    for name in [r"$V_R$", r"$V_L$", r"$V_C$", r"$V_E$"]:
        z = phasors[name]
        rms_std, phase_std = std_map.get(name, ("", ""))
        rms_std = to_float_or_nan(rms_std)
        phase_std = to_float_or_nan(phase_std)

        rms_text = format_pm(abs(z), rms_std, value_digits=4, uncertainty_digits=4)
        phase_text = format_pm(phase_deg(z), phase_std, value_digits=2, uncertainty_digits=2)
        lines.append(f"{label_map[name]}: {rms_text} V, {phase_text}°")

    return "\n".join(lines)


def build_theory_annotation(phasors):
    """Theoretical Phasor Diagram内に表示する理論値の振幅と位相の文字列を作る。"""
    label_map = {
        r"$V_R$": "VR",
        r"$V_L$": "VL",
        r"$V_C$": "VC",
        r"$V_E$": "VE",
    }

    lines = ["Theoretical values"]
    for name in [r"$V_R$", r"$V_L$", r"$V_C$", r"$V_E$"]:
        z = phasors[name]
        lines.append(f"{label_map[name]}: {abs(z):.4f} V, {phase_deg(z):.2f}°")

    return "\n".join(lines)


def main():
    script_name = "6キルヒホッフの法則(ベクトル図作成)"
    run_id = timestamp()
    outputs = []
    check_stop_requested()
    pbl_progress(0, 1, frequency=None, stage="load", message="ベクトル図作成中")

    clear_stop_request()
    emit_progress(0, 3, message="ベクトル図作成開始")

    check_stop_requested()
    results = load_json(VECTOR_JSON, {})
    if "coil" not in results:
        raise RuntimeError("coil の測定結果がありません。先に 5キルヒホッフの法則(コイル).py を実行してください。")
    if "capacitor" not in results:
        raise RuntimeError("capacitor の測定結果がありません。先に 4キルヒホッフの法則(キャパシタ).py を実行してください。")

    coil = results["coil"]
    capacitor = results["capacitor"]

    freq_coil = coil["frequency_Hz"]
    freq_cap = capacitor["frequency_Hz"]
    if abs(freq_coil - freq_cap) > 1e-9:
        raise RuntimeError("コイル測定とキャパシタ測定の周波数が一致していません。")

    FREQ = freq_coil
    omega = 2 * math.pi * FREQ

    VL_meas = load_complex(coil, "VL")
    VC_meas = load_complex(capacitor, "VC")
    VR_meas = load_complex(capacitor, "VR")

    VL_meas = rotate_by_reference(VL_meas, VR_meas)
    VC_meas = rotate_by_reference(VC_meas, VR_meas)
    VR_meas = rotate_by_reference(VR_meas, VR_meas)
    VE_meas = VL_meas + VC_meas + VR_meas

    phasors_meas = {
        r"$V_R$": VR_meas,
        r"$V_L$": VL_meas,
        r"$V_C$": VC_meas,
        r"$V_E$": VE_meas,
    }

    I_rms = abs(VR_meas) / R_THEORY
    VR_theory = I_rms * R_THEORY + 0j
    VL_theory = 1j * I_rms * omega * L_THEORY
    VC_theory = -1j * I_rms / (omega * C_THEORY)
    VE_theory = VR_theory + VL_theory + VC_theory

    phasors_theory = {
        r"$V_R$": VR_theory,
        r"$V_L$": VL_theory,
        r"$V_C$": VC_theory,
        r"$V_E$": VE_theory,
    }

    emit_progress(1, 3, frequency=FREQ, message="フェーザ計算中")
    print(f"\nFrequency = {FREQ} Hz")
    print(f"R_THEORY = {R_THEORY} Ω")
    print(f"L_THEORY = {L_THEORY} H")
    print(f"C_THEORY = {C_THEORY} F")
    print(f"I_rms used for theory = {I_rms:.6f} A")
    print_phasors("Measured phasors", phasors_meas)
    print_phasors("Theoretical phasors", phasors_theory)

    representative_lines = [
        f"Frequency = {FREQ} Hz",
        f"R_THEORY = {R_THEORY} Ω",
        f"L_THEORY = {L_THEORY} H",
        f"C_THEORY = {C_THEORY} F",
        f"I_rms used for theory = {I_rms:.6f} A",
        f"Measured VR: RMS = {abs(VR_meas):.6f} V, phase = {phase_deg(VR_meas):.3f} deg",
        f"Measured VL: RMS = {abs(VL_meas):.6f} V, phase = {phase_deg(VL_meas):.3f} deg",
        f"Measured VC: RMS = {abs(VC_meas):.6f} V, phase = {phase_deg(VC_meas):.3f} deg",
        f"Measured VE = VR + VL + VC: RMS = {abs(VE_meas):.6f} V, phase = {phase_deg(VE_meas):.3f} deg",
    ]
    representative_path = save_representative_values(
        "vector_diagram_representative.json",
        "6 キルヒホッフの法則(ベクトル図作成)",
        representative_lines,
        {
            "frequency_Hz": FREQ,
            "I_rms_A": I_rms,
            "VR_meas_rms_V": abs(VR_meas), "VR_meas_phase_deg": phase_deg(VR_meas),
            "VL_meas_rms_V": abs(VL_meas), "VL_meas_phase_deg": phase_deg(VL_meas),
            "VC_meas_rms_V": abs(VC_meas), "VC_meas_phase_deg": phase_deg(VC_meas),
            "VE_meas_rms_V": abs(VE_meas), "VE_meas_phase_deg": phase_deg(VE_meas),
            "VR_theory_rms_V": abs(VR_theory), "VR_theory_phase_deg": phase_deg(VR_theory),
            "VL_theory_rms_V": abs(VL_theory), "VL_theory_phase_deg": phase_deg(VL_theory),
            "VC_theory_rms_V": abs(VC_theory), "VC_theory_phase_deg": phase_deg(VC_theory),
            "VE_theory_rms_V": abs(VE_theory), "VE_theory_phase_deg": phase_deg(VE_theory),
        },
    )
    outputs.append(representative_path)

    std_map = {
        r"$V_R$": (get_std(capacitor, "VR_rms_fft_std"), get_std(capacitor, "VR_phase_deg_fft_std")),
        r"$V_L$": (get_std(coil, "VL_rms_fft_std"), get_std(coil, "VL_phase_deg_fft_std")),
        r"$V_C$": (get_std(capacitor, "VC_rms_fft_std"), get_std(capacitor, "VC_phase_deg_fft_std")),
        r"$V_E$": ("", ""),
    }

    u_VE_rms, u_VE_phase = estimate_sum_uncertainty(
        [
            (
                VR_meas,
                to_float_or_nan(std_map[r"$V_R$"][0]),
                to_float_or_nan(std_map[r"$V_R$"][1]),
            ),
            (
                VL_meas,
                to_float_or_nan(std_map[r"$V_L$"][0]),
                to_float_or_nan(std_map[r"$V_L$"][1]),
            ),
            (
                VC_meas,
                to_float_or_nan(std_map[r"$V_C$"][0]),
                to_float_or_nan(std_map[r"$V_C$"][1]),
            ),
        ]
    )
    std_map[r"$V_E$"] = (u_VE_rms, u_VE_phase)

    csv_rows = []
    for category, phasors in [("measured", phasors_meas), ("theory", phasors_theory)]:
        for name, z in phasors.items():
            rms_std, phase_std = std_map.get(name, ("", "")) if category == "measured" else ("", "")
            csv_rows.append([
                category,
                name,
                abs(z),
                rms_std,
                phase_deg(z),
                phase_std,
                z.real,
                z.imag,
            ])

    csv_path = save_csv(
        f"vector_diagram_{run_id}.csv",
        ["category", "phasor", "RMS [V]", "RMS_std [V]", "phase [deg]", "phase_std [deg]", "real [V]", "imag [V]"],
        csv_rows,
    )
    outputs.append(csv_path)

    fig, axes = plt.subplots(1, 2, figsize=(14, 7))
    draw_phasor_diagram(axes[0], phasors_theory, "Theoretical Phasor Diagram")
    draw_phasor_diagram(axes[1], phasors_meas, "Measured Phasor Diagram")
    fig.suptitle(f"LCR Voltage Phasor Diagrams, f = {FREQ} Hz", fontsize=16)
    axes[0].text(
        0.02,
        0.98,
        build_theory_annotation(phasors_theory),
        transform=axes[0].transAxes,
        fontsize=9,
        va="top",
        ha="left",
        bbox=dict(boxstyle="round", facecolor="white", alpha=0.85),
    )
    axes[1].text(
        0.02,
        0.98,
        build_measured_annotation(phasors_meas, std_map),
        transform=axes[1].transAxes,
        fontsize=9,
        va="top",
        ha="left",
        bbox=dict(boxstyle="round", facecolor="white", alpha=0.85),
    )
    plt.tight_layout()

    fig_path = image_path(f"vector_diagram_theory_and_exp_{frequency_tag(FREQ)}_{run_id}.png")
    emit_progress(2, 3, frequency=FREQ, message="画像保存中")
    plt.savefig(fig_path, dpi=300, bbox_inches="tight")
    plt.close()
    outputs.append(fig_path)

    print(f"\nVector diagram saved: {fig_path}")
    emit_progress(3, 3, frequency=FREQ, message="ベクトル図作成完了")
    pbl_progress(1, 1, frequency=FREQ, stage="done", message="完了")
    append_run_log(script_name, outputs)
    print_progress(1, 1, frequency=FREQ, message="完了")


if __name__ == "__main__":
    main()
