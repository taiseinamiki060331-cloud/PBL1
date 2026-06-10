import argparse
import time

import matplotlib.pyplot as plt
import numpy as np

from pbl_common import (
    image_path,
    auto_adjust_vertical_scale,
    append_run_log,
    fg_output_off,
    mean_std,
    measure_pk2pk_rms,
    open_instruments,
    print_idn,
    save_csv,
    save_representative_values,
    set_fg_sine,
    setup_fg_sine,
    setup_scope_external_trigger,
    setup_scope_time_scale,
    show_only_ch1,
    timestamp,
    resonance_condition_tag,
    pbl_progress,
    check_stop_requested,
    MeasurementStopped,
    init_csv,
    append_csv_row,
)

# ============================
# 測定条件
# ============================
VPP = 1.0
OFFSET = 0.0

COARSE_FREQUENCIES = np.logspace(np.log10(100), np.log10(10000), 30)

FINE_POINTS = 100
FINE_WIDTH_RATIO = 0.30

N_REPEAT = 5
REPEAT_INTERVAL = 0.1
SETTLE_TIME = 2.0
AUTO_SCALE_EVERY = 1

RAW_HEADER = ["stage", "Frequency [Hz]", "repeat", "Vrms [V]", "Irms [A]", "Z [Ohm]"]
SUMMARY_HEADER = [
    "stage",
    "Frequency [Hz]",
    "Vrms_mean [V]",
    "Vrms_std [V]",
    "Irms_mean [A]",
    "Irms_std [A]",
    "Z_mean [Ohm]",
    "Z_std [Ohm]",
]


def measure_frequency_list(
    fg,
    dmm,
    scope,
    frequencies,
    raw_results,
    summary_results,
    stage,
    raw_csv_name,
    summary_csv_name,
    progress_state,
):
    total_steps = progress_state["total"]
    for index, freq in enumerate(frequencies):
        check_stop_requested()
        freq = float(freq)
        print(f"\n[{stage}] Measuring at {freq:.2f} Hz")
        pbl_progress(progress_state["done"], total_steps, frequency=freq, stage=stage, message="周波数設定中")

        set_fg_sine(fg, freq, VPP, OFFSET)
        setup_scope_external_trigger(scope, freq)
        setup_scope_time_scale(scope, freq)

        # 共振付近で定常状態になるまで十分待つ
        sleep_with_stop_check(SETTLE_TIME)

        # 測定開始時と、周波数を5点進めるごとに縦軸スケールを自動調整
        if index == 0 or index % AUTO_SCALE_EVERY == 0:
            check_stop_requested()
            auto_adjust_vertical_scale(scope, "CH1")

        vrms_list = []
        irms_list = []
        z_list = []

        for repeat in range(1, N_REPEAT + 1):
            check_stop_requested()
            pbl_progress(progress_state["done"] + 1, total_steps, frequency=freq, stage=stage, message="測定中")
            irms = float(dmm.query("READ?"))

            _, vrms = measure_pk2pk_rms(scope, "CH1")
            Z = vrms / irms

            print(
                f"  repeat {repeat}/{N_REPEAT}: "
                f"Vrms = {vrms:.5f} V, Irms = {irms:.5f} A, Z = {Z:.5f} Ω"
            )

            raw_row = [stage, freq, repeat, vrms, irms, Z]
            raw_results.append(raw_row)
            append_csv_row(raw_csv_name, raw_row)
            vrms_list.append(vrms)
            irms_list.append(irms)
            z_list.append(Z)
            progress_state["done"] += 1

            if repeat < N_REPEAT:
                sleep_with_stop_check(REPEAT_INTERVAL)

        vrms_mean, vrms_std = mean_std(vrms_list)
        irms_mean, irms_std = mean_std(irms_list)
        z_mean, z_std = mean_std(z_list)

        summary_row = [
            stage,
            freq,
            vrms_mean,
            vrms_std,
            irms_mean,
            irms_std,
            z_mean,
            z_std,
        ]
        summary_results.append(summary_row)
        append_csv_row(summary_csv_name, summary_row)

        print(
            f"  mean ± std: Z = {z_mean:.5f} ± {z_std:.5f} Ω, "
            f"Vrms = {vrms_mean:.5f} ± {vrms_std:.5f} V, "
            f"Irms = {irms_mean:.5f} ± {irms_std:.5f} A"
        )


def get_args():
    parser = argparse.ArgumentParser(description="共振現象")
    parser.add_argument("--coarse-start", type=float, default=100.0, help="coarse scan開始周波数[Hz]")
    parser.add_argument("--coarse-stop", type=float, default=10000.0, help="coarse scan終了周波数[Hz]")
    parser.add_argument("--coarse-points", type=int, default=len(COARSE_FREQUENCIES), help="coarse scan点数")
    parser.add_argument("--fine-points", type=int, default=FINE_POINTS, help="fine scan点数")
    parser.add_argument("--fine-width-ratio", type=float, default=FINE_WIDTH_RATIO, help="fine scan幅。0.30ならcoarse最小点の±30%")
    parser.add_argument("--vpp", type=float, default=VPP, help="発振器の振幅[Vpp]")
    parser.add_argument("--n-repeat", type=int, default=N_REPEAT, help="各条件での繰り返し測定回数")
    return parser.parse_args()


def sleep_with_stop_check(seconds, interval=0.1):
    start = time.time()
    while time.time() - start < seconds:
        check_stop_requested()
        time.sleep(interval)


def make_logspace_frequencies(start, stop, points):
    start = float(start)
    stop = float(stop)
    points = int(points)
    if start <= 0 or stop <= 0:
        raise ValueError("周波数は正の値にしてください。")
    if stop <= start:
        raise ValueError("coarse終了周波数は開始周波数より大きくしてください。")
    if points < 2:
        raise ValueError("coarse点数は2以上にしてください。")
    return np.logspace(np.log10(start), np.log10(stop), points)


def main(coarse_frequencies=None, fine_points=None, fine_width_ratio=None, vpp=None, n_repeat=None):
    global VPP, N_REPEAT
    if vpp is not None:
        VPP = float(vpp)
    if n_repeat is not None:
        N_REPEAT = int(n_repeat)
    if VPP <= 0:
        raise ValueError("VPPは正の値にしてください。")
    if N_REPEAT < 1:
        raise ValueError("N_REPEATは1以上にしてください。")
    if coarse_frequencies is None:
        coarse_frequencies = COARSE_FREQUENCIES
    if fine_points is None:
        fine_points = FINE_POINTS
    if fine_width_ratio is None:
        fine_width_ratio = FINE_WIDTH_RATIO

    script_name = "7共振現象"
    run_id = timestamp()
    outputs = []
    coarse_frequencies = np.asarray(COARSE_FREQUENCIES if coarse_frequencies is None else coarse_frequencies, dtype=float)
    fine_points = int(FINE_POINTS if fine_points is None else fine_points)
    fine_width_ratio = float(FINE_WIDTH_RATIO if fine_width_ratio is None else fine_width_ratio)
    if coarse_frequencies.size < 2 or np.any(coarse_frequencies <= 0):
        raise ValueError("coarse周波数リストは正の値を2点以上にしてください。")
    if fine_points < 2:
        raise ValueError("fine点数は2以上にしてください。")
    if fine_width_ratio <= 0:
        raise ValueError("fine幅は正の値にしてください。")

    raw_csv_name = f"resonance_impedance_raw_{run_id}.csv"
    summary_csv_name = f"resonance_impedance_summary_{run_id}.csv"
    raw_csv_path = init_csv(raw_csv_name, RAW_HEADER)
    summary_csv_path = init_csv(summary_csv_name, SUMMARY_HEADER)
    outputs.extend([raw_csv_path, summary_csv_path])

    coarse_count = len(coarse_frequencies)
    # fine scanはcoarse後に範囲が決まるが、点数は既知なので最初から総数に入れる。
    progress_state = {"done": 0, "total": (coarse_count + fine_points) * N_REPEAT}

    fg = None
    dmm = None
    scope = None

    try:
        fg, dmm, scope = open_instruments(use_fg=True, use_dmm=True, use_scope=True)
        print_idn(fg=fg, dmm=dmm, scope=scope)

        setup_fg_sine(fg, 1000, VPP, OFFSET)

        show_only_ch1(scope)
        setup_scope_external_trigger(scope)
        setup_scope_time_scale(scope, 1000)
        dmm.write("*RST")
        dmm.write("CONF:CURR:AC")

        raw_results = []
        summary_results = []

        measure_frequency_list(
            fg,
            dmm,
            scope,
            coarse_frequencies,
            raw_results,
            summary_results,
            stage="coarse",
            raw_csv_name=raw_csv_name,
            summary_csv_name=summary_csv_name,
            progress_state=progress_state,
        )

        coarse_results = [row for row in summary_results if row[0] == "coarse"]
        coarse_freq_array = np.array([row[1] for row in coarse_results])
        coarse_Z_array = np.array([row[6] for row in coarse_results])

        coarse_min_index = np.argmin(coarse_Z_array)
        coarse_f_min = coarse_freq_array[coarse_min_index]
        coarse_Z_min = coarse_Z_array[coarse_min_index]

        print("\nCoarse minimum impedance point")
        print(f"coarse_f_min = {coarse_f_min:.5f} Hz")
        print(f"coarse_Z_min = {coarse_Z_min:.5f} Ω")

        fine_f_start = coarse_f_min * (1.0 - fine_width_ratio)
        fine_f_stop = coarse_f_min * (1.0 + fine_width_ratio)
        fine_frequencies = np.linspace(fine_f_start, fine_f_stop, fine_points)

        print("\nFine scan range")
        print(f"fine_f_start = {fine_f_start:.5f} Hz")
        print(f"fine_f_stop  = {fine_f_stop:.5f} Hz")
        print(f"fine_points  = {fine_points}")

        measure_frequency_list(
            fg,
            dmm,
            scope,
            fine_frequencies,
            raw_results,
            summary_results,
            stage="fine",
            raw_csv_name=raw_csv_name,
            summary_csv_name=summary_csv_name,
            progress_state=progress_state,
        )

        freq_array = np.array([row[1] for row in summary_results])
        Z_array = np.array([row[6] for row in summary_results])

        min_index = np.argmin(Z_array)
        f_min = freq_array[min_index]
        Z_min = Z_array[min_index]

        representative_lines = [
            "Final minimum impedance point",
            f"f_min = {f_min:.5f} Hz",
            f"Z_min = {Z_min:.5f} Ω",
            f"coarse_f_min = {coarse_f_min:.5f} Hz",
            f"coarse_Z_min = {coarse_Z_min:.5f} Ω",
        ]
        print("\n" + "\n".join(representative_lines))
        representative_path = save_representative_values(
            "resonance_representative.json",
            "7 共振現象",
            representative_lines,
            {
                "f_min_Hz": f_min, "Z_min_ohm": Z_min,
                "coarse_f_min_Hz": coarse_f_min, "coarse_Z_min_ohm": coarse_Z_min,
                "fine_f_start_Hz": fine_f_start, "fine_f_stop_Hz": fine_f_stop,
                "fine_points": fine_points,
            },
        )
        outputs.append(representative_path)

        raw_csv_path = save_csv(raw_csv_name, RAW_HEADER, raw_results)
        summary_csv_path = save_csv(summary_csv_name, SUMMARY_HEADER, summary_results)

        condition_tag = resonance_condition_tag(coarse_frequencies, fine_points, fine_width_ratio)
        fig_path = image_path(f"resonance_impedance_errorbar_{condition_tag}_{run_id}.png")

        coarse_plot = [row for row in summary_results if row[0] == "coarse"]
        fine_plot = [row for row in summary_results if row[0] == "fine"]

        coarse_freq = np.array([row[1] for row in coarse_plot])
        coarse_Z = np.array([row[6] for row in coarse_plot])
        coarse_Z_err = np.array([row[7] for row in coarse_plot])

        fine_freq = np.array([row[1] for row in fine_plot])
        fine_Z = np.array([row[6] for row in fine_plot])
        fine_Z_err = np.array([row[7] for row in fine_plot])

        plt.figure(figsize=(8, 5))
        plt.errorbar(coarse_freq, coarse_Z, yerr=coarse_Z_err, fmt="o", markersize=3,  capsize=2, elinewidth=0.5, capthick=0.5, label="Coarse scan")
        plt.errorbar(fine_freq, fine_Z, yerr=fine_Z_err, fmt="s", markersize=3, capsize=2, elinewidth=0.5, capthick=0.5, label="Fine scan")
        plt.scatter(f_min, Z_min, s=10, label=f"Final minimum: {f_min:.2f} Hz", zorder=5)
        plt.xscale("log")
        plt.yscale("log")
        plt.xlabel("Frequency [Hz]")
        plt.ylabel("Impedance [Ω]")
        plt.title("Frequency dependence of LC impedance")
        plt.grid(True, which="both")
        plt.legend()
        plt.tight_layout()
        plt.savefig(fig_path, dpi=300, bbox_inches="tight")
        plt.close()
        outputs.append(fig_path)
        print(f"Figure saved: {fig_path}")

        fine_fig_path = image_path(f"resonance_impedance_fine_only_{condition_tag}_{run_id}.png")
        plt.figure(figsize=(8, 5))
        plt.errorbar(fine_freq, fine_Z, yerr=fine_Z_err, fmt="s", markersize=3, capsize=2, elinewidth=0.5, capthick=0.5, label="Fine scan")
        plt.scatter(f_min, Z_min, s=10, label=f"Minimum: {f_min:.2f} Hz", zorder=5)
        plt.xlabel("Frequency [Hz]")
        plt.ylabel("Impedance [Ω]")
        plt.title("Fine scan around resonance")
        plt.grid(True)
        plt.legend()
        plt.tight_layout()
        plt.savefig(fine_fig_path, dpi=300, bbox_inches="tight")
        plt.close()
        outputs.append(fine_fig_path)
        print(f"Fine-only figure saved: {fine_fig_path}")

        pbl_progress(progress_state["total"], progress_state["total"], frequency=f_min, stage="done", message="完了")
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
    coarse = make_logspace_frequencies(args.coarse_start, args.coarse_stop, args.coarse_points)
    main(coarse_frequencies=coarse, fine_points=args.fine_points, fine_width_ratio=args.fine_width_ratio, vpp=args.vpp, n_repeat=args.n_repeat)
