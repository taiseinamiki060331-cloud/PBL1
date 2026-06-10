# ==========================================
# 物理学PBL1 共通ライブラリ
# ==========================================

from pathlib import Path
from datetime import datetime
import json
import csv
import time
import math

import pyvisa
import numpy as np

# ==========================================
# 保存フォルダ設定
# ==========================================
BASE_DIR = Path(__file__).resolve().parent
RESULT_DIR = BASE_DIR / "results"
IMAGE_DIR = RESULT_DIR / "images"
DATA_DIR = RESULT_DIR / "data"

# 画像は種類ごとのサブフォルダに分けて保存する。
IMAGE_SUBDIRS = {
    "flow_voltage": "flow_voltage",
    "kirchhoff_capacitor": "kirchhoff_capacitor",
    "kirchhoff_coil": "kirchhoff_coil",
    "reactance_capacitor_errorbar_fit": "reactance_capacitor_errorbar_fit",
    "reactance_coil_errorbar_fit": "reactance_coil_errorbar_fit",
    "resonance_impedance_errorbar": "resonance_impedance_errorbar",
    "resonance_impedance_fine_only": "resonance_impedance_fine_only",
    "vector_diagram_theory_and_exp": "vector_diagram_theory_and_exp",
}



def format_number_for_filename(value, digits=10):
    """数値をファイル名に使いやすい文字列へ変換する。"""
    try:
        x = float(value)
    except Exception:
        text = str(value)
    else:
        text = f"{x:.{digits}g}"
    text = text.replace("-", "m")
    text = text.replace("+", "")
    text = text.replace(".", "p")
    text = text.replace("e", "e")
    return text


def frequency_tag(freq):
    """単一周波数を画像ファイル名用タグにする。"""
    return f"freq_{format_number_for_filename(freq)}"


def frequency_list_tag(frequencies):
    """周波数リストを画像ファイル名用タグにする。"""
    return "freq_" + "-".join(format_number_for_filename(f) for f in frequencies)


def resonance_condition_tag(coarse_frequencies, fine_points, fine_width_ratio):
    """共振測定の走査条件を画像ファイル名用タグにする。"""
    arr = np.asarray(coarse_frequencies, dtype=float)
    return (
        f"coarse_{format_number_for_filename(arr.min())}-{format_number_for_filename(arr.max())}"
        f"_{len(arr)}pts_fine_{int(fine_points)}pts_width_{format_number_for_filename(fine_width_ratio)}"
    )

def image_subdir_for_filename(filename):
    """画像ファイル名に対応するサブフォルダ名を返す。"""
    name = Path(filename).name
    for prefix in sorted(IMAGE_SUBDIRS, key=len, reverse=True):
        if name.startswith(prefix):
            return IMAGE_SUBDIRS[prefix]
    return ""


def image_path(filename):
    """画像ファイルの保存先パスを返す。"""
    subdir = image_subdir_for_filename(filename)
    folder = IMAGE_DIR / subdir if subdir else IMAGE_DIR
    folder.mkdir(parents=True, exist_ok=True)
    return folder / filename


for d in [RESULT_DIR, IMAGE_DIR, DATA_DIR]:
    d.mkdir(parents=True, exist_ok=True)

for subdir in IMAGE_SUBDIRS.values():
    (IMAGE_DIR / subdir).mkdir(parents=True, exist_ok=True)

# ==========================================
# VISA機器の自動検出
# ==========================================
# 以前は固定アドレスを既定値として使っていたが、別個体・別PC・別USBポートでは
# ASRL番号やUSBシリアル番号が変わる。現在は list_resources() で実際に見つかった
# VISAリソースへ *IDN? を送り、応答内容から fg / dmm / scope を判定する。

VISA_CONFIG_PATH = DATA_DIR / "visa_config.json"

# 既知だった旧アドレス。通常の接続には使わず、表示・参考用だけに残す。
LEGACY_VISA_ADDRESSES = {
    "fg": "ASRL6::INSTR",
    "dmm": "ASRL5::INSTR",
    "scope": "USB0::0x0699::0x0368::C015501::INSTR",
}

# 旧コードとの互換性のために名前だけ残す。
FG_ADDR = LEGACY_VISA_ADDRESSES["fg"]
DMM_ADDR = LEGACY_VISA_ADDRESSES["dmm"]
SCOPE_ADDR = LEGACY_VISA_ADDRESSES["scope"]
DEFAULT_VISA_ADDRESSES = dict(LEGACY_VISA_ADDRESSES)


class VisaDeviceNotFound(RuntimeError):
    """指定した役割のVISA機器が自動検出できなかったときの例外。"""
    pass


def _normalize_resource_name(address):
    """VISAアドレスを文字列へ正規化する。"""
    return str(address).strip() if address is not None else ""


def configure_rs232_instrument(inst):
    """RS-232機器用の通信条件を設定する。"""
    inst.timeout = 5000
    inst.baud_rate = 9600
    inst.data_bits = 8
    inst.parity = pyvisa.constants.Parity.none
    inst.stop_bits = pyvisa.constants.StopBits.one
    inst.write_termination = "\r\n"
    inst.read_termination = "\r\n"
    return inst


def configure_instrument_by_address(inst, address):
    """VISAアドレスの種類に応じて最低限の通信設定を行う。"""
    addr = _normalize_resource_name(address).upper()
    if addr.startswith("ASRL"):
        configure_rs232_instrument(inst)
    else:
        inst.timeout = 10000
        # USBTMC機器では終端文字を明示しなくても動くことが多いが、
        # *IDN? の読み取りを安定させるため read_termination だけ設定を試みる。
        try:
            inst.read_termination = "\n"
        except Exception:
            pass
    return inst


def identify_instrument(idn):
    """*IDN?応答から機器種別を推定する。"""
    text = str(idn).upper()

    # できるだけ型番まで見る。型番が少し違う個体でも同系列なら拾えるようにする。
    if "TEXIO" in text and ("FGX-2112" in text or "FGX" in text):
        return "fg"
    if "KEITHLEY" in text and ("MODEL 2000" in text or ",2000," in text):
        return "dmm"
    if "TEKTRONIX" in text and ("TBS 1052B" in text or "TBS1052B" in text or "TBS" in text):
        return "scope"
    return "unknown"


def visa_role_label(role):
    """内部の機器種別名を表示名へ変換する。"""
    return {
        "fg": "FG / TEXIO FGX-2112",
        "dmm": "DMM / Keithley 2000",
        "scope": "Scope / Tektronix TBS1052B-EDU",
        "unknown": "Unknown",
    }.get(role, str(role))


def load_visa_config():
    """前回自動検出したVISAアドレスを読み込む。無ければ空設定を返す。"""
    config = {"fg": "", "dmm": "", "scope": ""}
    if VISA_CONFIG_PATH.exists():
        try:
            with open(VISA_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            for key in ["fg", "dmm", "scope"]:
                value = data.get(key)
                if value:
                    config[key] = _normalize_resource_name(value)
        except Exception:
            pass
    return config


def save_visa_config(config):
    """自動検出したVISAアドレス設定をresults/data/visa_config.jsonに保存する。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data = {"fg": "", "dmm": "", "scope": ""}
    data.update({k: _normalize_resource_name(v) for k, v in dict(config).items() if k in data and v})
    data["timestamp"] = datetime.now().isoformat(timespec="seconds")
    with open(VISA_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    return VISA_CONFIG_PATH


def _query_idn(inst):
    """機器へ*IDN?を送り、応答文字列を返す。"""
    try:
        inst.write("*CLS")
        time.sleep(0.05)
    except Exception:
        pass
    return inst.query("*IDN?").strip()


def _open_and_identify(rm, address):
    """1つのVISAリソースを開いて*IDN?で役割を判定する。"""
    inst = rm.open_resource(address)
    configure_instrument_by_address(inst, address)
    idn = _query_idn(inst)
    role = identify_instrument(idn)
    return inst, role, idn


def scan_visa_devices(query_idn=True, save=True):
    """接続中のVISA機器を列挙し、可能なら*IDN?で機器種別を判定する。"""
    rm = pyvisa.ResourceManager()
    resources = [_normalize_resource_name(a) for a in rm.list_resources()]
    rows = []
    detected = {}

    for address in resources:
        inst = None
        idn = ""
        status = "FOUND"
        role = "unknown"
        message = ""
        try:
            inst = rm.open_resource(address)
            configure_instrument_by_address(inst, address)
            if query_idn:
                idn = _query_idn(inst)
                role = identify_instrument(idn)
                status = "OK" if role != "unknown" else "UNKNOWN"
                if role != "unknown":
                    # 同じ役割が複数台ある場合は、後勝ちにせず最初に見つけたものを使う。
                    detected.setdefault(role, address)
        except Exception as e:
            status = "ERROR"
            message = str(e)
        finally:
            try:
                if inst is not None:
                    inst.close()
            except Exception:
                pass

        rows.append({
            "role": role,
            "role_label": visa_role_label(role),
            "address": address,
            "idn": idn,
            "status": status,
            "message": message,
        })

    config = load_visa_config()
    config.update(detected)
    if save:
        save_visa_config(config)

    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "resources": resources,
        "rows": rows,
        "config": config,
    }


def _format_scan_failure(role, resources, errors):
    """自動検出失敗時のエラーメッセージを作る。"""
    label = visa_role_label(role)
    lines = [
        f"{label} が見つかりませんでした。",
        "VISAリソース一覧を実際にスキャンしましたが、*IDN?応答から該当機器を判定できませんでした。",
        f"検出されたVISAリソース: {resources}",
    ]
    if errors:
        lines.append("スキャン中のエラー:")
        lines.extend(f"  {addr}: {err}" for addr, err in errors)
    lines.append("機器の電源、USB/RS-232接続、NI MAXで見えるか、COM番号の競合を確認してください。")
    return "\n".join(lines)


def open_resource_for_role(role, rescan=True, prefer_cache=True):
    """
    実際にVISAリソースをスキャンし、*IDN?応答から指定機器を開く。

    prefer_cache=True のときは、前回検出済みアドレスをまず試す。
    ただし、そのアドレスが存在しない・IDNが違う・openに失敗した場合は、
    自動的に全リソースを再スキャンして現在の正しいアドレスを使う。
    """
    role = str(role).lower()
    if role not in ["fg", "dmm", "scope"]:
        raise ValueError(f"未知のVISA roleです: {role}")

    rm = pyvisa.ResourceManager()
    resources = [_normalize_resource_name(a) for a in rm.list_resources()]
    config = load_visa_config()
    cached_address = _normalize_resource_name(config.get(role))
    errors = []

    # 前回検出済みアドレスが今も存在し、かつIDNが一致するならそれを使う。
    if prefer_cache and cached_address and cached_address in resources:
        try:
            inst, detected_role, idn = _open_and_identify(rm, cached_address)
            if detected_role == role:
                print(f"{role.upper()} found: {cached_address}")
                print(f"  IDN: {idn}")
                return inst, cached_address
            inst.close()
            errors.append((cached_address, f"別の機器として応答しました: {idn}"))
        except Exception as e:
            errors.append((cached_address, str(e)))

    if not rescan:
        raise VisaDeviceNotFound(_format_scan_failure(role, resources, errors))

    # 現在PCに見えている全VISAリソースを走査して、役割に合う機器を探す。
    for address in resources:
        if address == cached_address:
            continue
        inst = None
        try:
            inst, detected_role, idn = _open_and_identify(rm, address)
            if detected_role == role:
                config[role] = address
                save_visa_config(config)
                print(f"{role.upper()} found: {address}")
                print(f"  IDN: {idn}")
                return inst, address
            inst.close()
        except Exception as e:
            errors.append((address, str(e)))
            try:
                if inst is not None:
                    inst.close()
            except Exception:
                pass

    raise VisaDeviceNotFound(_format_scan_failure(role, resources, errors))


def query_idn_for_role(role):
    """指定機器へ*IDN?を送り、応答を返す。"""
    inst = None
    try:
        inst, address = open_resource_for_role(role)
        return {"ok": True, "role": role, "address": address, "value": inst.query("*IDN?").strip()}
    except Exception as e:
        return {"ok": False, "role": role, "address": load_visa_config().get(role, ""), "error": str(e)}
    finally:
        try:
            if inst is not None:
                inst.close()
        except Exception:
            pass


def test_fg_frequency_query():
    """FGの現在周波数をクエリして返す。"""
    inst = None
    try:
        inst, address = open_resource_for_role("fg")
        last_error = None
        for cmd in ["SOURce1:FREQuency?", "SOUR:FREQ?", "FREQ?"]:
            try:
                value = inst.query(cmd).strip()
                return {"ok": True, "role": "fg", "address": address, "command": cmd, "value": value}
            except Exception as e:
                last_error = e
        raise last_error
    except Exception as e:
        return {"ok": False, "role": "fg", "address": load_visa_config().get("fg", ""), "error": str(e)}
    finally:
        try:
            if inst is not None:
                inst.close()
        except Exception:
            pass


def test_fg_set_and_read_frequency(freq=1000.0, vpp=1.0, offset=0.0, output_on=False):
    """FGに正弦波条件を設定し、設定後の周波数を読み返す。"""
    inst = None
    try:
        inst, address = open_resource_for_role("fg")
        inst.write(f"SOUR:APPL:SIN {freq},{vpp},{offset}")
        inst.write("OUTP ON" if output_on else "OUTP OFF")
        time.sleep(0.5)
        value = inst.query("SOUR:FREQ?").strip()
        return {"ok": True, "role": "fg", "address": address, "command": "SOUR:APPL:SIN / SOUR:FREQ?", "value": value}
    except Exception as e:
        return {"ok": False, "role": "fg", "address": load_visa_config().get("fg", ""), "error": str(e)}
    finally:
        try:
            if inst is not None:
                inst.close()
        except Exception:
            pass


def test_dmm_read_current_function():
    """DMMの現在設定のままREAD?を実行して値を返す。"""
    inst = None
    try:
        inst, address = open_resource_for_role("dmm")
        value = inst.query("READ?").strip()
        return {"ok": True, "role": "dmm", "address": address, "command": "READ?", "value": value}
    except Exception as e:
        return {"ok": False, "role": "dmm", "address": load_visa_config().get("dmm", ""), "error": str(e)}
    finally:
        try:
            if inst is not None:
                inst.close()
        except Exception:
            pass


def test_dmm_ac_voltage_read():
    """DMMをAC電圧測定に設定してREAD?を実行する。"""
    inst = None
    try:
        inst, address = open_resource_for_role("dmm")
        inst.write("CONF:VOLT:AC")
        time.sleep(0.2)
        value = inst.query("READ?").strip()
        return {"ok": True, "role": "dmm", "address": address, "command": "CONF:VOLT:AC / READ?", "value": value}
    except Exception as e:
        return {"ok": False, "role": "dmm", "address": load_visa_config().get("dmm", ""), "error": str(e)}
    finally:
        try:
            if inst is not None:
                inst.close()
        except Exception:
            pass


def test_scope_ch1_pkpk():
    """オシロCH1のPK2PK測定値を返す。"""
    inst = None
    try:
        inst, address = open_resource_for_role("scope")
        inst.write("MEASUrement:IMMed:SOUrce CH1")
        inst.write("MEASUrement:IMMed:TYPE PK2PK")
        time.sleep(0.2)
        value = inst.query("MEASUrement:IMMed:VALue?").strip()
        return {"ok": True, "role": "scope", "address": address, "command": "MEAS:IMM CH1 PK2PK", "value": value}
    except Exception as e:
        return {"ok": False, "role": "scope", "address": load_visa_config().get("scope", ""), "error": str(e)}
    finally:
        try:
            if inst is not None:
                inst.close()
        except Exception:
            pass


def test_all_idn():
    """FG、DMM、Scopeの*IDN?確認をまとめて実行する。"""
    return [query_idn_for_role(role) for role in ["fg", "dmm", "scope"]]

# ==========================================
# ベクトル図用JSON
# ==========================================
VECTOR_JSON = DATA_DIR / "current_vector_results.json"


def timestamp():
    """現在時刻をファイル名用文字列に変換する。"""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def safe_std(values):
    """標本標準偏差を返す。データが1個以下なら0を返す。"""
    arr = np.asarray(values, dtype=float)
    if arr.size <= 1:
        return 0.0
    return float(np.std(arr, ddof=1))


def mean_std(values):
    """平均値と標本標準偏差を返す。"""
    arr = np.asarray(values, dtype=float)
    return float(np.mean(arr)), safe_std(arr)


def stats_rows(rows, columns):
    """辞書のリストから指定列の平均値・標準偏差を計算する。"""
    out = {}
    for col in columns:
        values = [row[col] for row in rows]
        out[f"{col}_mean"], out[f"{col}_std"] = mean_std(values)
    return out


def complex_mean(values):
    """複素数フェーザの平均を返す。"""
    arr = np.asarray(values, dtype=complex)
    return complex(np.mean(arr))


def phase_deg(z):
    """複素数の偏角をdegreeで返す。"""
    return math.degrees(math.atan2(z.imag, z.real))


def open_instruments(use_fg=True, use_dmm=True, use_scope=True):
    """保存済みVISAアドレスを使って、fg, dmm, scopeの順に接続して返す。"""
    fg = None
    dmm = None
    scope = None

    if use_fg:
        fg, _ = open_resource_for_role("fg")

    if use_dmm:
        dmm, _ = open_resource_for_role("dmm")

    if use_scope:
        scope, _ = open_resource_for_role("scope")

    return fg, dmm, scope


def print_idn(fg=None, dmm=None, scope=None):
    """接続機器の*IDN?応答を表示する。"""
    if fg is not None:
        print("FG:", fg.query("*IDN?").strip())
    if dmm is not None:
        print("DMM:", dmm.query("*IDN?").strip())
    if scope is not None:
        print("Scope:", scope.query("*IDN?").strip())


def setup_fg_sine(fg, freq, vpp, offset=0.0):
    """発振器をリセットし、正弦波を設定して出力をONにする。"""
    fg.write("*RST")
    time.sleep(1)
    fg.write(f"SOUR:APPL:SIN {freq},{vpp},{offset}")
    fg.write("OUTP ON")


def set_fg_sine(fg, freq, vpp, offset=0.0):
    """発振器の正弦波条件を変更する。"""
    fg.write(f"SOUR:APPL:SIN {freq},{vpp},{offset}")
    fg.write("OUTP ON")
# def set_fg_sine(fg, freq, vpp, offset):
#     fg.write("*CLS")
#     time.sleep(0.1)
#     fg.write("SOUR1:FUNC SIN")
#     time.sleep(0.1)
#     fg.write(f"SOUR1:FREQ {freq}")
#     time.sleep(0.1)
#     fg.write(f"SOUR1:AMPL {vpp}")
#     time.sleep(0.1)
#     fg.write(f"SOUR1:DCO {offset}")
#     time.sleep(0.1)
#     fg.write("OUTP ON")
#     time.sleep(0.3)


def fg_output_off(fg):
    """発振器出力をOFFにする。"""
    try:
        if fg is not None:
            fg.write("OUTP OFF")
    except Exception:
        pass


def setup_scope_time_scale(scope, freq):
    """オシロの時間軸を1目盛り=T/4に設定する。"""
    scope.write(f"HORizontal:MAIn:SCAle {1 / freq / 4}")


def setup_scope_time_scale_for_fft(scope, freq, n_periods=5, margin=1.2):
    """FFT用に、n_periods周期以上が画面全体に入る時間軸へ設定する。"""
    T = 1 / freq
    horizontal_divs = 10
    # オシロ画面は横10divなので、全体で n_periods * margin 周期入るようにする
    time_scale = (n_periods * margin * T) / horizontal_divs

    scope.write(f"HORizontal:MAIn:SCAle {time_scale}")


def setup_scope_average(scope, count=16):
    """オシロの波形取得を平均化モードにし、平均回数を設定する。"""
    scope.write("ACQuire:MODe AVErage")
    scope.write(f"ACQuire:NUMAVg {count}")


def setup_scope_external_trigger(scope, freq=None, average_count=16):
    """オシロを外部トリガ・平均化に設定し、freq指定時は時間軸も設定する。"""
    scope.write("TRIGger:MAIn:TYPe EDGE")
    scope.write("TRIGger:MAIn:EDGE:SOUrce EXT")
    scope.write("TRIGger:MAIn:EDGE:SLOpe RISE")
    scope.write("TRIGger:MAIn:EDGE:COUPling DC")
    scope.write("TRIGger:MAIn:LEVel 0.0")
    scope.write("TRIGger:MAIn:MODe AUTO")
    setup_scope_average(scope, average_count)
    if freq is not None:
        setup_scope_time_scale(scope, freq)


def setup_ch1_ch2_math_subtract(scope):
    """CH1, CH2, MATH=CH1-CH2を表示・設定する。

    TBS1000B/TBS1052B-EDUでは、MATH波形は SELECT:MATH ON で表示する。
    MATH:DISPlay ON や MENU OFF はこの機種でエラー要因になり得るため送らない。
    """
    # 直前の不正コマンド等が残っていると、後続の測定クエリが不安定になることがあるため消去する。
    try:
        scope.write("*CLS")
        time.sleep(0.05)
    except Exception:
        pass

    commands = [
        "SELect:CH1 ON",
        "SELect:CH2 ON",
        'MATH:DEFINE "CH1-CH2"',
        "SELect:MATH ON",
    ]
    for cmd in commands:
        scope.write(cmd)
        time.sleep(0.08)

    # 表示反映待ち。特にリセット直後はMATH表示が遅れることがある。
    time.sleep(0.5)


def show_only_ch1(scope):
    """CH1だけを表示する。"""
    scope.write("SELect:CH1 ON")
    scope.write("SELect:CH2 OFF")
    scope.write("SELect:MATH OFF")


def measure_pk2pk_rms(scope, source):
    """オシロのPK2PK値から正弦波RMS値を計算する。"""
    scope.write(f"MEASUrement:IMMed:SOUrce {source}")
    scope.write("MEASUrement:IMMed:TYPE PK2PK")
    vpp = float(scope.query("MEASUrement:IMMed:VALue?"))
    vrms = vpp / (2 * math.sqrt(2))
    return vpp, vrms



def _scope_write_first_available(scope, commands):
    """候補コマンドを順に送り、少なくとも1つ成功したかを返す。"""
    ok = False
    last_error = None
    for cmd in commands:
        try:
            scope.write(cmd)
            ok = True
            time.sleep(0.05)
        except Exception as e:
            last_error = e
    if not ok and last_error is not None:
        raise last_error
    return ok


def set_vertical_scale(scope, channel="CH1", scale=1.0, position=0.0):
    """CH1/CH2/MATHの縦軸スケールと縦位置を設定する。"""
    source = channel.upper()

    if source == "MATH":
        scale_commands = [
            f"MATH:VERTical:SCAle {scale}",
            f"MATH:VERT:SCAle {scale}",
            f"MATH:SCAle {scale}",
        ]
        position_commands = [
            f"MATH:VERTical:POSition {position}",
            f"MATH:VERT:POSition {position}",
            f"MATH:POSition {position}",
        ]
    else:
        scale_commands = [f"{source}:SCAle {scale}"]
        position_commands = [f"{source}:POSition {position}"]

    _scope_write_first_available(scope, scale_commands)
    _scope_write_first_available(scope, position_commands)


def auto_adjust_vertical_scale(scope, channel="CH1", safe_scale=1.0, target_div=6.0,
                               margin=1.3, min_scale=0.02, max_scale=5.0,
                               settle_after_safe=1.0, settle_after_position=0.5,
                               settle_after_scale=1.0):
    """
    オシロの指定チャンネルの縦軸スケールを自動調整する。

    CH1/CH2などの通常チャンネルとMATHではSCPIコマンド体系が違うため、
    MATH指定時はMATH:VERTical:SCAle系のコマンドを優先して送る。
    """
    source = channel.upper()

    # まず安全な大きめスケールにする
    set_vertical_scale(scope, source, safe_scale, 0.0)
    time.sleep(settle_after_safe)

    # 一旦中央へ
    set_vertical_scale(scope, source, safe_scale, 0.0)
    time.sleep(settle_after_position)

    # 大きめスケールの状態でpk-pkを測定する
    pkpk, _ = measure_pk2pk_rms(scope, source)

    # 異常値対策
    if pkpk <= 0 or np.isnan(pkpk):
        pkpk = safe_scale * target_div

    # 画面高さ8divのうち約target_div divを使う
    target_scale = pkpk / target_div
    target_scale *= margin
    target_scale = max(target_scale, min_scale)
    target_scale = min(target_scale, max_scale)

    set_vertical_scale(scope, source, target_scale, 0.0)

    print(
        f"  auto vertical scale ({source}): "
        f"pkpk = {pkpk:.5f} V, "
        f"scale = {target_scale:.5f} V/div"
    )

    time.sleep(settle_after_scale)
    return target_scale


def auto_adjust_math_vertical_scale(scope, safe_scale=1.0, target_div=6.0,
                                    margin=1.3, min_scale=0.02, max_scale=5.0):
    """MATH=CH1-CH2用の安定した縦軸オートスケール。"""

    set_vertical_scale(scope, "MATH", safe_scale, 0.0)
    time.sleep(1.0)

    pkpk_ch1, _ = measure_pk2pk_rms(scope, "CH1")
    pkpk_ch2, _ = measure_pk2pk_rms(scope, "CH2")

    # CH1-CH2の最大振幅を安全側に見積もる
    pkpk = pkpk_ch1 + pkpk_ch2

    if pkpk <= 0 or np.isnan(pkpk):
        pkpk = safe_scale * target_div

    target_scale = pkpk / target_div * margin
    target_scale = max(min_scale, min(target_scale, max_scale))

    set_vertical_scale(scope, "MATH", target_scale, 0.0)
    time.sleep(1.0)

    print(
        f"  auto vertical scale (MATH): "
        f"estimated pkpk = {pkpk:.5f} V, "
        f"scale = {target_scale:.5f} V/div"
    )

    return target_scale


def save_csv(filename, header, rows):
    """CSVファイルをresults/dataに保存する。"""
    path = DATA_DIR / filename
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def save_json(path, data):
    """JSONファイルを保存する。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def load_json(path, default=None):
    """JSONファイルを読み込む。存在しない場合はdefaultを返す。"""
    path = Path(path)
    if not path.exists():
        return {} if default is None else default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)




def save_representative_values(filename, title, lines, values=None):
    """代表値をJSONとしてresults/dataに保存する。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / filename
    data = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "title": title,
        "lines": list(lines),
        "values": values or {},
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    return path


def save_vector_result(kind, data):
    """ベクトル図用測定結果をJSONへ保存する。"""
    results = load_json(VECTOR_JSON, {})
    results[kind] = data
    save_json(VECTOR_JSON, results)
    return VECTOR_JSON


def append_run_log(script_name, outputs):
    """実行したスクリプト名と出力ファイルをrun_log.csvに追記する。"""
    log_path = RESULT_DIR / "run_log.csv"
    is_new = not log_path.exists()
    with open(log_path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["timestamp", "script", "output"])
        now = datetime.now().isoformat(timespec="seconds")
        for output in outputs:
            writer.writerow([now, script_name, str(output)])


def save_scope_screenshot(scope, filename):
    """オシロ画面をJPEG画像として保存する。"""
    path = image_path(filename)
    scope.write("HARDCopy:FORMat JPEG")
    scope.write("HARDCopy:INKSaver OFF")
    time.sleep(0.5)
    scope.write("HARDCopy STARt")
    time.sleep(1.0)
    data = scope.read_raw()
    start = data.find(b"\xff\xd8")
    end = data.rfind(b"\xff\xd9")
    if start != -1 and end != -1:
        data = data[start:end + 2]
    with open(path, "wb") as f:
        f.write(data)
    return path


def read_waveform(scope, source):
    """オシロから波形データを取得し、時間軸と電圧配列を返す。"""
    scope.write(f"DATa:SOUrce {source}")
    scope.write("DATa:ENCdg RIBinary")
    scope.write("DATa:WIDth 1")
    scope.write("DATa:STARt 1")
    scope.write("DATa:STOP 2500")

    ymult = float(scope.query("WFMPRE:YMULT?"))
    yzero = float(scope.query("WFMPRE:YZERO?"))
    yoff = float(scope.query("WFMPRE:YOFF?"))
    xincr = float(scope.query("WFMPRE:XINCR?"))
    xzero = float(scope.query("WFMPRE:XZERO?"))
    pt_off = float(scope.query("WFMPRE:PT_OFF?"))

    scope.write("CURVe?")
    raw = scope.read_raw()
    header_start = raw.find(b"#")
    if header_start == -1:
        raise RuntimeError("ブロックヘッダが見つかりません")

    n_digits = int(raw[header_start + 1:header_start + 2])
    n_bytes = int(raw[header_start + 2:header_start + 2 + n_digits])
    data_start = header_start + 2 + n_digits
    data_end = data_start + n_bytes
    data = raw[data_start:data_end]

    adc = np.frombuffer(data, dtype=np.int8)
    voltage = (adc - yoff) * ymult + yzero
    time_axis = xzero + (np.arange(len(voltage)) - pt_off) * xincr
    return time_axis, voltage, xincr


# def fft_phasor_one_period(time_axis, voltage, freq):
#     """1周期分をFFTして、基本波のRMS複素フェーザを求める。"""
#     dt = time_axis[1] - time_axis[0]
#     T = 1 / freq
#     M = int(round(T / dt))
#     if M <= 1:
#         raise RuntimeError("Mが小さすぎます")
#     if M > len(voltage):
#         raise RuntimeError("データ長不足")

#     v = voltage[:M]
#     v = v - np.mean(v)
#     V = np.fft.rfft(v)
#     complex_amp_peak = 2 * V[1] / M
#     phasor_rms = complex_amp_peak / math.sqrt(2)
#     rms = abs(phasor_rms)
#     phase = phase_deg(phasor_rms)
#     return phasor_rms, rms, phase, M, dt, M * dt
def fft_phasor_n_period(voltage, dt, freq, n_periods=4):
    """n周期分をFFTして、基本波のRMS複素フェーザを求める。"""
    T = 1 / freq

    # n周期分の点数
    M = int(round(n_periods * T / dt))

    if M <= 1:
        raise RuntimeError("Mが小さすぎます")
    if M > len(voltage):
        raise RuntimeError("データ長不足")

    v = voltage[:M]
    v = v - np.mean(v)

    V = np.fft.rfft(v)

    # n周期分を切り出すと、基本波は k = n_periods 番目のビン
    k = n_periods

    complex_amp_peak = 2 * V[k] / M
    phasor_rms = complex_amp_peak / math.sqrt(2)

    rms = abs(phasor_rms)
    phase = phase_deg(phasor_rms)

    print(
        f"freq={freq:.1f} Hz, "
        f"dt={dt:.9e} s, "
        f"M={M}, "
        f"Mdt={M*dt:.9e} s, "
        f"nT={n_periods/freq:.9e} s"
    )

    return phasor_rms, rms, phase, M, dt, M * dt


# ==========================================
# リアルタイム表示・停止要求・逐次CSV保存
# ==========================================

class MeasurementStopped(RuntimeError):
    """GUIから停止要求が出されたときに送出する例外。"""
    pass


STOP_REQUEST_PATH = DATA_DIR / "stop_requested.flag"


def init_csv(filename, header):
    """CSVファイルを新規作成し、ヘッダーだけを書き込む。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / filename
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(header)
    return path


def init_realtime_csv(filename, header):
    """リアルタイム更新用CSVを初期化する。init_csvの別名。"""
    return init_csv(filename, header)


def append_csv_row(filename, row):
    """CSVファイルへ1行追記する。測定途中のリアルタイム表示に使う。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / filename
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(row)
        f.flush()
    return path


def pbl_progress(current, total, frequency=None, stage="", message=""):
    """launcher.pyが読めるJSON形式で測定進捗を標準出力へ出す。"""
    payload = {
        "pbl_progress": {
            "current": current,
            "total": total,
            "frequency": frequency,
            "stage": stage,
            "message": message,
        }
    }
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def emit_progress(current, total, frequency=None, stage="", message=""):
    """pbl_progressの互換名。"""
    pbl_progress(current, total, frequency=frequency, stage=stage, message=message)


def print_progress(current, total, frequency=None, stage="", message=""):
    """pbl_progressの互換名。"""
    pbl_progress(current, total, frequency=frequency, stage=stage, message=message)


def clear_stop_request():
    """停止要求ファイルを削除する。"""
    try:
        STOP_REQUEST_PATH.unlink(missing_ok=True)
    except TypeError:
        if STOP_REQUEST_PATH.exists():
            STOP_REQUEST_PATH.unlink()


def stop_requested():
    """停止要求が出ているかを返す。"""
    return STOP_REQUEST_PATH.exists()


def check_stop_requested():
    """停止要求が出ていればMeasurementStoppedを送出する。"""
    if stop_requested():
        raise MeasurementStopped("GUIから停止要求が出されました。")


def raise_if_stop_requested():
    """check_stop_requestedの互換名。"""
    check_stop_requested()

