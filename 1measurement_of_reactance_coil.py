import argparse
import math
import time

import matplotlib.pyplot as plt
import numpy as np

from pbl_common import (
    image_path,
    append_run_log,
    auto_adjust_vertical_scale,
    fg_output_off,
    measure_pk2pk_rms,
    mean_std,
    open_instruments,
    print_idn,
    save_csv,
    save_representative_values,
    set_fg_sine,
    setup_fg_sine,
    setup_scope_external_trigger,
    setup_scope_time_scale,
    timestamp,
    frequency_list_tag,
    pbl_progress,
    check_stop_requested,
    MeasurementStopped,
    init_csv,
    append_csv_row,
)

# ============================
# 測定条件
# ============================
FREQUENCIES = [200, 500, 1000, 2000, 5000]
VPP = 1.0
OFFSET = 0.0
N_REPEAT = 10
REPEAT_INTERVAL = 0.5

RAW_HEADER = ["Frequency [Hz]", "repeat", "Vrms [V]", "Irms [A]", "XL [Ohm]"]
SUMMARY_HEADER = [
    "Frequency [Hz]",
    "Vrms_mean [V]",
    "Vrms_std [V]",
    "Irms_mean [A]",
    "Irms_std [A]",
    "XL_mean [Ohm]",
    "XL_std [Ohm]",
]


def parse_frequency_list(text):
    values = []
    for part in str(text).replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        value = float(part)
        if value <= 0:
            raise ValueError("周波数は正の値にしてください。")
        values.append(value)
    if not values:
        raise ValueError("周波数リストが空です。")
    return values


def get_args():
    parser = argparse.ArgumentParser(description="リアクタンス測定(コイル)")
    parser.add_argument("--frequencies", default=None, help="測定周波数リスト。例: 200,500,1000,2000,5000")
    parser.add_argument("--vpp", type=float, default=VPP, help="発振器の振幅[Vpp]")
    parser.add_argument("--n-repeat", type=int, default=N_REPEAT, help="各条件での繰り返し測定回数")
    return parser.parse_args()


def sleep_with_stop_check(seconds, interval=0.1):
    start = time.time()
    while time.time() - start < seconds:
        check_stop_requested()
        time.sleep(interval)


def main(frequencies=None, vpp=None, n_repeat=None):
    global VPP, N_REPEAT
    if vpp is not None:
        VPP = float(vpp)
    if n_repeat is not None:
        N_REPEAT = int(n_repeat)
    if VPP <= 0:
        raise ValueError("VPPは正の値にしてください。")
    if N_REPEAT < 1:
        raise ValueError("N_REPEATは1以上にしてください。")
    if frequencies is None:
        frequencies = FREQUENCIES

    script_name = "1リアクタンス測定(コイル)"
    run_id = timestamp()
    outputs = []
    frequencies = list(FREQUENCIES if frequencies is None else frequencies)
    total_steps = len(frequencies) * N_REPEAT
    done_steps = 0

    raw_csv_name = f"reactance_coil_raw_{run_id}.csv"
    summary_csv_name = f"reactance_coil_summary_{run_id}.csv"
    raw_csv_path = init_csv(raw_csv_name, RAW_HEADER)
    summary_csv_path = init_csv(summary_csv_name, SUMMARY_HEADER)
    outputs.extend([raw_csv_path, summary_csv_path])

    fg = None
    dmm = None
    scope = None

    try:
        fg, dmm, scope = open_instruments(use_fg=True, use_dmm=True, use_scope=True)
        print_idn(fg=fg, dmm=dmm, scope=scope)

        # CH2とMATHは使用しないのでOFFにしておく
        scope.write("SELECT:CH2 OFF")
        scope.write("SELECT:MATH OFF")

        setup_fg_sine(fg, 1000, VPP, OFFSET)
        sleep_with_stop_check(1.0)
        dmm.write("*RST")
        sleep_with_stop_check(1.0)

        raw_results = []
        summary_results = []

        for freq in frequencies:
            check_stop_requested()
            pbl_progress(done_steps, total_steps, frequency=freq, stage="frequency setup", message="周波数設定中")
            print(f"\nMeasuring at {freq} Hz")
            set_fg_sine(fg, freq, VPP, OFFSET)
            setup_scope_external_trigger(scope, freq)
            setup_scope_time_scale(scope, freq)
            sleep_with_stop_check(3.0)
            auto_adjust_vertical_scale(scope, "CH1")

            vrms_list = []
            irms_list = []
            xl_list = []

            for repeat in range(1, N_REPEAT + 1):
                check_stop_requested()
                pbl_progress(done_steps + 1, total_steps, frequency=freq, stage="repeat", message="測定中")
                dmm.write("CONF:CURR:AC")
                irms = float(dmm.query("READ?"))

                _, vrms = measure_pk2pk_rms(scope, "CH1")
                XL = vrms / irms

                print(
                    f"  repeat {repeat}/{N_REPEAT}: "
                    f"Vrms = {vrms:.5f} V, Irms = {irms:.5f} A, XL = {XL:.5f} Ω"
                )

                raw_row = [freq, repeat, vrms, irms, XL]
                raw_results.append(raw_row)
                append_csv_row(raw_csv_name, raw_row)
                vrms_list.append(vrms)
                irms_list.append(irms)
                xl_list.append(XL)
                done_steps += 1
                sleep_with_stop_check(REPEAT_INTERVAL)

            vrms_mean, vrms_std = mean_std(vrms_list)
            irms_mean, irms_std = mean_std(irms_list)
            xl_mean, xl_std = mean_std(xl_list)
            summary_row = [
                freq,
                vrms_mean,
                vrms_std,
                irms_mean,
                irms_std,
                xl_mean,
                xl_std,
            ]
            summary_results.append(summary_row)
            append_csv_row(summary_csv_name, summary_row)

            print(
                f"  mean ± std: XL = {xl_mean:.5f} ± {xl_std:.5f} Ω, "
                f"Vrms = {vrms_mean:.5f} ± {vrms_std:.5f} V, "
                f"Irms = {irms_mean:.5f} ± {irms_std:.5f} A"
            )

        # 最終版として同じCSVを上書き保存する。
        raw_csv_path = save_csv(raw_csv_name, RAW_HEADER, raw_results)
        summary_csv_path = save_csv(summary_csv_name, SUMMARY_HEADER, summary_results)

        x = np.array([row[0] for row in summary_results], dtype=float)
        y = np.array([row[5] for row in summary_results], dtype=float)
        yerr = np.array([row[6] for row in summary_results], dtype=float)

        # 切片なしモデル y = a x で最小二乗フィットする。
        # yerr が 0 の点は重み 1 として扱う。
        # weights = 1.0 / np.where(yerr > 0, yerr, 1.0)
        # 測定精度がわりといいので、等重みでフィットする。重み付きにするとweightsが大きくなりすぎてフィットが不安定になる。
        weights = 1.0
        a = np.sum((weights ** 2) * x * y) / np.sum((weights ** 2) * x ** 2)

        # 切片なしモデル y = ax の傾きの標準不確かさを求める。
        # 自由度は「データ点数 - 推定パラメータ数」なので n - 1。
        y_fit_at_data = a * x
        residuals = y - y_fit_at_data
        dof = len(x) - 1
        if dof > 0:
            residual_variance = np.sum(residuals ** 2) / dof
            u_a = math.sqrt(residual_variance / np.sum(x ** 2))
        else:
            u_a = float("nan")

        L = a / (2 * math.pi)
        u_L = u_a / (2 * math.pi)
        x_fit = np.linspace(0, x.max(), 300)
        y_fit = a * x_fit

        representative_lines = [
            f"L = {L * 1e3:.5f} ± {u_L * 1e3:.5f} mH",
            f"Fit slope = {a:.8g} ± {u_a:.8g} Ω/Hz",
            "Fit model = y = ax",
        ]
        print("\n" + "\n".join(representative_lines))
        representative_path = save_representative_values(
            "reactance_coil_representative.json",
            "1 リアクタンス測定(コイル)",
            representative_lines,
            {
                "L_H": L,
                "u_L_H": u_L,
                "L_mH": L * 1e3,
                "u_L_mH": u_L * 1e3,
                "fit_slope_ohm_per_Hz": a,
                "u_fit_slope_ohm_per_Hz": u_a,
                "fit_model": "y=ax",
            },
        )
        outputs.append(representative_path)

        condition_tag = frequency_list_tag(frequencies)
        fig_path = image_path(f"reactance_coil_errorbar_fit_{condition_tag}_{run_id}.png")
        plt.figure(figsize=(6, 4))
        plt.errorbar(x, y, yerr=yerr, fmt="o", capsize=5, label="Mean ± SD")
        plt.plot(x_fit, y_fit, label=f"Fit: y = {a:.5f}x")
        plt.xlabel("Frequency [Hz]")
        plt.ylabel("Reactance [Ω]")
        plt.title("Frequency dependence of reactance (coil)")

        result_text = (
            f"Estimated L = {L * 1e3:.5f} ± {u_L * 1e3:.5f} mH\n"
            f"Fit slope = {a:.5g} ± {u_a:.2g} Ω/Hz"
        )
        ax = plt.gca()
        ax.text(
            0.98,
            0.05,
            result_text,
            transform=ax.transAxes,
            ha="right",
            va="bottom",
            bbox={"boxstyle": "round", "facecolor": "white", "alpha": 0.85},
        )

        plt.legend()
        plt.grid(True)
        plt.tight_layout()
        plt.savefig(fig_path, dpi=300, bbox_inches="tight")
        plt.close()
        outputs.append(fig_path)
        print(f"Figure saved: {fig_path}")

        pbl_progress(total_steps, total_steps, frequency=frequencies[-1], stage="done", message="完了")
        append_run_log(script_name, outputs)

    except MeasurementStopped:
        print("停止要求を受けたため、測定を終了します。", flush=True)

    finally:
        fg_output_off(fg)

        for inst in [scope, dmm, fg]:
            try:
                if inst is not None:
                    inst.close()
            except Exception:
                pass

if __name__ == "__main__":
    args = get_args()
    freqs = parse_frequency_list(args.frequencies) if args.frequencies else None
    main(freqs, vpp=args.vpp, n_repeat=args.n_repeat)
