import argparse
import math
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
R_KNOWN = 56.0
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

    script_name = "4キルヒホッフの法則(キャパシタ)"
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
        sleep_with_stop_check(2.0)

        # 接続：発振器 → L → C → R → GND
        # CH1 = Cの前の電位
        # CH2 = Cの後の電位 = Rの上端電位
        # MATH = CH1 - CH2 = VC
        setup_ch1_ch2_math_subtract(scope)
        setup_scope_external_trigger(scope, freq)
        setup_scope_time_scale_for_fft(scope, freq, n_periods=5, margin=1.2)
        sleep_with_stop_check(2.0)

        check_stop_requested()
        auto_adjust_vertical_scale(scope, "CH1")
        check_stop_requested()
        auto_adjust_vertical_scale(scope, "CH2")
        check_stop_requested()
        auto_adjust_math_vertical_scale(scope)


        raw_results = []
        VC_list = []
        VR_list = []
        VC_rms_list = []
        VR_rms_list = []
        VC_phase_list = []
        VR_phase_list = []
        irms_list = []
        xc_list = []
        C_list = []
        vC_scope_list = []
        vR_scope_list = []
        M_C_list = []
        M_R_list = []
        dt_C_list = []
        dt_R_list = []

        for repeat in range(1, N_REPEAT + 1):
            check_stop_requested()
            t1, ch1, dt1 = read_waveform(scope, "CH1")
            check_stop_requested()
            t2, ch2, dt2 = read_waveform(scope, "CH2")

            vC_wave = ch1 - ch2
            vR_wave = ch2

            VC, VC_rms, VC_phase_deg, M_C, dt_C, actual_T_C = fft_phasor_n_period(vC_wave, dt1, freq, n_periods=5)
            VR, VR_rms, VR_phase_deg, M_R, dt_R, actual_T_R = fft_phasor_n_period(vR_wave, dt2, freq, n_periods=5)

            _, vR_rms_scope = measure_pk2pk_rms(scope, "CH2")
            _, vC_rms_scope = measure_pk2pk_rms(scope, "MATH")

            irms = VR_rms / R_KNOWN
            XC = VC_rms / irms
            C = 1 / (2 * math.pi * freq * XC)

            print(
                f"repeat {repeat}/{N_REPEAT}: "
                f"VC_rms = {VC_rms:.6f} V, VC_phase = {VC_phase_deg:.3f} deg, "
                f"VR_rms = {VR_rms:.6f} V, VR_phase = {VR_phase_deg:.3f} deg, "
                f"XC = {XC:.6f} Ω, C = {C * 1e6:.6f} μF"
            )

            raw_results.append([
                freq, repeat,
                VC_rms, VC_phase_deg, float(VC.real), float(VC.imag),
                VR_rms, VR_phase_deg, float(VR.real), float(VR.imag),
                irms, XC, C,
                vC_rms_scope, vR_rms_scope,
                M_C, dt_C, actual_T_C, M_R, dt_R, actual_T_R,
            ])

            VC_list.append(VC)
            VR_list.append(VR)
            VC_rms_list.append(VC_rms)
            VR_rms_list.append(VR_rms)
            VC_phase_list.append(VC_phase_deg)
            VR_phase_list.append(VR_phase_deg)
            irms_list.append(irms)
            xc_list.append(XC)
            C_list.append(C)
            vC_scope_list.append(vC_rms_scope)
            vR_scope_list.append(vR_rms_scope)
            M_C_list.append(M_C)
            M_R_list.append(M_R)
            dt_C_list.append(dt_C)
            dt_R_list.append(dt_R)
            sleep_with_stop_check(REPEAT_INTERVAL)

        VC_mean = complex_mean(VC_list)
        VR_mean = complex_mean(VR_list)
        VC_rms_mean, VC_rms_std = mean_std(VC_rms_list)
        VR_rms_mean, VR_rms_std = mean_std(VR_rms_list)
        VC_phase_mean, VC_phase_std = mean_std(VC_phase_list)
        VR_phase_mean, VR_phase_std = mean_std(VR_phase_list)
        irms_mean, irms_std = mean_std(irms_list)
        xc_mean, xc_std = mean_std(xc_list)
        C_mean, C_std = mean_std(C_list)
        vC_scope_mean, vC_scope_std = mean_std(vC_scope_list)
        vR_scope_mean, vR_scope_std = mean_std(vR_scope_list)
        M_C_mean, M_C_std = mean_std(M_C_list)
        M_R_mean, M_R_std = mean_std(M_R_list)
        dt_C_mean, dt_C_std = mean_std(dt_C_list)
        dt_R_mean, dt_R_std = mean_std(dt_R_list)

        data = {
            "timestamp": datetime.now().isoformat(),
            "frequency_Hz": freq,
            "R_known_ohm": R_KNOWN,
            "n_repeat": N_REPEAT,
            "M_C": M_C_mean,
            "M_C_std": M_C_std,
            "dt_C_s": dt_C_mean,
            "dt_C_s_std": dt_C_std,
            "Mdt_C_s": M_C_mean * dt_C_mean,
            "M_R": M_R_mean,
            "M_R_std": M_R_std,
            "dt_R_s": dt_R_mean,
            "dt_R_s_std": dt_R_std,
            "Mdt_R_s": M_R_mean * dt_R_mean,
            "VC_rms_fft": abs(VC_mean),
            "VC_rms_fft_std": VC_rms_std,
            "VC_phase_deg_fft": phase_deg(VC_mean),
            "VC_phase_deg_fft_std": VC_phase_std,
            "VC_real": float(VC_mean.real),
            "VC_imag": float(VC_mean.imag),
            "VR_rms_fft": abs(VR_mean),
            "VR_rms_fft_std": VR_rms_std,
            "VR_phase_deg_fft": phase_deg(VR_mean),
            "VR_phase_deg_fft_std": VR_phase_std,
            "VR_real": float(VR_mean.real),
            "VR_imag": float(VR_mean.imag),
            "VC_rms_scope": vC_scope_mean,
            "VC_rms_scope_std": vC_scope_std,
            "VR_rms_scope": vR_scope_mean,
            "VR_rms_scope_std": vR_scope_std,
            "Irms_A": irms_mean,
            "Irms_A_std": irms_std,
            "XC_ohm": xc_mean,
            "XC_ohm_std": xc_std,
            "C_F": C_mean,
            "C_F_std": C_std,
        }

        vector_json = save_vector_result("capacitor", data)
        outputs.append(vector_json)

        raw_csv_path = save_csv(
            f"kirchhoff_capacitor_raw_{run_id}.csv",
            [
                "Frequency [Hz]", "repeat",
                "VC_rms_fft [V]", "VC_phase [deg]", "VC_real [V]", "VC_imag [V]",
                "VR_rms_fft [V]", "VR_phase [deg]", "VR_real [V]", "VR_imag [V]",
                "Irms [A]", "XC [Ohm]", "C [F]",
                "VC_rms_scope [V]", "VR_rms_scope [V]",
                "M_C", "dt_C [s]", "Mdt_C [s]", "M_R", "dt_R [s]", "Mdt_R [s]",
            ],
            raw_results,
        )
        outputs.append(raw_csv_path)

        summary_csv_path = save_csv(
            f"kirchhoff_capacitor_summary_{run_id}.csv",
            [
                "Frequency [Hz]", "n_repeat",
                "VC_rms_fft_mean [V]", "VC_rms_fft_std [V]", "VC_phase_mean [deg]", "VC_phase_std [deg]",
                "VR_rms_fft_mean [V]", "VR_rms_fft_std [V]", "VR_phase_mean [deg]", "VR_phase_std [deg]",
                "Irms_mean [A]", "Irms_std [A]", "XC_mean [Ohm]", "XC_std [Ohm]", "C_mean [F]", "C_std [F]",
            ],
            [[
                freq, N_REPEAT,
                abs(VC_mean), VC_rms_std, phase_deg(VC_mean), VC_phase_std,
                abs(VR_mean), VR_rms_std, phase_deg(VR_mean), VR_phase_std,
                irms_mean, irms_std, xc_mean, xc_std, C_mean, C_std,
            ]],
        )
        outputs.append(summary_csv_path)

        representative_lines = [
            "--- Capacitor measurement summary ---",
            f"Frequency = {freq:g} Hz",
            f"VC_rms by FFT = {abs(VC_mean):.6f} ± {VC_rms_std:.6f} V",
            f"VC_phase by FFT = {phase_deg(VC_mean):.3f} ± {VC_phase_std:.3f} deg",
            f"VR_rms by FFT = {abs(VR_mean):.6f} ± {VR_rms_std:.6f} V",
            f"VR_phase by FFT = {phase_deg(VR_mean):.3f} ± {VR_phase_std:.3f} deg",
            f"Irms = {irms_mean:.6f} ± {irms_std:.6f} A",
            f"XC = {xc_mean:.6f} ± {xc_std:.6f} Ω",
            f"C = {C_mean * 1e6:.6f} ± {C_std * 1e6:.6f} μF",
        ]
        print("\n" + "\n".join(representative_lines) + "\n")
        representative_path = save_representative_values(
            "kirchhoff_capacitor_representative.json",
            "4 キルヒホッフの法則(キャパシタ)",
            representative_lines,
            {
                "frequency_Hz": freq,
                "VC_rms_fft_V": abs(VC_mean), "VC_rms_fft_std_V": VC_rms_std,
                "VC_phase_deg_fft": phase_deg(VC_mean), "VC_phase_deg_fft_std": VC_phase_std,
                "VR_rms_fft_V": abs(VR_mean), "VR_rms_fft_std_V": VR_rms_std,
                "VR_phase_deg_fft": phase_deg(VR_mean), "VR_phase_deg_fft_std": VR_phase_std,
                "Irms_A": irms_mean, "Irms_A_std": irms_std,
                "XC_ohm": xc_mean, "XC_ohm_std": xc_std,
                "C_F": C_mean, "C_F_std": C_std,
            },
        )
        outputs.append(representative_path)

        img_path = save_scope_screenshot(scope, f"kirchhoff_capacitor_{frequency_tag(freq)}_{run_id}.jpg")
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
