import argparse
import math
import time

from pbl_common import (
    append_run_log,
    fg_output_off,
    mean_std,
    measure_pk2pk_rms,
    open_instruments,
    print_idn,
    save_csv,
    save_representative_values,
    save_scope_screenshot,
    setup_ch1_ch2_math_subtract,
    setup_fg_sine,
    setup_scope_external_trigger,
    setup_scope_time_scale,
    check_stop_requested,
    MeasurementStopped,
    timestamp,
    frequency_tag,
)

# ============================
# 設定
# ============================
R_KNOWN = 56.0
FREQ = 5000
VPP = 3.0
OFFSET = 0.0
N_REPEAT = 10
REPEAT_INTERVAL = 0.5


def get_args():
    parser = argparse.ArgumentParser(description="フロー電位測定")
    parser.add_argument("--freq", type=float, default=FREQ, help="測定周波数[Hz]")
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
        freq = FREQ
    if freq <= 0:
        raise ValueError("周波数は正の値にしてください。")

    script_name = "3フロー電位測定"
    run_id = timestamp()
    outputs = []
    freq = float(FREQ if freq is None else freq)
    if freq <= 0:
        raise ValueError("周波数は正の値にしてください。")

    fg = None
    dmm = None
    scope = None

    try:
        fg, _, scope = open_instruments(use_fg=True, use_dmm=False, use_scope=True)
        print_idn(fg=fg, scope=scope)

        setup_fg_sine(fg, freq, VPP, OFFSET)

        scope.write("*RST")
        sleep_with_stop_check(2.0)

        # CH1: 全体電圧 Vtotal
        # CH2: 抵抗電圧 VR
        # MATH = CH1 - CH2 = コイル電圧 VL
        setup_ch1_ch2_math_subtract(scope)
        setup_scope_external_trigger(scope, freq)
        setup_scope_time_scale(scope, freq)
        sleep_with_stop_check(2.0)

        raw_results = []
        vL_list = []
        vR_list = []
        irms_list = []
        xl_list = []
        L_list = []

        for repeat in range(1, N_REPEAT + 1):
            check_stop_requested()

            # なんでirmsを直接DMMで測定しないんだっけ？
            # 試しにirmsを直接DMMで測定しようかな？
            vR_pp, vR_rms = measure_pk2pk_rms(scope, "CH2")
            vL_pp, vL_rms = measure_pk2pk_rms(scope, "MATH")

            irms = float(dmm.query("READ?"))
            # irms = vR_rms / R_KNOWN
            XL = vL_rms / irms
            L = XL / (2 * math.pi * freq)

            print(
                f"repeat {repeat}/{N_REPEAT}: "
                f"VL_rms = {vL_rms:.5f} V, VR_rms = {vR_rms:.5f} V, "
                f"Irms = {irms:.6f} A, XL = {XL:.5f} Ω, L = {L * 1e3:.5f} mH"
            )

            raw_results.append([freq, repeat, vL_rms, vR_rms, irms, XL, L])
            vL_list.append(vL_rms)
            vR_list.append(vR_rms)
            irms_list.append(irms)
            xl_list.append(XL)
            L_list.append(L)
            sleep_with_stop_check(REPEAT_INTERVAL)

        vL_mean, vL_std = mean_std(vL_list)
        vR_mean, vR_std = mean_std(vR_list)
        irms_mean, irms_std = mean_std(irms_list)
        xl_mean, xl_std = mean_std(xl_list)
        L_mean, L_std = mean_std(L_list)

        representative_lines = [
            f"Frequency = {freq:g} Hz",
            f"VL_rms = {vL_mean:.5f} ± {vL_std:.5f} V",
            f"VR_rms = {vR_mean:.5f} ± {vR_std:.5f} V",
            f"Irms = {irms_mean:.6f} ± {irms_std:.6f} A",
            f"XL = {xl_mean:.5f} ± {xl_std:.5f} Ω",
            f"L = {L_mean * 1e3:.5f} ± {L_std * 1e3:.5f} mH",
        ]
        print("\n" + "\n".join(representative_lines))
        representative_path = save_representative_values(
            "flow_voltage_representative.json",
            "3 フロー電位測定",
            representative_lines,
            {
                "frequency_Hz": freq,
                "VL_rms_mean_V": vL_mean, "VL_rms_std_V": vL_std,
                "VR_rms_mean_V": vR_mean, "VR_rms_std_V": vR_std,
                "Irms_mean_A": irms_mean, "Irms_std_A": irms_std,
                "XL_mean_ohm": xl_mean, "XL_std_ohm": xl_std,
                "L_mean_H": L_mean, "L_std_H": L_std,
            },
        )
        outputs.append(representative_path)

        raw_csv_path = save_csv(
            f"flow_voltage_raw_{run_id}.csv",
            ["Frequency [Hz]", "repeat", "VL_rms [V]", "VR_rms [V]", "Irms [A]", "XL [Ohm]", "L [H]"],
            raw_results,
        )
        outputs.append(raw_csv_path)

        summary_csv_path = save_csv(
            f"flow_voltage_summary_{run_id}.csv",
            [
                "Frequency [Hz]",
                "VL_rms_mean [V]", "VL_rms_std [V]",
                "VR_rms_mean [V]", "VR_rms_std [V]",
                "Irms_mean [A]", "Irms_std [A]",
                "XL_mean [Ohm]", "XL_std [Ohm]",
                "L_mean [H]", "L_std [H]",
            ],
            [[freq, vL_mean, vL_std, vR_mean, vR_std, irms_mean, irms_std, xl_mean, xl_std, L_mean, L_std]],
        )
        outputs.append(summary_csv_path)

        img_path = save_scope_screenshot(scope, f"flow_voltage_{frequency_tag(freq)}_{run_id}.jpg")
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
    args = get_args()
    main(args.freq, vpp=args.vpp, n_repeat=args.n_repeat)
