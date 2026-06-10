import csv
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import queue
import re
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    Image = None
    ImageTk = None
    PIL_AVAILABLE = False

try:
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    MATPLOTLIB_AVAILABLE = True
except Exception:
    FigureCanvasTkAgg = None
    Figure = None
    MATPLOTLIB_AVAILABLE = False

try:
    import pbl_common
    PBL_COMMON_AVAILABLE = True
except Exception as e:
    pbl_common = None
    PBL_COMMON_AVAILABLE = False
    PBL_COMMON_IMPORT_ERROR = e


# PyInstaller対応:
# 通常実行時 -> launcher.py の場所
# exe実行時   -> launcher.exe の場所
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

RESULT_DIR = BASE_DIR / "results"
IMAGE_DIR = RESULT_DIR / "images"
DATA_DIR = RESULT_DIR / "data"

# 画像は種類ごとのサブフォルダから読み込む。
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


def image_subdir_for_pattern(pattern: str) -> str:
    """画像ファイル名パターンに対応するサブフォルダ名を返す。"""
    prefix = pattern.split("_*")[0]
    return IMAGE_SUBDIRS.get(prefix, "")

IMAGE_MAX_WIDTH = 900
IMAGE_MAX_HEIGHT = 850
CSV_TREE_MAX_VISIBLE_ROWS = 12
# 画像ビューアで左ドラッグしたときの移動感度。
# 1.0でマウス移動量と同じ、1.2なら約120%だけ移動する。
IMAGE_VIEWER_DRAG_SENSITIVITY = 1.2
IMAGE_THUMBNAIL_CACHE = {}
RUNNING_PROCESS = None
RUNNING_TAB = None
RUNNING_BUTTON = None
RUNNING_START_TIME = None
STDOUT_QUEUE = queue.Queue()


def get_subprocess_no_console_kwargs():
    """Windowsで測定コード起動時にコマンドプロンプトを表示しないためのPopen追加設定を返す。"""
    if os.name != "nt":
        return {}

    kwargs = {}

    # Pythonを python.exe / py.exe から起動しても、黒いコンソール窓を新しく出さない。
    # subprocess.CREATE_NO_WINDOW はWindows専用なので、存在確認してから使う。
    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if create_no_window:
        kwargs["creationflags"] = create_no_window

    # 念のためSTARTUPINFO側でも非表示指定を入れる。
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    kwargs["startupinfo"] = startupinfo

    return kwargs


NOTE = (
    "各測定コードは同一条件で複数回測定し、平均値と標準偏差を results に保存します。\n"
    "測定完了後、この画面の各タブに最新の表と画像を自動表示します。\n"
    "4→5→6 はこの順で実行してください。\n"
    "全ての測定において、トリガとしてオシロのSYNC出力を使用し、トリガレベルは0.0 Vとします。"
)


# GUI color palette: 白基調＋薄い色のボタン
COLOR_BG = "#ffffff"
COLOR_TEXT = "#111111"
COLOR_MUTED = "#6b7280"
COLOR_BORDER = "#d9e2ec"
COLOR_TAB_INACTIVE = "#eaf7ff"
COLOR_BUTTON = "#eef6ff"
COLOR_BUTTON_ACTIVE = "#dceeff"
COLOR_UPDATE_BUTTON = "#e8f5ff"
COLOR_UPDATE_BUTTON_ACTIVE = "#d7ecfb"
COLOR_RUN_BUTTON = "#ffecec"
COLOR_RUN_BUTTON_ACTIVE = "#ffdada"
COLOR_PANEL = "#ffffff"
COLOR_TEXTBOX = "#ffffff"
COLOR_ERROR = "#b91c1c"
COLOR_STOP_BUTTON = "#fee2e2"
COLOR_STOP_BUTTON_ACTIVE = "#fecaca"


@dataclass(frozen=True)
class ScriptConfig:
    label: str
    script: str
    csv_pattern: str
    image_pattern: str
    representative_json: str
    conditions: str

    @property
    def image_patterns(self):
        """このタブで表示する画像パターンの一覧を返す。"""
        if self.label.startswith("4-6 "):
            return [
                "kirchhoff_capacitor_*.jpg",
                "kirchhoff_coil_*.jpg",
                "vector_diagram_theory_and_exp_*.png",
            ]
        if self.label.startswith("7 "):
            return [
                "resonance_impedance_errorbar_*.png",
                "resonance_impedance_fine_only_*.png",
            ]
        return [self.image_pattern]


SCRIPTS = [
    ScriptConfig(
        "1 リアクタンス測定(コイル)",
        "1measurement_of_reactance_coil.py",
        "reactance_coil_summary_*.csv",
        "reactance_coil_errorbar_fit_*.png",
        "reactance_coil_representative.json",
        (
            "測定条件\n"
            "  周波数: 200, 500, 1000, 2000, 5000 Hz\n"
            "  発振器: sine, VPP = 1.0 V, offset = 0.0 V\n"
            "  繰り返し回数: N_REPEAT = 10\n"
            "  測定間隔: REPEAT_INTERVAL = 0.5 s\n\n"
            "測定量\n"
            "  オシロ CH1: コイル両端電圧 VL\n"
            "  DMM: 交流電流 Irms\n"
            "  XL = Vrms / Irms\n"
            "  L = 傾き / (2π)\n\n"
            "出力\n"
            "  reactance_coil_summary_*.csv\n"
            "  reactance_coil_errorbar_fit_*.png"
        ),
    ),
    ScriptConfig(
        "2 リアクタンス測定(キャパシタ)",
        "2measurement_of_reactance_capacitor.py",
        "reactance_capacitor_summary_*.csv",
        "reactance_capacitor_errorbar_fit_*.png",
        "reactance_capacitor_representative.json",
        (
            "測定条件\n"
            "  周波数: 200, 500, 1000, 2000, 5000 Hz\n"
            "  発振器: sine, VPP = 1.0 V, offset = 0.0 V\n"
            "  繰り返し回数: N_REPEAT = 10\n"
            "  測定間隔: REPEAT_INTERVAL = 0.5 s\n\n"
            "測定量\n"
            "  オシロ CH1: キャパシタ両端電圧 VC\n"
            "  DMM: 交流電流 Irms\n"
            "  XC = Vrms / Irms\n"
            "  C = 1 / (2π × 傾き)\n\n"
            "出力\n"
            "  reactance_capacitor_summary_*.csv\n"
            "  reactance_capacitor_errorbar_fit_*.png"
        ),
    ),
    ScriptConfig(
        "3 フロー電位測定",
        "3measurement_of_floating_voltage.py",
        "flow_voltage_summary_*.csv",
        "flow_voltage_*.jpg",
        "flow_voltage_representative.json",
        (
            "測定条件\n"
            "  周波数: FREQ = 5000 Hz\n"
            "  発振器: sine, VPP = 3.0 V, offset = 0.0 V\n"
            "  既知抵抗: R_KNOWN = 56.0 Ω\n"
            "  繰り返し回数: N_REPEAT = 10\n"
            "  測定間隔: REPEAT_INTERVAL = 0.5 s\n\n"
            "測定量\n"
            "  CH1: 全体電圧 Vtotal = VL + VR\n"
            "  CH2: 抵抗電圧 VR\n"
            "  MATH = CH1 - CH2 = コイル電圧 VL\n"
            "  Irms = VR_rms / R_KNOWN\n"
            "  XL = VL_rms / Irms\n"
            "  L = XL / (2πf)\n\n"
            "出力\n"
            "  flow_voltage_summary_*.csv\n"
            "  flow_voltage_*.jpg"
        ),
    ),
    ScriptConfig(
        "4-6 キルヒッフ測定",
        "6kirchhoffs_laws_phasor_diagram.py",
        "vector_diagram_*.csv",
        "vector_diagram_theory_and_exp_*.png",
        "vector_diagram_representative.json",
        (
            "測定条件\n"
            "  4・5・6を1つのタブにまとめています。\n"
            "  アプリ上の共通周波数欄で、4と5に同じFREQを渡します。\n"
            "  4: キャパシタ測定、5: コイル測定、6: ベクトル図作成\n"
            "  推奨実行順序: 4 → 5 → 6\n\n"
            "配線\n"
            "  接続: 発振器 → L → C → R → GND\n"
            "  4では CH1: Cの前, CH2: Cの後 = R上端, MATH = VC\n"
            "  5では CH1: Lの前, CH2: Lの後, MATH = VL\n\n"
            "注意\n"
            "  4と5は同じ周波数で実行してください。\n"
            "  6は4・5で保存された current_vector_results.json を読み込んで作図します。\n\n"
            "出力\n"
            "  kirchhoff_capacitor_*.jpg\n"
            "  kirchhoff_coil_*.jpg\n"
            "  vector_diagram_theory_and_exp_*.png\n"
            "  vector_diagram_*.csv"
        ),
    ),
    ScriptConfig(
        "7 共振現象",
        "7resonance.py",
        "resonance_impedance_summary_*.csv",
        "resonance_impedance_errorbar_*.png",
        "resonance_representative.json",
        (
            "測定条件\n"
            "  発振器: sine, VPP = 1.0 V, offset = 0.0 V\n"
            "  coarse scan: 100 ～ 10000 Hz\n"
            "  coarse points: 30点\n"
            "  fine scan: coarse最小周波数の ±30 %\n"
            "  fine points: 100点\n"
            "  繰り返し回数: N_REPEAT = 5\n"
            "  SETTLE_TIME = 4.0 s\n"
            "  REPEAT_INTERVAL = 0.5 s\n\n"
            "理論値\n"
            "  L = 2.8 mH\n"
            "  C = 4.7 μF\n"
            "  理論共振周波数 f0 ≈ 1387 Hz\n\n"
            "測定量\n"
            "  CH1: VC + VL\n"
            "  DMM: 交流電流 Irms\n"
            "  Z = Vrms / Irms\n"
            "  coarse scanで最小点を探し、その近傍±30%をfine scanする\n\n"
            "出力\n"
            "  resonance_impedance_summary_*.csv\n"
            "  resonance_impedance_errorbar_*.png\n"
            "  resonance_impedance_fine_only_*.png"
        ),
    ),
]





class VisaTab:
    """VISAアドレスの自動検出結果を表示するタブ。"""
    def __init__(self, notebook: ttk.Notebook):
        self.frame = ttk.Frame(notebook, style="TFrame")
        notebook.add(self.frame, text="Address")

        self.content = ttk.Frame(self.frame, padding=10, style="TFrame")
        self.content.pack(fill="both", expand=True)

        title_frame = ttk.Frame(self.content, style="TFrame")
        title_frame.pack(fill="x", pady=(0, 8))

        ttk.Label(
            title_frame,
            text="0 VISA接続確認",
            font=("Yu Gothic", 12, "bold"),
        ).pack(side="left")

        ttk.Button(
            title_frame,
            text="↻ 再スキャン",
            command=self.scan_async,
            style="Update.TButton",
        ).pack(side="right")

        self.summary_label = ttk.Label(
            self.content,
            text="VISAスキャン未実行",
            foreground=COLOR_MUTED,
        )
        self.summary_label.pack(anchor="w", pady=(0, 6))

        table_frame = ttk.Frame(self.content, style="TFrame")
        table_frame.pack(fill="both", expand=True)

        columns = ["role", "address", "status", "idn", "message"]
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=10)
        for col, heading, width in [
            ("role", "推定機器", 180),
            ("address", "VISA Address", 260),
            ("status", "状態", 90),
            ("idn", "*IDN? 応答", 420),
            ("message", "エラー/補足", 260),
        ]:
            self.tree.heading(col, text=heading)
            self.tree.column(col, width=width, anchor="w", stretch=True)

        y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        x_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        config_section = AccordionSection(self.content, "現在使用するアドレス", opened=True)
        self.config_text = tk.Text(
            config_section.body,
            height=5,
            wrap="word",
            font=("Yu Gothic", 10),
            bg=COLOR_TEXTBOX,
            fg=COLOR_TEXT,
            insertbackground=COLOR_TEXT,
            relief="flat",
        )
        self.config_text.pack(fill="x", expand=False)
        self.config_text.configure(state="disabled")

        note_section = AccordionSection(self.content, "使い方", opened=False)
        ttk.Label(
            note_section.body,
            text=(
                "起動時または再スキャン時に取得したVISAアドレスを results/data/visa_config.json に保存します。\n"
                "測定コードは pbl_common.open_instruments() 経由で、この保存済みアドレスを使って毎回 open_resource() します。\n"
                "通信が不安定な場合は、機器の電源・USB/RS-232接続を確認してから再スキャンしてください。"
            ),
            justify="left",
        ).pack(anchor="w")

        self.refresh_config_text()

    def refresh_config_text(self):
        self.config_text.configure(state="normal")
        self.config_text.delete("1.0", "end")
        if not PBL_COMMON_AVAILABLE:
            self.config_text.insert("1.0", f"pbl_common.pyを読み込めませんでした: {PBL_COMMON_IMPORT_ERROR}")
        else:
            config = pbl_common.load_visa_config()
            lines = [
                f"FG    : {config.get('fg', '')}",
                f"DMM   : {config.get('dmm', '')}",
                f"Scope : {config.get('scope', '')}",
                f"保存先: {pbl_common.VISA_CONFIG_PATH}",
            ]
            self.config_text.insert("1.0", "\n".join(lines))
        self.config_text.configure(state="disabled")

    def scan_async(self):
        if not PBL_COMMON_AVAILABLE:
            messagebox.showerror("エラー", f"pbl_common.pyを読み込めませんでした。\n\n{PBL_COMMON_IMPORT_ERROR}")
            return
        set_status("VISAスキャン中")
        self.summary_label.config(text="VISAスキャン中...", foreground=COLOR_MUTED)
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self):
        try:
            result = pbl_common.scan_visa_devices(query_idn=True, save=True)
            root.after(0, lambda: self.apply_scan_result(result))
        except Exception as e:
            root.after(0, lambda: self.scan_failed(e))

    def scan_failed(self, error):
        self.summary_label.config(text=f"VISAスキャン失敗: {error}", foreground=COLOR_ERROR)
        set_status("VISAスキャン失敗")

    def apply_scan_result(self, result):
        for item in self.tree.get_children():
            self.tree.delete(item)

        rows = result.get("rows", [])
        for row in rows:
            self.tree.insert(
                "",
                "end",
                values=(
                    row.get("role_label", row.get("role", "")),
                    row.get("address", ""),
                    row.get("status", ""),
                    row.get("idn", ""),
                    row.get("message", ""),
                ),
            )

        config = result.get("config", {})
        found = ", ".join(f"{k}={v}" for k, v in config.items() if k in ["fg", "dmm", "scope"])
        self.summary_label.config(
            text=f"スキャン完了: {len(rows)} 個のVISAリソースを確認 / {found}",
            foreground=COLOR_TEXT,
        )
        self.refresh_config_text()
        set_status("VISAスキャン完了")


class TestTab:
    """機器の簡易動作確認を実行するタブ。"""
    def __init__(self, notebook: ttk.Notebook, visa_tab: VisaTab):
        self.visa_tab = visa_tab
        self.frame = ttk.Frame(notebook, style="TFrame")
        notebook.add(self.frame, text="Test")

        self.content = ttk.Frame(self.frame, padding=10, style="TFrame")
        self.content.pack(fill="both", expand=True)

        ttk.Label(
            self.content,
            text="動作テスト",
            font=("Yu Gothic", 12, "bold"),
        ).pack(anchor="w", pady=(0, 8))

        ttk.Label(
            self.content,
            text="各ボタンは保存済みVISAアドレスを使って、その機器だけを open_resource() して確認します。",
            foreground=COLOR_MUTED,
        ).pack(anchor="w", pady=(0, 8))

        button_grid = ttk.Frame(self.content, style="TFrame")
        button_grid.pack(fill="x", pady=(0, 8))

        tests = [
            ("全機器 *IDN?", self.test_all_idn),
            ("FG *IDN?", lambda: pbl_common.query_idn_for_role("fg")),
            ("FG 周波数取得", pbl_common.test_fg_frequency_query),
            ("FG 1000Hz設定→取得", pbl_common.test_fg_set_and_read_frequency),
            ("DMM *IDN?", lambda: pbl_common.query_idn_for_role("dmm")),
            ("DMM READ?", pbl_common.test_dmm_read_current_function),
            ("DMM AC電圧 READ?", pbl_common.test_dmm_ac_voltage_read),
            ("Scope *IDN?", lambda: pbl_common.query_idn_for_role("scope")),
            ("Scope CH1 PK2PK", pbl_common.test_scope_ch1_pkpk),
        ]

        for index, (label, func) in enumerate(tests):
            row = index // 3
            col = index % 3
            ttk.Button(
                button_grid,
                text=label,
                command=lambda f=func, name=label: self.run_test_async(name, f),
                style="TButton",
            ).grid(row=row, column=col, sticky="ew", padx=4, pady=4)

        for col in range(3):
            button_grid.columnconfigure(col, weight=1)

        ttk.Button(
            self.content,
            text="結果をクリア",
            command=self.clear_log,
            style="Update.TButton",
        ).pack(anchor="e", pady=(0, 4))

        log_frame = ttk.Frame(self.content, style="TFrame")
        log_frame.pack(fill="both", expand=True)

        self.log = tk.Text(
            log_frame,
            height=24,
            wrap="word",
            font=("Consolas", 10),
            bg=COLOR_TEXTBOX,
            fg=COLOR_TEXT,
            insertbackground=COLOR_TEXT,
            relief="groove",
        )
        log_scroll = ttk.Scrollbar(
            log_frame,
            orient="vertical",
            command=self.log.yview,
        )
        self.log.configure(yscrollcommand=log_scroll.set)
        self.log.grid(row=0, column=0, sticky="nsew")
        log_scroll.grid(row=0, column=1, sticky="ns")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.log.insert("1.0", "テスト結果がここに表示されます。\n")
        self.log.configure(state="disabled")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def append_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def run_test_async(self, name, func):
        if not PBL_COMMON_AVAILABLE:
            messagebox.showerror("エラー", f"pbl_common.pyを読み込めませんでした。\n\n{PBL_COMMON_IMPORT_ERROR}")
            return
        self.append_log(f"\n--- {name} 実行 ---")
        set_status(f"テスト実行中: {name}")
        threading.Thread(target=self._test_worker, args=(name, func), daemon=True).start()

    def _test_worker(self, name, func):
        try:
            result = func()
            root.after(0, lambda: self.show_test_result(name, result))
        except Exception as e:
            root.after(0, lambda: self.show_test_result(name, {"ok": False, "error": str(e)}))

    def show_test_result(self, name, result):
        import json as _json
        self.append_log(_json.dumps(result, indent=2, ensure_ascii=False))
        set_status("待機中")

    def test_all_idn(self):
        return pbl_common.test_all_idn()

class AccordionSection:
    """開閉できるセクションを作る簡易アコーディオン。"""
    def __init__(self, parent, title: str, opened: bool = True, on_open=None):
        self.title = title
        self.opened = opened
        self.on_open = on_open

        self.outer = ttk.Frame(parent, style="TFrame")
        self.outer.pack(fill="x", pady=(0, 8))

        self.button = ttk.Button(
            self.outer,
            text=self._button_text(),
            command=self.toggle,
            style="TButton",
        )
        self.button.pack(fill="x")

        self.body = ttk.Frame(self.outer, padding=6, relief="groove", style="TFrame")
        if self.opened:
            self.body.pack(fill="both", expand=True, pady=(2, 0))
            if self.on_open is not None:
                self.body.after_idle(self.on_open)

    def _button_text(self):
        mark = "▼" if self.opened else "▶"
        return f"{mark} {self.title}"

    def toggle(self):
        self.opened = not self.opened
        self.button.configure(text=self._button_text())
        if self.opened:
            self.body.pack(fill="both", expand=True, pady=(2, 0))
            if self.on_open is not None:
                self.body.after_idle(self.on_open)
        else:
            self.body.pack_forget()


class ResultTab:
    def __init__(self, notebook: ttk.Notebook, config: ScriptConfig):
        self.config = config
        self.image_refs = []
        self.csv_loaded_path = None
        self.csv_loaded_mtime = None
        self.csv_section = None


        # 各タブ全体をスクロール可能にする
        self.frame = ttk.Frame(notebook, style="TFrame")
        notebook.add(self.frame, text=config.label.split()[0])

        self.canvas = tk.Canvas(self.frame, bg=COLOR_BG, highlightthickness=0)
        self.v_scroll = ttk.Scrollbar(self.frame, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.v_scroll.set)

        self.v_scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.content = ttk.Frame(self.canvas, padding=8, style="TFrame")
        self.canvas_window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")

        self.content.bind("<Configure>", self._update_scroll_region)
        self.canvas.bind("<Configure>", self._resize_canvas_window)
        # bind_allは各タブ分を共存させるため add="+" を使う。
        # これを付けないと最後に作られたタブだけがホイールを受け取り、
        # 他タブではスクロールバーをドラッグしないと動かないことがある。
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        self.canvas.bind_all("<Button-4>", self._on_mousewheel, add="+")
        self.canvas.bind_all("<Button-5>", self._on_mousewheel, add="+")

        title_frame = ttk.Frame(self.content, style="TFrame")
        title_frame.pack(fill="x", pady=(0, 6))
        self.title_frame = title_frame

        ttk.Label(
            title_frame,
            text=config.label,
            font=("Yu Gothic", 12, "bold"),
        ).pack(side="left")

        self.run_button = ttk.Button(
            title_frame,
            text="このコードを実行",
            command=self.on_run_button,
            style="Run.TButton",
        )
        self.run_button.pack(side="right", padx=(6, 0))

        self.kirchhoff_buttons = {}
        self.run_button_default_text = {str(self.run_button): "このコードを実行"}

        if config.label.startswith("4-6 "):
            self.run_button.configure(text="6 ベクトル図作成")
            self.run_button_default_text[str(self.run_button)] = "6 ベクトル図作成"

            coil_button = ttk.Button(
                title_frame,
                text="5 コイル測定",
                command=lambda: self.run_kirchhoff_script("coil"),
                style="Run.TButton",
            )
            coil_button.pack(side="right", padx=(6, 0))
            self.kirchhoff_buttons["coil"] = coil_button
            self.run_button_default_text[str(coil_button)] = "5 コイル測定"

            capacitor_button = ttk.Button(
                title_frame,
                text="4 キャパシタ測定",
                command=lambda: self.run_kirchhoff_script("capacitor"),
                style="Run.TButton",
            )
            capacitor_button.pack(side="right", padx=(6, 0))
            self.kirchhoff_buttons["capacitor"] = capacitor_button
            self.run_button_default_text[str(capacitor_button)] = "4 キャパシタ測定"

        ttk.Button(
            title_frame,
            text="↻ このタブを更新",
            command=self.refresh,
            style="Update.TButton",
        ).pack(side="right")

        ttk.Button(
            title_frame,
            text="画像フォルダを開く",
            command=lambda: open_image_folders_for_config(config),
            style="TButton",
        ).pack(side="right", padx=(6, 0))

        self.parameter_frame = ttk.Frame(self.content, padding=6, relief="groove", style="TFrame")
        self.parameter_entries = {}
        self.build_parameter_inputs()

        self.progress_frame = ttk.Frame(self.content, padding=6, relief="groove", style="TFrame")
        ttk.Label(self.progress_frame, text="測定状態", font=("Yu Gothic", 10, "bold")).grid(row=0, column=0, sticky="w", columnspan=4)
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(self.progress_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(4, 4))
        self.progress_text_var = tk.StringVar(value="待機中")
        self.freq_text_var = tk.StringVar(value="現在周波数: -")
        self.stage_text_var = tk.StringVar(value="段階: -")
        self.remaining_text_var = tk.StringVar(value="残り予想時間: -")
        ttk.Label(self.progress_frame, textvariable=self.progress_text_var).grid(row=2, column=0, sticky="w")
        ttk.Label(self.progress_frame, textvariable=self.freq_text_var).grid(row=2, column=1, sticky="w", padx=(12, 0))
        ttk.Label(self.progress_frame, textvariable=self.stage_text_var).grid(row=2, column=2, sticky="w", padx=(12, 0))
        ttk.Label(self.progress_frame, textvariable=self.remaining_text_var).grid(row=2, column=3, sticky="w", padx=(12, 0))
        for _col in range(4):
            self.progress_frame.columnconfigure(_col, weight=1)

        self.live_plot_enabled = config.label.startswith(("1 ", "2 ", "7 "))
        self.live_fig = None
        self.live_ax = None
        self.live_canvas = None
        self.live_widget = None
        self.result_graph_label = None
        self.graph_status_label = None

        # 画像エリアは作らず、全タブで「グラフ」欄に統一する。
        # 1,2,7だけ実行中はリアルタイムグラフ、終了後は同じ欄に最終画像を表示する。
        # 3〜6は最終画像だけを同じ欄に表示する。
        self.graph_section = AccordionSection(self.content, "グラフ", opened=True)
        self.graph_status_label = ttk.Label(
            self.graph_section.body,
            text="測定結果グラフ: 最新の保存画像を表示しています。",
            foreground=COLOR_MUTED,
        )
        self.graph_status_label.pack(anchor="w", pady=(0, 4))

        if self.live_plot_enabled and MATPLOTLIB_AVAILABLE:
            self.live_fig = Figure(figsize=(8, 5), dpi=100)
            self.live_ax = self.live_fig.add_subplot(111)
            self.live_canvas = FigureCanvasTkAgg(self.live_fig, master=self.graph_section.body)
            self.live_widget = self.live_canvas.get_tk_widget()
        elif self.live_plot_enabled and not MATPLOTLIB_AVAILABLE:
            self.graph_status_label.config(
                text="matplotlibを読み込めないため、実行中のリアルタイムグラフは表示できません。終了後の保存画像は表示します。",
                foreground=COLOR_ERROR,
            )

        self.result_graph_frame = ttk.Frame(self.graph_section.body, style="TFrame")
        self.result_graph_labels = []
        self.show_result_graph()

        condition_section = AccordionSection(self.content, "測定条件・理論値・配線", opened=False)
        self.progress_pack_before = self.graph_section.outer

        condition_text_frame = ttk.Frame(condition_section.body, style="TFrame")
        condition_text_frame.pack(fill="both", expand=True)

        self.condition_text = tk.Text(
            condition_text_frame,
            height=8,
            wrap="word",
            font=("Yu Gothic", 9),
            bg=COLOR_TEXTBOX,
            fg=COLOR_TEXT,
            insertbackground=COLOR_TEXT,
            relief="flat",
        )
        condition_scroll = ttk.Scrollbar(
            condition_text_frame,
            orient="vertical",
            command=self.condition_text.yview,
        )
        self.condition_text.configure(yscrollcommand=condition_scroll.set)
        self.condition_text.grid(row=0, column=0, sticky="nsew")
        condition_scroll.grid(row=0, column=1, sticky="ns")
        condition_text_frame.rowconfigure(0, weight=1)
        condition_text_frame.columnconfigure(0, weight=1)

        self.condition_text.insert("1.0", config.conditions)
        self.condition_text.configure(state="disabled")

        representative_section = AccordionSection(self.content, "代表値", opened=False)

        self.representative_label = ttk.Label(
            representative_section.body,
            text="代表値: 未読み込み",
            foreground=COLOR_MUTED,
        )
        self.representative_label.pack(anchor="w", pady=(0, 4))

        self.representative_text = tk.Text(
            representative_section.body,
            height=6,
            wrap="word",
            font=("Yu Gothic", 10),
            bg=COLOR_TEXTBOX,
            fg=COLOR_TEXT,
            insertbackground=COLOR_TEXT,
            relief="flat",
        )
        self.representative_text.pack(fill="x", expand=False)
        self.representative_text.insert("1.0", "測定後に代表値が表示されます。")
        self.representative_text.configure(state="disabled")

        self.csv_section = AccordionSection(self.content, "CSV表示", opened=False, on_open=self.refresh_table)

        self.csv_label = ttk.Label(
            self.csv_section.body,
            text="CSV: 未読み込み（欄を開くと読み込みます）",
            foreground=COLOR_MUTED,
        )
        self.csv_label.pack(anchor="w", pady=(0, 4))

        table_frame = ttk.Frame(self.csv_section.body, style="TFrame")
        table_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(table_frame, show="headings", height=6)
        y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        x_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        # 以前の下側「画像」エリアは廃止し、上の「グラフ」欄へ統一した。
        self.image_widgets = []

    def _update_scroll_region(self, event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _resize_canvas_window(self, event=None):
        self.canvas.itemconfigure(self.canvas_window, width=event.width)

    def _is_descendant_widget(self, widget, parent):
        """widgetがparent自身またはparentの子孫ならTrueを返す。"""
        while widget is not None:
            if widget == parent:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _on_mousewheel(self, event):
        # 現在表示中のタブだけをマウスホイール/タッチパッドでスクロールする。
        # Windows/macOSのMouseWheelとLinux系のButton-4/5の両方に対応。
        if notebook.select() != str(self.frame):
            return

        # 画像ビューアなどの別ウィンドウで発生したホイールイベントは、
        # アプリ本体のスクロールに使わない。
        event_widget = getattr(event, "widget", None)
        if event_widget is not None and event_widget.winfo_toplevel() is not root:
            return

        # CSV表や測定条件テキストは、それぞれのウィジェット自身でスクロールさせる。
        # ここでタブ全体のCanvasスクロールを止めることで、内側だけが動くようにする。
        if event_widget is not None:
            if hasattr(self, "tree") and self._is_descendant_widget(event_widget, self.tree):
                return
            if hasattr(self, "condition_text") and self._is_descendant_widget(event_widget, self.condition_text):
                return

        if getattr(event, "num", None) == 4:
            steps = -1
        elif getattr(event, "num", None) == 5:
            steps = 1
        else:
            delta = getattr(event, "delta", 0)
            if delta == 0:
                return
            # タッチパッドではdeltaの絶対値が120未満になることがあるので、
            # 0に丸めず最低1ステップ動かす。
            steps = -1 if delta > 0 else 1

        self.canvas.yview_scroll(steps, "units")
        return "break"

    def on_run_button(self):
        if RUNNING_TAB is self:
            stop_running_script()
        else:
            extra_args = self.get_parameter_args()
            if extra_args is None:
                return
            run_script(self.config, self, extra_args=extra_args)

    def build_parameter_inputs(self):
        """各測定タブの周波数・走査条件入力欄を作る。"""
        label = self.config.label
        rows = []
        if label.startswith("1 "):
            rows = [("frequencies", "周波数リスト[Hz]", "200,500,1000,2000,5000"), ("vpp", "VPP[V]", "1.0"), ("n_repeat", "繰り返し回数", "10")]
        elif label.startswith("2 "):
            rows = [("frequencies", "周波数リスト[Hz]", "200,500,1000,2000,5000"), ("vpp", "VPP[V]", "1.0"), ("n_repeat", "繰り返し回数", "10")]
        elif label.startswith("3 "):
            rows = [("freq", "測定周波数[Hz]", "5000"), ("vpp", "VPP[V]", "3.0"), ("n_repeat", "繰り返し回数", "10")]
        elif label.startswith("4-6 "):
            rows = [("kirchhoff_freq", "共通周波数[Hz]", "500"), ("vpp", "VPP[V]", "3.0"), ("n_repeat", "繰り返し回数", "10")]
        elif label.startswith("7 "):
            rows = [
                ("coarse_start", "coarse開始[Hz]", "100"),
                ("coarse_stop", "coarse終了[Hz]", "10000"),
                ("coarse_points", "coarse点数", "30"),
                ("fine_points", "fine点数", "100"),
                ("fine_width_ratio", "fine幅比率", "0.30"),
                ("vpp", "VPP[V]", "1.0"),
                ("n_repeat", "繰り返し回数", "5"),
            ]

        if not rows:
            return

        ttk.Label(
            self.parameter_frame,
            text="測定条件を入力",
            font=("Yu Gothic", 10, "bold"),
        ).grid(row=0, column=0, sticky="w", columnspan=6, pady=(0, 4))

        for index, (key, label_text, default) in enumerate(rows, start=1):
            row = 1 + (index - 1) // 3
            col = ((index - 1) % 3) * 2
            ttk.Label(self.parameter_frame, text=label_text).grid(row=row, column=col, sticky="e", padx=(0, 4), pady=2)
            var = tk.StringVar(value=default)
            width = 28 if key == "frequencies" else 10
            entry = ttk.Entry(self.parameter_frame, textvariable=var, width=width)
            entry.grid(row=row, column=col + 1, sticky="w", padx=(0, 12), pady=2)
            self.parameter_entries[key] = var

        for col in range(6):
            self.parameter_frame.columnconfigure(col, weight=1 if col % 2 == 1 else 0)
        self.parameter_frame.pack(fill="x", pady=(0, 8))

    def get_common_measurement_args(self):
        """VPPと繰り返し回数をコマンドライン引数へ変換する。"""
        vpp = float(self.parameter_entries["vpp"].get())
        n_repeat = int(float(self.parameter_entries["n_repeat"].get()))
        if vpp <= 0 or n_repeat < 1:
            raise ValueError
        return ["--vpp", str(vpp), "--n-repeat", str(n_repeat)]

    def get_parameter_args(self):
        """入力欄の値を測定コード用コマンドライン引数に変換する。"""
        label = self.config.label
        try:
            if label.startswith("1 ") or label.startswith("2 "):
                text = self.parameter_entries["frequencies"].get().strip()
                values = []
                for item in text.replace("，", ",").split(","):
                    item = item.strip()
                    if not item:
                        continue
                    value = float(item)
                    if value <= 0:
                        raise ValueError
                    values.append(value)
                if not values:
                    raise ValueError
                return ["--frequencies", ",".join(str(v) for v in values)] + self.get_common_measurement_args()

            if label.startswith("3 "):
                freq = float(self.parameter_entries["freq"].get())
                if freq <= 0:
                    raise ValueError
                return ["--freq", str(freq)] + self.get_common_measurement_args()

            if label.startswith("4-6 "):
                freq = float(self.parameter_entries["kirchhoff_freq"].get())
                if freq <= 0:
                    raise ValueError
                # 6 ベクトル図作成は保存済みの4・5の結果を読むため、周波数引数は渡さない。
                return []

            if label.startswith("7 "):
                coarse_start = float(self.parameter_entries["coarse_start"].get())
                coarse_stop = float(self.parameter_entries["coarse_stop"].get())
                coarse_points = int(float(self.parameter_entries["coarse_points"].get()))
                fine_points = int(float(self.parameter_entries["fine_points"].get()))
                fine_width_ratio = float(self.parameter_entries["fine_width_ratio"].get())
                if coarse_start <= 0 or coarse_stop <= coarse_start or coarse_points < 2 or fine_points < 1 or fine_width_ratio <= 0:
                    raise ValueError
                return [
                    "--coarse-start", str(coarse_start),
                    "--coarse-stop", str(coarse_stop),
                    "--coarse-points", str(coarse_points),
                    "--fine-points", str(fine_points),
                    "--fine-width-ratio", str(fine_width_ratio),
                ] + self.get_common_measurement_args()
        except Exception:
            if label.startswith("1 ") or label.startswith("2 "):
                messagebox.showerror("入力エラー", "周波数リストは 200,500,1000 のように、正の数をカンマ区切りで入力してください。")
            elif label.startswith("3 "):
                messagebox.showerror("入力エラー", "測定周波数[Hz]には正の数を入力してください。")
            elif label.startswith("4-6 "):
                messagebox.showerror("入力エラー", "共通周波数[Hz]には正の数を入力してください。")
            elif label.startswith("7 "):
                messagebox.showerror("入力エラー", "コード7の走査条件を確認してください。開始>0、終了>開始、coarse点数>=2、fine点数>=1、fine幅比率>0 が必要です。")
            else:
                messagebox.showerror("入力エラー", "入力値を確認してください。VPPは正の数、繰り返し回数は1以上の整数にしてください。")
            return None

        return []

    def get_kirchhoff_freq_args(self):
        """キルヒホッフ測定タブの共通周波数をコマンドライン引数へ変換する。"""
        try:
            freq = float(self.parameter_entries["kirchhoff_freq"].get())
        except Exception:
            messagebox.showerror("入力エラー", "共通周波数[Hz]には数値を入力してください。")
            return None
        if freq <= 0:
            messagebox.showerror("入力エラー", "共通周波数[Hz]は正の値にしてください。")
            return None
        try:
            common_args = self.get_common_measurement_args()
        except Exception:
            messagebox.showerror("入力エラー", "VPPは正の数、繰り返し回数は1以上の整数にしてください。")
            return None
        return ["--freq", str(freq)] + common_args

    def run_kirchhoff_script(self, kind: str):
        """4または5の測定コードを、共通周波数つきで実行する。"""
        active_button = self.kirchhoff_buttons.get(kind)
        if RUNNING_TAB is self and RUNNING_BUTTON is active_button:
            stop_running_script()
            return

        args = self.get_kirchhoff_freq_args()
        if args is None:
            return
        if kind == "capacitor":
            config = ScriptConfig(
                "4 キルヒッフの法則(キャパシタ)",
                "4kirchhoffs_laws_capacitor.py",
                "kirchhoff_capacitor_summary_*.csv",
                "kirchhoff_capacitor_*.jpg",
                "kirchhoff_capacitor_representative.json",
                "",
            )
        else:
            config = ScriptConfig(
                "5 キルヒッフの法則(コイル)",
                "5kirchhoffs_laws_coil.py",
                "kirchhoff_coil_summary_*.csv",
                "kirchhoff_coil_*.jpg",
                "kirchhoff_coil_representative.json",
                "",
            )
        run_script(config, self, extra_args=args, active_button=active_button)

    def get_run_buttons(self):
        """このタブの測定実行ボタンをすべて返す。"""
        return [self.run_button] + list(getattr(self, "kirchhoff_buttons", {}).values())

    def set_running(self, running: bool, active_button=None):
        if active_button is None:
            active_button = self.run_button

        if running:
            for button in self.get_run_buttons():
                if button is active_button:
                    button.configure(text="停止", style="Stop.TButton", state="normal")
                else:
                    button.configure(state="disabled")
            self.show_progress_frame()
            self.show_live_graph()
            self.progress_text_var.set("実行中")
        else:
            for button in self.get_run_buttons():
                button.configure(
                    text=self.run_button_default_text.get(str(button), "このコードを実行"),
                    style="Run.TButton",
                    state="normal",
                )
            self.hide_progress_frame()
            self.show_result_graph()

    def show_progress_frame(self):
        if not self.progress_frame.winfo_ismapped():
            self.progress_frame.pack(fill="x", pady=(0, 8), before=self.progress_pack_before)

    def hide_progress_frame(self):
        if self.progress_frame.winfo_ismapped():
            self.progress_frame.pack_forget()

    def set_disabled_while_other_running(self, disabled: bool):
        if RUNNING_TAB is self:
            return
        for button in self.get_run_buttons():
            button.configure(state="disabled" if disabled else "normal")

    def reset_progress(self):
        self.progress_var.set(0)
        self.progress_text_var.set("待機中")
        self.freq_text_var.set("現在周波数: -")
        self.stage_text_var.set("段階: -")
        self.remaining_text_var.set("残り予想時間: -")

    def show_live_graph(self):
        if not self.live_plot_enabled:
            return
        if self.graph_status_label is not None:
            self.graph_status_label.config(text="実行中: CSVに追記された測定点からリアルタイムグラフのみを表示しています。")

        # 実行中は、前回までに保存された測定結果画像を隠す。
        # これをしないとコード7のリアルタイムグラフの下に、過去の最新画像も残って見える。
        if hasattr(self, "result_graph_frame") and self.result_graph_frame.winfo_ismapped():
            self.result_graph_frame.pack_forget()
        self.result_graph_labels = []

        if self.live_widget is not None and not self.live_widget.winfo_ismapped():
            self.live_widget.pack(fill="both", expand=True)
        self.refresh_live_plot()

    def show_result_graph(self):
        if self.live_widget is not None:
            self.live_widget.pack_forget()
        if self.graph_status_label is not None:
            self.graph_status_label.config(text="測定結果グラフ: 最新の保存画像を表示しています。")

        if not hasattr(self, "result_graph_frame"):
            return

        for widget in self.result_graph_frame.winfo_children():
            widget.destroy()
        self.result_graph_labels = []

        image_paths = self.find_result_images()
        if not image_paths:
            label = tk.Label(
                self.result_graph_frame,
                image="",
                text="測定結果グラフはまだありません。",
                bg=COLOR_BG,
                fg=COLOR_TEXT,
            )
            label.pack(fill="x", expand=False)
            self.result_graph_labels.append(label)
            self.result_graph_frame.pack(fill="x", expand=False)
            return

        # コード7は「coarse + fine 全体」と「fine only」の2枚を表示する。
        # それ以外のコードは各パターンの最新画像を表示する。
        for index, image_path in enumerate(image_paths, start=1):
            if len(image_paths) > 1:
                caption = ttk.Label(
                    self.result_graph_frame,
                    text=f"{index}. {image_path.name}",
                    foreground=COLOR_TEXT,
                )
                caption.pack(anchor="w", pady=(8 if index > 1 else 0, 4))

            image_label = tk.Label(self.result_graph_frame, bg=COLOR_BG, fg=COLOR_TEXT)
            image_label.pack(fill="x", expand=False)
            self.result_graph_labels.append(image_label)
            try:
                photo = get_cached_thumbnail(image_path)
                self.image_refs.append(photo)
                image_label.config(image=photo, text="", cursor="hand2")
                image_label.bind("<Button-1>", lambda event, p=image_path: open_interactive_image_viewer(p))
            except Exception as e:
                image_label.config(
                    image="",
                    text=f"測定結果グラフを表示できませんでした。\n{image_path.name}\n{e}",
                    cursor="",
                    justify="center",
                )

        self.result_graph_frame.pack(fill="x", expand=False)
        self._update_scroll_region()

    def find_result_images(self):
        if self.config.label.startswith("4-6 "):
            return self.find_kirchhoff_result_images()

        images = []
        seen = set()
        for pattern in self.config.image_patterns:
            subdir = image_subdir_for_pattern(pattern)
            folder = IMAGE_DIR / subdir if subdir else IMAGE_DIR
            image = find_latest(folder, pattern)
            if image is None and folder != IMAGE_DIR:
                image = find_latest(IMAGE_DIR, pattern)
            if image is not None and str(image) not in seen:
                images.append(image)
                seen.add(str(image))
        return images

    def find_kirchhoff_result_images(self):
        """最新のコード6画像からfreqタグを取り出し、同じ周波数の4・5・6画像をそろえて返す。"""
        vector_folder = IMAGE_DIR / "vector_diagram_theory_and_exp"
        latest_vector = find_latest(vector_folder, "vector_diagram_theory_and_exp_freq_*.png")
        if latest_vector is None:
            latest_vector = find_latest(IMAGE_DIR, "vector_diagram_theory_and_exp_freq_*.png")
        if latest_vector is None:
            return []

        match = re.search(r"(freq_[^_]+)_\d{8}_\d{6}\.png$", latest_vector.name)
        if not match:
            return [latest_vector]
        freq_tag = match.group(1)

        targets = [
            ("kirchhoff_capacitor", f"kirchhoff_capacitor_{freq_tag}_*.jpg"),
            ("kirchhoff_coil", f"kirchhoff_coil_{freq_tag}_*.jpg"),
            ("vector_diagram_theory_and_exp", latest_vector.name),
        ]

        images = []
        seen = set()
        for subdir, pattern in targets:
            folder = IMAGE_DIR / subdir
            image = latest_vector if pattern == latest_vector.name else find_latest(folder, pattern)
            if image is None and folder != IMAGE_DIR:
                image = find_latest(IMAGE_DIR, pattern)
            if image is not None and str(image) not in seen:
                images.append(image)
                seen.add(str(image))
        return images

    def find_latest_result_image(self):
        images = self.find_result_images()
        if not images:
            return None
        return max(images, key=lambda p: p.stat().st_mtime)

    def apply_progress(self, payload):
        current = payload.get("current")
        total = payload.get("total")
        frequency = payload.get("frequency")
        stage = payload.get("stage") or payload.get("message") or "測定中"
        message = payload.get("message") or "測定中"
        if isinstance(current, (int, float)) and isinstance(total, (int, float)) and total > 0:
            percent = max(0, min(100, 100 * float(current) / float(total)))
            self.progress_var.set(percent)
            # 子プロセス側の日本語messageが環境によって文字化けすることがあるため、
            # 左端の進捗欄はASCII数字だけで表示する。
            self.progress_text_var.set(f"{int(current)}/{int(total)}  ({percent:.1f}%)")
            if RUNNING_START_TIME and current > 0:
                elapsed = time.time() - RUNNING_START_TIME
                remain = max(0, elapsed * (float(total) - float(current)) / float(current))
                self.remaining_text_var.set(f"残り予想時間: {format_seconds(remain)}")
        else:
            self.progress_text_var.set(str(message))
        if frequency is not None:
            try:
                self.freq_text_var.set(f"現在周波数: {float(frequency):.5g} Hz")
            except Exception:
                self.freq_text_var.set(f"現在周波数: {frequency}")
        if stage:
            self.stage_text_var.set(f"段階: {stage}")
        if self.live_plot_enabled:
            self.refresh_live_plot()

    def refresh_live_plot(self):
        if not (self.live_plot_enabled and self.live_ax is not None and self.live_canvas is not None):
            return
        latest_csv = find_latest(DATA_DIR, self.config.csv_pattern)
        if latest_csv is None:
            return
        try:
            with open(latest_csv, "r", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
        except Exception:
            return
        if not rows:
            return
        self.live_ax.clear()
        try:
            if self.config.label.startswith("1 "):
                good = [r for r in rows if r.get("Frequency [Hz]") and r.get("XL_mean [Ohm]")]
                x = [float(r["Frequency [Hz]"]) for r in good]
                y = [float(r["XL_mean [Ohm]"]) for r in good]
                self.live_ax.plot(x, y, marker="o")
                self.live_ax.set_xlabel("Frequency [Hz]")
                self.live_ax.set_ylabel("XL_mean [Ohm]")
                self.live_ax.set_title("Coil reactance")
            elif self.config.label.startswith("2 "):
                good = [r for r in rows if r.get("1/f [1/Hz]") and r.get("XC_mean [Ohm]")]
                x = [float(r["1/f [1/Hz]"]) for r in good]
                y = [float(r["XC_mean [Ohm]"]) for r in good]
                self.live_ax.plot(x, y, marker="o")
                self.live_ax.set_xlabel("1/f [1/Hz]")
                self.live_ax.set_ylabel("XC_mean [Ohm]")
                self.live_ax.set_title("Capacitor reactance")
            elif self.config.label.startswith("7 "):
                for stage, marker in [("coarse", "o"), ("fine", "s")]:
                    sub = [r for r in rows if r.get("stage") == stage and r.get("Frequency [Hz]") and r.get("Z_mean [Ohm]")]
                    if sub:
                        x = [float(r["Frequency [Hz]"]) for r in sub]
                        y = [float(r["Z_mean [Ohm]"]) for r in sub]
                        self.live_ax.plot(x, y, marker=marker, linestyle="-", label=stage)
                # 保存される測定結果グラフと見た目をそろえる。
                self.live_ax.set_xscale("log")
                self.live_ax.set_yscale("log")
                self.live_ax.set_xlabel("Frequency [Hz]")
                self.live_ax.set_ylabel("Z_mean [Ohm]")
                self.live_ax.set_title("Resonance impedance")
                self.live_ax.legend()
            self.live_ax.grid(True)
            self.live_fig.tight_layout()
            self.live_canvas.draw_idle()
        except Exception:
            return

    def refresh(self):
        self.refresh_representative()
        self.refresh_csv_status(load_if_open=True)
        if RUNNING_TAB is not self:
            self.show_result_graph()

    def refresh_representative(self):
        path = DATA_DIR / self.config.representative_json

        self.representative_text.configure(state="normal")
        self.representative_text.delete("1.0", "end")

        if not path.exists():
            self.representative_label.config(
                text=f"代表値: {path.name} がまだありません",
                foreground=COLOR_MUTED,
            )
            self.representative_text.insert("1.0", "測定後に代表値が表示されます。")
            self.representative_text.configure(state="disabled")
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            self.representative_label.config(
                text=f"代表値読み込みエラー: {path.name} / {e}",
                foreground=COLOR_ERROR,
            )
            self.representative_text.insert("1.0", "代表値JSONを読み込めませんでした。")
            self.representative_text.configure(state="disabled")
            return

        lines = data.get("lines", [])
        if isinstance(lines, list):
            text = "\n".join(str(line) for line in lines)
        else:
            text = str(lines)

        if not text.strip():
            text = "代表値JSONに表示用テキストがありません。"

        self.representative_text.insert("1.0", text)
        self.representative_text.configure(height=max(3, min(12, text.count("\n") + 1)))
        self.representative_text.configure(state="disabled")

        timestamp_text = data.get("timestamp", "")
        suffix = f" / 測定時刻: {timestamp_text}" if timestamp_text else ""
        self.representative_label.config(
            text=f"代表値: {path.name}{suffix} / 更新時刻: {format_mtime(path)}",
            foreground=COLOR_TEXT,
        )

    def refresh_csv_status(self, load_if_open: bool = False):
        latest_csv = find_latest(DATA_DIR, self.config.csv_pattern)

        if latest_csv is None:
            clear_tree(self.tree)
            self.csv_loaded_path = None
            self.csv_loaded_mtime = None
            self.csv_label.config(
                text=f"CSV: {self.config.csv_pattern} がまだありません",
                foreground=COLOR_MUTED,
            )
            return

        row_count = count_csv_data_rows(latest_csv)
        self.csv_label.config(
            text=(
                f"CSV: {latest_csv.name} / 更新時刻: {format_mtime(latest_csv)} / "
                f"{row_count}行 / CSV表示欄を開くと表を読み込みます"
            ),
            foreground=COLOR_TEXT,
        )

        if load_if_open and self.csv_section is not None and self.csv_section.opened:
            self.refresh_table()

    def refresh_table(self):
        latest_csv = find_latest(DATA_DIR, self.config.csv_pattern)

        if latest_csv is None:
            clear_tree(self.tree)
            self.csv_loaded_path = None
            self.csv_loaded_mtime = None
            self.csv_label.config(
                text=f"CSV: {self.config.csv_pattern} がまだありません",
                foreground=COLOR_MUTED,
            )
            return

        latest_mtime = latest_csv.stat().st_mtime
        if self.csv_loaded_path == latest_csv and self.csv_loaded_mtime == latest_mtime:
            return

        clear_tree(self.tree)

        try:
            with open(latest_csv, "r", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.reader(f))
        except Exception as e:
            self.csv_label.config(
                text=f"CSV読み込みエラー: {latest_csv.name} / {e}",
                foreground=COLOR_ERROR,
            )
            return

        if not rows:
            self.csv_label.config(
                text=f"CSV: {latest_csv.name} は空です",
                foreground=COLOR_ERROR,
            )
            return

        header = rows[0]
        data_rows = rows[1:]

        self.tree["columns"] = header

        for col_index, col in enumerate(header):
            sample_values = [str(row[col_index]) for row in data_rows[:30] if col_index < len(row)]
            max_len = max([len(str(col))] + [len(v) for v in sample_values])
            self.tree.heading(col, text=col)
            self.tree.column(
                col,
                width=max(95, min(220, max_len * 9)),
                anchor="center",
                stretch=True,
            )

        self.tree.configure(height=max(3, min(CSV_TREE_MAX_VISIBLE_ROWS, len(data_rows))))

        for row in data_rows:
            row = row + [""] * (len(header) - len(row))
            self.tree.insert("", "end", values=row[:len(header)])

        self.csv_loaded_path = latest_csv
        self.csv_loaded_mtime = latest_mtime
        self.csv_label.config(
            text=(
                f"CSV: {latest_csv.name} / "
                f"更新時刻: {format_mtime(latest_csv)} / "
                f"{len(rows) - 1}行 Treeview表示"
            ),
            foreground=COLOR_TEXT,
        )

    def refresh_image(self):
        self.image_refs = []

        for widget in self.image_widgets:
            widget.destroy()
        self.image_widgets = []

        image_paths = []
        searched_folders = []

        for pattern in self.config.image_patterns:
            subdir = image_subdir_for_pattern(pattern)
            image_folder = IMAGE_DIR / subdir if subdir else IMAGE_DIR
            searched_folders.append(image_folder)

            latest_image = find_latest(image_folder, pattern)

            # 旧形式との互換性: サブフォルダに無い場合だけ、従来のresults/images直下も探す。
            if latest_image is None and image_folder != IMAGE_DIR:
                latest_image = find_latest(IMAGE_DIR, pattern)

            if latest_image is not None:
                image_paths.append(latest_image)

        if not image_paths:
            patterns_text = ", ".join(self.config.image_patterns)
            folders_text = " / ".join(str(folder) for folder in searched_folders)
            self.image_label.config(
                text=f"画像: {patterns_text} がまだありません / 探したフォルダ: {folders_text}",
                foreground=COLOR_MUTED,
            )
            label = tk.Label(
                self.image_box,
                text="画像ファイルがまだありません",
                bg=COLOR_BG,
                fg=COLOR_TEXT,
            )
            label.pack(fill="x", expand=False)
            self.image_widgets.append(label)
            return

        image_names = " / ".join(path.name for path in image_paths)
        latest_mtime = max(path.stat().st_mtime for path in image_paths)
        from datetime import datetime
        latest_mtime_text = datetime.fromtimestamp(latest_mtime).strftime("%Y-%m-%d %H:%M:%S")

        self.image_label.config(
            text=f"画像: {image_names} / 最新更新時刻: {latest_mtime_text}",
            foreground=COLOR_TEXT,
        )

        for index, image_path in enumerate(image_paths, start=1):
            caption = ttk.Label(
                self.image_box,
                text=f"{index}. {image_path.name}",
                foreground=COLOR_TEXT,
            )
            caption.pack(anchor="w", pady=(8 if index > 1 else 0, 4))
            self.image_widgets.append(caption)

            image_label = tk.Label(
                self.image_box,
                bg=COLOR_BG,
                fg=COLOR_TEXT,
            )
            image_label.pack(fill="x", expand=False)
            self.image_widgets.append(image_label)

            try:
                photo = get_cached_thumbnail(image_path)
                self.image_refs.append(photo)
                image_label.config(image=photo, text="", bg=COLOR_BG, cursor="hand2")
                image_label.bind("<Button-1>", lambda event, p=image_path: open_interactive_image_viewer(p))
                self._update_scroll_region()

            except Exception as e:
                msg = (
                    "画像をGUI内に表示できませんでした。\n"
                    f"{image_path.name}\n"
                    f"{e}\n\n"
                    "外部アプリで開いて確認してください。"
                )
                image_label.config(
                    image="",
                    text=msg,
                    bg=COLOR_BG,
                    justify="center",
                )



def open_interactive_image_viewer(image_path: Path):
    """画像を拡大縮小・ドラッグ移動できる別ウィンドウで開く。"""
    if not PIL_AVAILABLE:
        open_folder(image_path)
        return
    try:
        base_image = Image.open(image_path).convert("RGB")
    except Exception as e:
        messagebox.showerror("画像表示エラー", f"画像を開けませんでした。\n\n{image_path}\n{e}")
        return

    win = tk.Toplevel(root)
    win.title(f"画像ビューア - {image_path.name}")
    win.geometry("1000x750")
    win.configure(bg=COLOR_BG)

    top = ttk.Frame(win, padding=6, style="TFrame")
    top.pack(fill="x")
    ttk.Label(top, text="ホイール: 拡大縮小 / 左ドラッグ: 移動", foreground=COLOR_MUTED).pack(side="left")
    canvas = tk.Canvas(win, bg="#f8fafc", highlightthickness=0, cursor="fleur")
    canvas.pack(fill="both", expand=True)

    state = {
        "zoom": 1.0,
        "photo": None,
        "image_id": None,
        "drag_x": 0,
        "drag_y": 0,
        "image_w": base_image.width,
        "image_h": base_image.height,
    }

    def render():
        w = max(1, int(base_image.width * state["zoom"]))
        h = max(1, int(base_image.height * state["zoom"]))
        resized = base_image.resize((w, h), Image.LANCZOS)
        state["photo"] = ImageTk.PhotoImage(resized)
        state["image_w"] = w
        state["image_h"] = h
        if state["image_id"] is None:
            state["image_id"] = canvas.create_image(0, 0, image=state["photo"], anchor="nw")
        else:
            canvas.itemconfigure(state["image_id"], image=state["photo"])
        canvas.configure(scrollregion=(0, 0, w, h))

    def set_zoom(value):
        state["zoom"] = max(0.05, min(8.0, float(value)))
        render()

    def fit_to_window():
        cw = max(1, canvas.winfo_width())
        ch = max(1, canvas.winfo_height())
        state["zoom"] = min(cw / base_image.width, ch / base_image.height, 1.0)
        render()

    def on_wheel(event):
        # 画像ビューア内のホイールはここで消費し、
        # 親アプリ側の bind_all に伝わってスクロールしないようにする。
        if getattr(event, "num", None) == 4:
            factor = 1.15
        elif getattr(event, "num", None) == 5:
            factor = 1 / 1.15
        else:
            delta = getattr(event, "delta", 0)
            if delta == 0:
                return "break"
            factor = 1.15 if delta > 0 else 1 / 1.15
        set_zoom(state["zoom"] * factor)
        return "break"

    def on_drag_start(event):
        state["drag_x"] = event.x
        state["drag_y"] = event.y

    def on_drag(event):
        dx = (state["drag_x"] - event.x) * IMAGE_VIEWER_DRAG_SENSITIVITY
        dy = (state["drag_y"] - event.y) * IMAGE_VIEWER_DRAG_SENSITIVITY
        state["drag_x"] = event.x
        state["drag_y"] = event.y

        # xview_scroll(..., "units") は1pxのドラッグでも大きく動きやすいので、
        # スクロール位置を画像サイズに対する割合で直接更新する。
        if state["image_w"] > canvas.winfo_width():
            left = canvas.xview()[0] + dx / state["image_w"]
            canvas.xview_moveto(max(0.0, min(1.0, left)))
        if state["image_h"] > canvas.winfo_height():
            top = canvas.yview()[0] + dy / state["image_h"]
            canvas.yview_moveto(max(0.0, min(1.0, top)))
        return "break"

    ttk.Button(top, text="全体表示", command=fit_to_window).pack(side="right", padx=3)
    canvas.bind("<MouseWheel>", on_wheel)
    canvas.bind("<Button-4>", on_wheel)
    canvas.bind("<Button-5>", on_wheel)
    canvas.bind("<ButtonPress-1>", on_drag_start)
    canvas.bind("<B1-Motion>", on_drag)
    win.after(100, fit_to_window)


def get_cached_thumbnail(image_path: Path):
    """GUI表示用に縮小済み画像を作り、同じ画像は再利用する。"""
    mtime = image_path.stat().st_mtime
    key = (str(image_path), mtime, IMAGE_MAX_WIDTH, IMAGE_MAX_HEIGHT, PIL_AVAILABLE)

    # 古いキャッシュを削除してメモリ使用量を増やしすぎないようにする。
    for old_key in list(IMAGE_THUMBNAIL_CACHE):
        if old_key[0] == str(image_path) and old_key != key:
            IMAGE_THUMBNAIL_CACHE.pop(old_key, None)

    if key in IMAGE_THUMBNAIL_CACHE:
        return IMAGE_THUMBNAIL_CACHE[key]

    if PIL_AVAILABLE:
        with Image.open(image_path) as image:
            image = image.copy()
            image.thumbnail((IMAGE_MAX_WIDTH, IMAGE_MAX_HEIGHT), Image.LANCZOS)
            photo = ImageTk.PhotoImage(image)
    else:
        photo = tk.PhotoImage(file=str(image_path))

    IMAGE_THUMBNAIL_CACHE[key] = photo

    # 最大でも最近の20画像程度に抑える。
    while len(IMAGE_THUMBNAIL_CACHE) > 20:
        IMAGE_THUMBNAIL_CACHE.pop(next(iter(IMAGE_THUMBNAIL_CACHE)))

    return photo


def count_csv_data_rows(path: Path) -> int:
    """CSVのデータ行数を軽く数える。ヘッダー行は除外する。"""
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            return max(0, sum(1 for _ in f) - 1)
    except Exception:
        return 0


def ensure_dirs():
    for d in [RESULT_DIR, IMAGE_DIR, DATA_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    for subdir in IMAGE_SUBDIRS.values():
        (IMAGE_DIR / subdir).mkdir(parents=True, exist_ok=True)


def find_latest(folder: Path, pattern: str):
    files = [p for p in folder.glob(pattern) if p.is_file()]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def format_mtime(path: Path):
    from datetime import datetime

    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")


def clear_tree(tree: ttk.Treeview):
    for item in tree.get_children():
        tree.delete(item)
    tree["columns"] = []


def get_python_executable():
    candidates = [
        r"C:\Users\taise\anaconda3\python.exe",
        shutil.which("python"),
        shutil.which("py"),
    ]

    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate

    return None


def run_script(config: ScriptConfig, tab=None, extra_args=None, active_button=None):
    global RUNNING_PROCESS, RUNNING_TAB, RUNNING_BUTTON, RUNNING_START_TIME

    if RUNNING_PROCESS is not None:
        messagebox.showinfo("実行中", "別の測定コードが実行中です。先に停止または完了を待ってください。")
        return

    script_path = BASE_DIR / config.script
    if not script_path.exists():
        messagebox.showerror("エラー", f"{config.script} が見つかりません。\n\n探した場所:\n{script_path}")
        return

    python_exe = get_python_executable()
    if python_exe is None:
        messagebox.showerror("Pythonが見つかりません", "測定コードを実行するためのPythonが見つかりませんでした。\n\nC:\\Users\\taise\\anaconda3\\python.exe が存在するか確認してください。")
        return

    ok = messagebox.askyesno("配線確認", f"{config.label} を実行します。\n\n配線は正しいですか？\n実行中はこのボタンが停止ボタンになります。")
    if not ok:
        return

    clear_stop_request_file()
    if tab is None:
        tab = tab_for_config(config)
    RUNNING_TAB = tab
    RUNNING_BUTTON = active_button if active_button is not None else (tab.run_button if tab is not None else None)
    RUNNING_START_TIME = time.time()
    if tab is not None:
        tab.reset_progress()
        tab.set_running(True, active_button=RUNNING_BUTTON)
        notebook.select(tab.frame)
    set_all_run_buttons_disabled_except(tab)
    set_status(f"実行中: {config.label}")

    cmd = [python_exe, str(script_path)]
    if extra_args:
        cmd.extend(extra_args)
    if Path(python_exe).name.lower() == "py.exe":
        cmd = [python_exe, "-3", str(script_path)]
        if extra_args:
            cmd.extend(extra_args)

    try:
        popen_kwargs = get_subprocess_no_console_kwargs()
        child_env = os.environ.copy()
        # 測定コードの標準出力をUTF-8に固定し、進捗JSONや日本語ログの文字化けを防ぐ。
        child_env["PYTHONIOENCODING"] = "utf-8"
        child_env.setdefault("PYTHONUTF8", "1")
        RUNNING_PROCESS = subprocess.Popen(
            cmd,
            cwd=str(BASE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=child_env,
            **popen_kwargs,
        )
    except Exception as e:
        RUNNING_PROCESS = None
        RUNNING_TAB = None
        RUNNING_BUTTON = None
        if tab is not None:
            tab.set_running(False)
        set_all_run_buttons_disabled_except(None)
        messagebox.showerror("実行エラー", f"{config.label} を開始できませんでした。\n\n{e}")
        set_status("待機中")
        return

    threading.Thread(target=_stdout_reader, args=(RUNNING_PROCESS,), daemon=True).start()
    root.after(100, poll_running_process)


def _stdout_reader(process):
    try:
        for line in process.stdout:
            STDOUT_QUEUE.put(line.rstrip("\n"))
    except Exception as e:
        STDOUT_QUEUE.put(f"[stdout read error] {e}")


def poll_running_process():
    global RUNNING_PROCESS, RUNNING_TAB, RUNNING_BUTTON, RUNNING_START_TIME
    while True:
        try:
            line = STDOUT_QUEUE.get_nowait()
        except queue.Empty:
            break
        handle_process_output_line(line)

    process = RUNNING_PROCESS
    if process is None:
        return
    if process.poll() is None:
        if RUNNING_TAB is not None and RUNNING_TAB.live_plot_enabled:
            RUNNING_TAB.refresh_live_plot()
        root.after(500, poll_running_process)
        return

    return_code = process.returncode
    finished_tab = RUNNING_TAB
    RUNNING_PROCESS = None
    RUNNING_TAB = None
    RUNNING_BUTTON = None
    RUNNING_START_TIME = None
    clear_stop_request_file()

    if finished_tab is not None:
        finished_tab.set_running(False)
        if return_code == 0:
            finished_tab.progress_var.set(100)
            finished_tab.progress_text_var.set("完了")
            finished_tab.remaining_text_var.set("残り予想時間: 0秒")
        elif return_code < 0:
            finished_tab.progress_text_var.set("停止しました")
        else:
            finished_tab.progress_text_var.set(f"エラー終了: return code {return_code}")
        finished_tab.refresh()

    set_all_run_buttons_disabled_except(None)
    refresh_all_results()
    if return_code == 0:
        set_status("完了")
        messagebox.showinfo("完了", "測定が完了しました。")
    elif return_code < 0:
        set_status("停止しました")
        messagebox.showinfo("停止", "測定を停止しました。")
    else:
        set_status("実行エラー")
        messagebox.showerror("実行エラー", f"測定コードがエラー終了しました。return code = {return_code}")


def handle_process_output_line(line):
    if not line:
        return
    try:
        obj = json.loads(line)
        progress = obj.get("pbl_progress")
        if progress and RUNNING_TAB is not None:
            RUNNING_TAB.apply_progress(progress)
            return
    except Exception:
        pass
    set_status(line[-140:])


def stop_running_script():
    global RUNNING_PROCESS
    if RUNNING_PROCESS is None:
        return

    create_stop_request_file()

    if RUNNING_TAB is not None:
        RUNNING_TAB.progress_text_var.set("停止要求中... FG出力OFF")
    set_status("停止要求中: FG出力OFF")

    # launcher側から先にFG出力を止める
    try:
        if PBL_COMMON_AVAILABLE:
            fg, _ = pbl_common.open_resource_for_role("fg")
            try:
                fg.write("OUTP OFF")
            finally:
                fg.close()
    except Exception as e:
        set_status(f"停止要求中: FG出力OFF失敗 {e}")

    # すぐterminateしない。測定コード側の安全終了を少し待つ。
    root.after(3000, force_terminate_if_still_running)


def force_terminate_if_still_running():
    global RUNNING_PROCESS

    if RUNNING_PROCESS is None:
        return

    try:
        if RUNNING_PROCESS.poll() is None:
            set_status("強制終了中")
            RUNNING_PROCESS.terminate()
    except Exception:
        pass


def create_stop_request_file():
    ensure_dirs()
    try:
        with open(DATA_DIR / "stop_requested.flag", "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except Exception:
        pass


def clear_stop_request_file():
    try:
        (DATA_DIR / "stop_requested.flag").unlink(missing_ok=True)
    except Exception:
        pass


def set_all_run_buttons_disabled_except(active_tab):
    for tab in result_tabs:
        if tab is active_tab:
            continue
        tab.set_disabled_while_other_running(active_tab is not None)


def tab_for_config(config):
    for tab in result_tabs:
        if tab.config == config:
            return tab
    return None


def format_seconds(seconds):
    seconds = int(max(0, seconds))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}時間{minutes}分{sec}秒"
    if minutes:
        return f"{minutes}分{sec}秒"
    return f"{sec}秒"


def open_folder(path: Path):
    ensure_dirs()

    try:
        os.startfile(path)
    except AttributeError:
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.run([opener, str(path)], check=False)


def image_folders_for_config(config: ScriptConfig):
    """タブ設定に対応する画像フォルダ一覧を返す。"""
    folders = []
    for pattern in config.image_patterns:
        subdir = image_subdir_for_pattern(pattern)
        folder = IMAGE_DIR / subdir if subdir else IMAGE_DIR
        if folder not in folders:
            folders.append(folder)
    return folders


def open_image_folders_for_config(config: ScriptConfig):
    """タブに対応する画像フォルダを開く。"""
    ensure_dirs()
    for folder in image_folders_for_config(config):
        open_folder(folder)


def open_latest_image_for_current_tab():
    
    current_tab_id = notebook.select()

    for tab in result_tabs:
        if str(tab.frame) == current_tab_id:
            images = []
            for pattern in tab.config.image_patterns:
                subdir = image_subdir_for_pattern(pattern)
                image_folder = IMAGE_DIR / subdir if subdir else IMAGE_DIR
                image = find_latest(image_folder, pattern)

                # 旧形式との互換性
                if image is None and image_folder != IMAGE_DIR:
                    image = find_latest(IMAGE_DIR, pattern)

                if image is not None:
                    images.append(image)

            if not images:
                messagebox.showinfo(
                    "情報",
                    "このタブに対応する画像ファイルがまだありません。",
                )
                return

            open_folder(images[0])
            return


def refresh_all_results():
    ensure_dirs()

    for tab in result_tabs:
        tab.refresh()


def set_status(text: str):
    status_var.set(text)


ensure_dirs()

root = tk.Tk()
root.title("物理学PBL1 測定Launcher")
root.geometry("1050x900")

root.configure(bg=COLOR_BG)

style = ttk.Style()
try:
    style.theme_use("clam")
except tk.TclError:
    pass

tk_font = ("Yu Gothic", 10)

# 全体は白背景・黒文字を基本にする
style.configure("TFrame", background=COLOR_BG)
style.configure("TLabel", background=COLOR_BG, foreground=COLOR_TEXT)
style.configure("TLabelframe", background=COLOR_BG, foreground=COLOR_TEXT)
style.configure("TLabelframe.Label", background=COLOR_BG, foreground=COLOR_TEXT)

# タブはサイズを変えず、色だけ変更する
style.configure("TNotebook", background=COLOR_BG, borderwidth=0)
style.configure("TNotebook.Tab", background=COLOR_TAB_INACTIVE, foreground=COLOR_TEXT)
style.map(
    "TNotebook.Tab",
    background=[("selected", COLOR_BG), ("!selected", COLOR_TAB_INACTIVE)],
    foreground=[("selected", COLOR_TEXT), ("!selected", COLOR_TEXT)],
)

# 表は白基調にする
style.configure(
    "Treeview",
    background=COLOR_BG,
    fieldbackground=COLOR_BG,
    foreground=COLOR_TEXT,
    bordercolor=COLOR_BORDER,
    lightcolor=COLOR_BORDER,
    darkcolor=COLOR_BORDER,
)
style.configure(
    "Treeview.Heading",
    background="#f4f8fb",
    foreground=COLOR_TEXT,
    relief="flat",
)
style.map("Treeview", background=[("selected", "#dff1ff")], foreground=[("selected", COLOR_TEXT)])

# ボタンは薄い色に統一する
style.configure(
    "TButton",
    background=COLOR_BUTTON,
    foreground=COLOR_TEXT,
    bordercolor=COLOR_BORDER,
    lightcolor="#ffffff",
    darkcolor=COLOR_BORDER,
    relief="raised",
)
style.map("TButton", background=[("active", COLOR_BUTTON_ACTIVE)], foreground=[("active", COLOR_TEXT)])

# 更新ボタンは全タブ・このタブで同じ色にする
style.configure(
    "Update.TButton",
    background=COLOR_UPDATE_BUTTON,
    foreground=COLOR_TEXT,
    bordercolor=COLOR_BORDER,
    lightcolor="#ffffff",
    darkcolor=COLOR_BORDER,
    relief="raised",
)
style.map("Update.TButton", background=[("active", COLOR_UPDATE_BUTTON_ACTIVE)], foreground=[("active", COLOR_TEXT)])

# 実行ボタンだけ薄い赤色にする
style.configure(
    "Run.TButton",
    background=COLOR_RUN_BUTTON,
    foreground=COLOR_TEXT,
    bordercolor="#f2caca",
    lightcolor="#ffffff",
    darkcolor="#f2caca",
    relief="raised",
)
style.map("Run.TButton", background=[("active", COLOR_RUN_BUTTON_ACTIVE)], foreground=[("active", COLOR_TEXT)])

style.configure(
    "Stop.TButton",
    background=COLOR_STOP_BUTTON,
    foreground=COLOR_TEXT,
    bordercolor="#f2caca",
    lightcolor="#ffffff",
    darkcolor="#f2caca",
    relief="raised",
)
style.map("Stop.TButton", background=[("active", COLOR_STOP_BUTTON_ACTIVE)], foreground=[("active", COLOR_TEXT)])

header = ttk.Frame(root, padding=10, style="TFrame")
header.pack(fill="x")

left_header = ttk.Frame(header, style="TFrame")
left_header.pack(side="left", fill="x", expand=True)

ttk.Label(
    left_header,
    text="物理学PBL1 測定Launcher",
    font=("Yu Gothic", 16, "bold"),
).pack(anchor="w")

ttk.Label(
    left_header,
    text=NOTE,
    font=tk_font,
    justify="left",
).pack(anchor="w", pady=(6, 0))

button_frame = ttk.Frame(header, style="TFrame")
button_frame.pack(side="right", padx=(12, 0))

ttk.Button(
    button_frame,
    text="↻ 全タブを更新",
    width=22,
    command=refresh_all_results,
    style="Update.TButton",
).pack(pady=2)

ttk.Button(
    button_frame,
    text="現在タブの最新画像を開く",
    width=22,
    command=open_latest_image_for_current_tab,
    style="TButton",
).pack(pady=2)

ttk.Button(
    button_frame,
    text="results フォルダを開く",
    width=22,
    command=lambda: open_folder(RESULT_DIR),
    style="TButton",
).pack(pady=2)

notebook = ttk.Notebook(root)
notebook.pack(fill="both", expand=True, padx=10, pady=(0, 8))

status_var = tk.StringVar(value="待機中")

visa_tab = VisaTab(notebook)
test_tab = TestTab(notebook, visa_tab)
result_tabs = [ResultTab(notebook, config) for config in SCRIPTS]

status_bar = ttk.Label(
    root,
    textvariable=status_var,
    relief="sunken",
    anchor="w",
    padding=4,
    background=COLOR_BG,
    foreground=COLOR_TEXT,
)
status_bar.pack(fill="x", side="bottom")

refresh_all_results()

root.mainloop()