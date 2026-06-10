import argparse
import time
from datetime import datetime

from pbl_common import (
    append_run_log,
    auto_adjust_vertical_scale,
    auto_adjust_math_vertical_scale,
    check_stop_requested,
    MeasurementStopped,
    complex_mean,
    fft_phasor_n_period,
    fg_output_off,
    mean_std,
    measure_pk2pk_rms,
    open_instruments,
    phase_deg,
    print_idn,
    read_waveform,
    save_csv,
    save_representative_values,
    save_scope_screenshot,
    save_vector_result,
    setup_ch1_ch2_math_subtract,
    setup_fg_sine,
    setup_scope_external_trigger,
    setup_scope_time_scale_for_fft,
    timestamp,
    frequency_tag,)

# 4 → 5 → 6 の順に実行してください。

# ============================
# 設定
# ============================
FREQ = 500
VPP = 3.0
OFFSET = 0.0
N_REPEAT = 10
REPEAT_INTERVAL = 0.5


def parse_args():
    parser = argparse.ArgumentParser(description="物理学PBL1 キルヒホッフ測定")
    parser.add_argument(
        "--freq",
        type=float,
        default=FREQ,
        help="測定周波数 [Hz]。指定しない場合はコード内のFREQを使います。",
    )
    parser.add_argument("--vpp", type=float, default=VPP, help="発振器の振幅[Vpp]")
    parser.add_argument("--n-repeat", type=int, default=N_REPEAT, help="各条件での繰り返し測定回数")
    return parser.parse_args()


def sleep_with_stop_check(seconds, interval=0.1):
    start = time.time()
    while time.time() - start < seconds:
        check_stop_requested()
        time.sleep(interval)


def main(freq=None, vpp=None, n_repeat=None):
    global VPP, N_REPEAT
    if vpp is not None:
        VPP = float(vpp)
    if n_repeat is not None:
        N_REPEAT = int(n_repeat)
    if VPP <= 0:
        raise ValueError("VPPは正の値にしてください。")
    if N_REPEAT < 1:
        raise ValueError("N_REPEATは1以上にしてください。")
    if freq is None:
        args = parse_args()
        freq = args.freq
    freq = float(freq)

    script_name = "5キルヒホッフの法則(コイル)"
    run_id = timestamp()
    outputs = []

    fg = None
    dmm = None
    scope = None

    try:
        fg, _, scope = open_instruments(use_fg=True, use_dmm=False, use_scope=True)
        print_idn(fg=fg, scope=scope)

        setup_fg_sine(fg, freq, VPP, OFFSET)

        scope.write("*RST")
        sleep_with_stop_check(2)

        # 接続：発振器 → L → C → R → GND
        # CH1 = Lの前の電位
        # CH2 = Lの後の電位
        # MATH = CH1 - CH2 = VL
        setup_ch1_ch2_math_subtract(scope)
        setup_scope_external_trigger(scope, freq)
        setup_scope_time_scale_for_fft(scope, freq, n_periods=5, margin=1.2)
        sleep_with_stop_check(2)

        check_stop_requested()
        auto_adjust_vertical_scale(scope, "CH1")
        check_stop_requested()
        auto_adjust_vertical_scale(scope, "CH2")
        check_stop_requested()
        auto_adjust_vertical_scale(scope)

        raw_results = []
        VL_list = []
        VL_rms_list = []
        VL_phase_list = []
        vL_scope_list = []
        M_list = []
        dt_list = []
 
        for repeat in range(1, N_REPEAT + 1):
            check_stop_requested()
            t1, ch1, dt1 = read_waveform(scope, "CH1")
            check_stop_requested()
            _, ch2, dt2 = read_waveform(scope, "CH2")

            vL_wave = ch1 - ch2
            VL, VL_rms, VL_phase_deg, M, dt, actual_T = fft_phasor_n_period(vL_wave, dt1, freq, n_periods=5)
            _, vL_rms_scope = measure_pk2pk_rms(scope, "MATH")

            print(
                f"repeat {repeat}/{N_REPEAT}: "
                f"VL_rms = {VL_rms:.6f} V, VL_phase = {VL_phase_deg:.3f} deg, "
                f"VL_rms_scope = {vL_rms_scope:.6f} V"
            )

            raw_results.append([
                freq, repeat,
                VL_rms, VL_phase_deg, float(VL.real), float(VL.imag),
                vL_rms_scope, M, dt, actual_T,
            ])
            VL_list.append(VL)
            VL_rms_list.append(VL_rms)
            VL_phase_list.append(VL_phase_deg)
            vL_scope_list.append(vL_rms_scope)
            M_list.append(M)
            dt_list.append(dt)
            sleep_with_stop_check(REPEAT_INTERVAL)

        VL_mean = complex_mean(VL_list)
        VL_rms_mean, VL_rms_std = mean_std(VL_rms_list)
        VL_phase_mean, VL_phase_std = mean_std(VL_phase_list)
        vL_scope_mean, vL_scope_std = mean_std(vL_scope_list)
        M_mean, M_std = mean_std(M_list)
        dt_mean, dt_std = mean_std(dt_list)

        data = {
            "timestamp": datetime.now().isoformat(),
            "frequency_Hz": freq,
            "n_repeat": N_REPEAT,
            "M": M_mean,
            "M_std": M_std,
            "dt_s": dt_mean,
            "dt_s_std": dt_std,
            "Mdt_s": M_mean * dt_mean,
            "VL_rms_fft": abs(VL_mean),
            "VL_rms_fft_std": VL_rms_std,
            "VL_phase_deg_fft": phase_deg(VL_mean),
            "VL_phase_deg_fft_std": VL_phase_std,
            "VL_real": float(VL_mean.real),
            "VL_imag": float(VL_mean.imag),
            "VL_pp_scope": None,
            "VL_rms_scope": vL_scope_mean,
            "VL_rms_scope_std": vL_scope_std,
        }

        vector_json = save_vector_result("coil", data)
        outputs.append(vector_json)

        raw_csv_path = save_csv(
            f"kirchhoff_coil_raw_{run_id}.csv",
            [
                "Frequency [Hz]", "repeat",
                "VL_rms_fft [V]", "VL_phase [deg]", "VL_real [V]", "VL_imag [V]",
                "VL_rms_scope [V]", "M", "dt [s]", "Mdt [s]",
            ],
            raw_results,
        )
        outputs.append(raw_csv_path)

        summary_csv_path = save_csv(
            f"kirchhoff_coil_summary_{run_id}.csv",
            [
                "Frequency [Hz]", "n_repeat",
                "VL_rms_fft_mean [V]", "VL_rms_fft_std [V]",
                "VL_phase_mean [deg]", "VL_phase_std [deg]",
                "VL_real_mean [V]", "VL_imag_mean [V]",
                "VL_rms_scope_mean [V]", "VL_rms_scope_std [V]",
                "M_mean", "M_std", "dt_mean [s]", "dt_std [s]", "Mdt_mean [s]",
            ],
            [[
                freq, N_REPEAT,
                abs(VL_mean), VL_rms_std,
                phase_deg(VL_mean), VL_phase_std,
                float(VL_mean.real), float(VL_mean.imag),
                vL_scope_mean, vL_scope_std,
                M_mean, M_std, dt_mean, dt_std, M_mean * dt_mean,
            ]],
        )
        outputs.append(summary_csv_path)

        representative_lines = [
            "--- Coil measurement summary ---",
            f"Frequency = {freq:g} Hz",
            f"Δt = {dt_mean:.9e} ± {dt_std:.9e} s",
            f"M = {M_mean:.3f} ± {M_std:.3f}",
            f"MΔt = {M_mean * dt_mean:.9e} s",
            f"VL_rms by FFT = {abs(VL_mean):.6f} ± {VL_rms_std:.6f} V",
            f"VL_phase by FFT = {phase_deg(VL_mean):.3f} ± {VL_phase_std:.3f} deg",
            f"VL_rms by scope PK2PK = {vL_scope_mean:.6f} ± {vL_scope_std:.6f} V",
        ]
        print("\n" + "\n".join(representative_lines) + "\n")
        representative_path = save_representative_values(
            "kirchhoff_coil_representative.json",
            "5 キルヒホッフの法則(コイル)",
            representative_lines,
            {
                "frequency_Hz": freq,
                "dt_s": dt_mean, "dt_s_std": dt_std,
                "M": M_mean, "M_std": M_std, "Mdt_s": M_mean * dt_mean,
                "VL_rms_fft_V": abs(VL_mean), "VL_rms_fft_std_V": VL_rms_std,
                "VL_phase_deg_fft": phase_deg(VL_mean), "VL_phase_deg_fft_std": VL_phase_std,
                "VL_rms_scope_V": vL_scope_mean, "VL_rms_scope_std_V": vL_scope_std,
            },
        )
        outputs.append(representative_path)

        img_path = save_scope_screenshot(scope, f"kirchhoff_coil_{frequency_tag(freq)}_{run_id}.jpg")
        outputs.append(img_path)
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
    args = parse_args()
    main(args.freq, vpp=args.vpp, n_repeat=args.n_repeat)
