"""
TrueSize PDF - shows PDF pages on screen at their exact real-world (printed) size.

Hold a printed page or a ruler up to the screen and it matches 1:1.

Requirements:  Windows 10/11, Python 3.8+, PyMuPDF  (pip install pymupdf)
Run:           python TrueSizePDF.py [optional path to a .pdf file]
"""

import sys
import os
import json
import math
import ctypes

# --------------------------------------------------------------------------
#  DPI awareness MUST be set before any window is created, otherwise Windows
#  scales the window (125%, 150% ...) and every measurement would be wrong.
# --------------------------------------------------------------------------
IS_WINDOWS = sys.platform == "win32"
if IS_WINDOWS:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)      # per-monitor aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import tempfile

try:
    import fitz  # PyMuPDF
except ImportError:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(
        "TrueSize PDF",
        "PyMuPDF is not installed.\n\nOpen a Command Prompt and run:\n\n"
        "    pip install pymupdf\n\nor just double-click run.bat.")
    sys.exit(1)

APP_NAME = "TrueSize PDF"
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "TrueSizePDF")
CONFIG_FILE = os.path.join(CONFIG_DIR, "settings.json")

DEFAULTS = {
    "mode": "auto",          # "auto" (read from monitor) or "manual"
    "manual_w_mm": None,
    "manual_h_mm": None,
    "cal_x": 1.0,            # fine-calibration multipliers from the ruler test
    "cal_y": 1.0,
    "show_hud": True,
    "show_ruler": False,
    "last_dir": "",
    "print": {},
}

PRINT_PAPERS = ["A4", "A3", "A2", "A1", "A0", "A5", "Letter", "Legal", "Tabloid", "Custom"]
PRINT_DEFAULTS = {
    "paper": "A4", "orient": "portrait", "custom_w": 210.0, "custom_h": 297.0,
    "margin": 5.0, "scale": 1.0, "poster": False, "overlap": 10.0,
    "all_pages": False, "label": True, "fx": 0.0, "fy": 0.0,
}

FULL_RENDER_LIMIT = 45_000_000   # pixels; bigger pages are rendered piece by piece
BG = "#2a2a2e"
ACCENT = "#4fa3ff"

PAPER_SIZES = {
    "A0": (841, 1189), "A1": (594, 841), "A2": (420, 594), "A3": (297, 420),
    "A4": (210, 297), "A5": (148, 210), "A6": (105, 148),
    "B4": (250, 353), "B5": (176, 250),
    "Letter": (215.9, 279.4), "Legal": (215.9, 355.6), "Tabloid": (279.4, 431.8),
}


def paper_name(w_mm, h_mm):
    for name, (a, b) in PAPER_SIZES.items():
        if abs(w_mm - a) < 2 and abs(h_mm - b) < 2:
            return f"{name} portrait"
        if abs(w_mm - b) < 2 and abs(h_mm - a) < 2:
            return f"{name} landscape"
    return "custom size"


# ==========================================================================
#  Windows monitor helpers (pixel size + physical size from the monitor EDID)
# ==========================================================================
if IS_WINDOWS:
    from ctypes import wintypes
    import winreg

    user32 = ctypes.windll.user32

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT), ("rcWork", RECT),
                    ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]

    class DISPLAY_DEVICEW(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32),
                    ("DeviceString", wintypes.WCHAR * 128), ("StateFlags", wintypes.DWORD),
                    ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]

    MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                         ctypes.POINTER(RECT), wintypes.LPARAM)

    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HMONITOR
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFOEXW)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.EnumDisplayDevicesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                           ctypes.POINTER(DISPLAY_DEVICEW), wintypes.DWORD]
    user32.EnumDisplayDevicesW.restype = wintypes.BOOL
    user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, MONITORENUMPROC,
                                           wintypes.LPARAM]
    user32.EnumDisplayMonitors.restype = wintypes.BOOL


def monitor_info(hmon):
    mi = MONITORINFOEXW()
    mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
    user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
    r = mi.rcMonitor
    return {"device": mi.szDevice, "left": r.left, "top": r.top,
            "w": r.right - r.left, "h": r.bottom - r.top}


def monitor_for_window(hwnd):
    return monitor_info(user32.MonitorFromWindow(hwnd, 2))   # MONITOR_DEFAULTTONEAREST


def all_monitors():
    mons = []

    def cb(hmon, hdc, rect, lparam):
        mons.append(monitor_info(hmon))
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(cb), 0)
    mons.sort(key=lambda m: (m["left"], m["top"]))
    return mons


def parse_edid(edid):
    """Returns (width_mm, height_mm, model_name) or None."""
    if not edid or len(edid) < 128 or bytes(edid[0:8]) != b"\x00\xff\xff\xff\xff\xff\xff\x00":
        return None
    name = ""
    for off in (54, 72, 90, 108):
        d = edid[off:off + 18]
        if d[0] == 0 and d[1] == 0 and d[3] == 0xFC:
            name = bytes(d[5:18]).decode("ascii", "ignore").split("\n")[0].strip()
    # 1) Preferred detailed timing descriptor: size in millimetres (most precise)
    d = edid[54:72]
    if d[0] or d[1]:
        w = d[12] | ((d[14] & 0xF0) << 4)
        h = d[13] | ((d[14] & 0x0F) << 8)
        if w > 50 and h > 50:
            return float(w), float(h), name
    # 2) Basic display parameters: size in centimetres
    if edid[21] and edid[22]:
        return edid[21] * 10.0, edid[22] * 10.0, name
    return None


def read_edid(model, instance):
    path = rf"SYSTEM\CurrentControlSet\Enum\DISPLAY\{model}\{instance}\Device Parameters"
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as k:
        return winreg.QueryValueEx(k, "EDID")[0]


def detect_physical_size(device_name):
    """Physical size (mm) of the monitor attached to \\\\.\\DISPLAYn, read from its EDID."""
    if not IS_WINDOWS:
        return None
    ids = []
    for flags in (1, 0):   # 1 = EDD_GET_DEVICE_INTERFACE_NAME
        i = 0
        while True:
            dd = DISPLAY_DEVICEW()
            dd.cb = ctypes.sizeof(DISPLAY_DEVICEW)
            if not user32.EnumDisplayDevicesW(device_name, i, ctypes.byref(dd), flags):
                break
            if dd.StateFlags & 0x1 or i == 0:          # active monitor first
                ids.append(dd.DeviceID)
            i += 1
    for dev_id in ids:
        try:
            if dev_id.startswith("\\\\?\\"):
                # \\?\DISPLAY#GSM5B7F#5&2ab3&0&UID4352#{guid}
                parts = dev_id[4:].split("#")
                res = parse_edid(read_edid(parts[1], parts[2]))
                if res:
                    return res
            elif dev_id.upper().startswith("MONITOR\\"):
                # MONITOR\GSM5B7F\{guid}\0003 -> look through instances of that model
                model = dev_id.split("\\")[1]
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                    rf"SYSTEM\CurrentControlSet\Enum\DISPLAY\{model}") as k:
                    n = 0
                    while True:
                        try:
                            inst = winreg.EnumKey(k, n)
                        except OSError:
                            break
                        n += 1
                        try:
                            res = parse_edid(read_edid(model, inst))
                            if res:
                                return res
                        except OSError:
                            pass
        except (OSError, IndexError):
            continue
    return None


# ==========================================================================
#  Settings
# ==========================================================================
def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            s.update(json.load(f))
    except Exception:
        pass
    return s


def save_settings(s):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
    except Exception:
        pass


# ==========================================================================
#  The application
# ==========================================================================
class TrueSizeApp:
    def __init__(self, root, initial_file=None):
        self.root = root
        self.s = load_settings()
        self.pr = dict(PRINT_DEFAULTS)
        self.pr.update(self.s.get("print") or {})
        self.pr["fx"] = self.pr["fy"] = 0.0

        self.doc = None
        self.doc_path = ""
        self.page_index = 0
        self.full_img = None        # PhotoImage of the whole page (normal case)
        self.clip_img = None        # PhotoImage of the visible part (huge pages)
        self.off_x = 0.0            # content pixel shown at the left edge of the view area
        self.off_y = 0.0
        self.drag = None
        self.mode = "view"          # "view" | "calibrate" | "help"
        self.print_mode = False
        self.overview = False       # zoomed-out overview (NOT real size)
        self.view_zoom = 1.0
        self.cal_backup = None
        self.page_w_mm = self.page_h_mm = 0.0
        self.page_w_px = self.page_h_px = 0.0
        self.ui = 1.0

        root.title(APP_NAME)
        root.configure(bg=BG)
        self.canvas = tk.Canvas(root, bg=BG, highlightthickness=0, cursor="fleur")
        self.canvas.pack(fill="both", expand=True)

        root.attributes("-fullscreen", True)
        root.update_idletasks()
        root.update()

        if IS_WINDOWS:
            try:
                dpi = user32.GetDpiForWindow(self.hwnd())
                root.tk.call("tk", "scaling", dpi / 72.0)
                self.ui = dpi / 96.0
            except Exception:
                pass

        self.print_btn = tk.Button(root, text="Print & Scale  (P)", font=("Segoe UI", 11),
                                   bg="#1b1b1e", fg="#ffffff", activebackground=ACCENT,
                                   activeforeground="#ffffff", relief="flat", bd=0,
                                   padx=14, pady=6, cursor="hand2",
                                   command=self.toggle_print_mode)
        self.print_btn.place(x=12, y=12)
        self.panel = PrintPanel(self)

        self.bind_keys()
        self.update_scale()

        if self.ppm_x is None:
            self.redraw()
            root.after(300, lambda: self.open_settings(first_run=True))
        if initial_file:
            root.after(100, lambda: self.load_pdf(initial_file))
        else:
            self.redraw()

    # ---------------------------------------------------------------- utils
    def hwnd(self):
        try:
            return int(self.root.wm_frame(), 16)
        except Exception:
            return self.root.winfo_id()

    @property
    def W(self):
        return max(1, self.canvas.winfo_width())

    @property
    def H(self):
        return max(1, self.canvas.winfo_height())

    def left_inset(self):
        """Width covered by the print panel on the left."""
        if self.print_mode and self.mode != "calibrate":
            return self.panel.frame.winfo_width()
        return 0

    # -------------------------------------------------------- scale / size
    def update_scale(self):
        """Work out how many screen pixels make one real millimetre."""
        if IS_WINDOWS:
            mon = monitor_for_window(self.hwnd())
            self.mon = mon
            self.detected = detect_physical_size(mon["device"])
        else:
            self.mon = {"device": "", "left": 0, "top": 0,
                        "w": self.root.winfo_screenwidth(), "h": self.root.winfo_screenheight()}
            self.detected = None

        px_w, px_h = self.mon["w"], self.mon["h"]
        if self.detected:
            dw, dh, name = self.detected
            # monitor rotated to portrait? EDID always reports landscape size
            if (px_w > px_h) != (dw > dh):
                self.detected = (dh, dw, name)

        self.screen_mm = None
        self.size_source = ""
        if self.s["mode"] == "manual" and self.s["manual_w_mm"] and self.s["manual_h_mm"]:
            self.screen_mm = (float(self.s["manual_w_mm"]), float(self.s["manual_h_mm"]))
            self.size_source = "manual"
        elif self.detected:
            self.screen_mm = (self.detected[0], self.detected[1])
            self.size_source = "auto-detected"

        if self.screen_mm:
            self.ppm_x = px_w / self.screen_mm[0] * self.s["cal_x"]
            self.ppm_y = px_h / self.screen_mm[1] * self.s["cal_y"]
        else:
            self.ppm_x = self.ppm_y = None

    # ---------------------------------------------------------------- keys
    def bind_keys(self):
        r = self.root
        r.bind("<Key>", self.on_key)
        self.canvas.bind("<ButtonPress-1>", lambda e: self.on_press(e, 1))
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", lambda e: setattr(self, "drag", None))
        self.canvas.bind("<ButtonPress-3>", lambda e: self.on_press(e, 3))
        self.canvas.bind("<B3-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-3>", lambda e: setattr(self, "drag", None))
        self.canvas.bind("<MouseWheel>", self.on_wheel)
        self.canvas.bind("<Configure>", lambda e: self.after_resize())

    def after_resize(self):
        self.clamp()
        self.redraw()

    def on_key(self, e):
        # typing in a text box of the print panel must not trigger shortcuts
        try:
            focus = self.root.focus_get()
        except Exception:
            focus = None
        if isinstance(focus, (tk.Entry, ttk.Entry, ttk.Combobox, tk.Spinbox)):
            if e.keysym == "Escape":
                self.canvas.focus_set()
            return
        try:
            if focus is not None and focus.winfo_toplevel() is not self.root:
                return
        except Exception:
            pass

        k = e.keysym
        shift = bool(e.state & 0x1)
        ctrl = bool(e.state & 0x4)

        if self.mode == "help":
            self.mode = "view"
            self.redraw()
            return
        if self.mode == "calibrate":
            self.calibrate_key(k, shift)
            return

        if k == "Escape":
            if self.print_mode:
                self.toggle_print_mode()
            else:
                self.quit()
        elif k in ("o", "O"):
            self.open_dialog()
        elif k in ("p", "P"):
            self.toggle_print_mode()
        elif k in ("z", "Z"):
            self.toggle_overview()
        elif k in ("s", "S"):
            self.open_settings()
        elif k in ("c", "C"):
            self.start_calibration()
        elif k in ("h", "H"):
            self.s["show_hud"] = not self.s["show_hud"]
            save_settings(self.s)
            self.redraw()
        elif k in ("r", "R"):
            self.s["show_ruler"] = not self.s["show_ruler"]
            save_settings(self.s)
            self.redraw()
        elif k in ("m", "M"):
            self.next_monitor()
        elif k in ("F1", "question", "slash"):
            self.mode = "help"
            self.redraw()
        elif k in ("Next", "space") and self.doc:
            self.goto_page(self.page_index + 1)
        elif k in ("Prior", "BackSpace") and self.doc:
            self.goto_page(self.page_index - 1)
        elif k == "Home" and self.doc:
            self.goto_page(0)
        elif k == "End" and self.doc:
            self.goto_page(len(self.doc) - 1)
        elif k in ("Left", "Right", "Up", "Down") and self.doc and self.ppm_x:
            if ctrl:   # jump to edge
                big = 10 ** 9
                dx = {"Left": -big, "Right": big}.get(k, 0)
                dy = {"Up": -big, "Down": big}.get(k, 0)
            else:
                step_mm = 50 if shift else 10
                dx = {"Left": -1, "Right": 1}.get(k, 0) * step_mm * self.ppm_x
                dy = {"Up": -1, "Down": 1}.get(k, 0) * step_mm * self.ppm_y
            self.pan(dx, dy)

    def on_press(self, e, button):
        self.canvas.focus_set()
        if self.print_mode and button == 1 and self.doc:
            self.drag = ("frame", e.x, e.y, self.pr["fx"], self.pr["fy"])
        else:
            self.drag = ("pan", e.x, e.y)

    def on_drag(self, e):
        if not self.drag or self.mode != "view":
            return
        if self.drag[0] == "pan":
            dx, dy = self.drag[1] - e.x, self.drag[2] - e.y
            self.drag = ("pan", e.x, e.y)
            self.pan(dx, dy)
        else:
            _, sx0, sy0, fx0, fy0 = self.drag
            sx, sy = self.px_per_print_mm()
            self.move_frame(fx0 + (e.x - sx0) / sx, fy0 + (e.y - sy0) / sy, snap=True)

    def on_wheel(self, e):
        if not (self.doc and self.ppm_x and self.mode == "view"):
            return
        amount = -e.delta / 120 * 15   # 15 mm per wheel notch
        if e.state & 0x1:
            self.pan(amount * self.ppm_x, 0)
        else:
            self.pan(0, amount * self.ppm_y)

    def quit(self):
        self.save_print_settings()
        save_settings(self.s)
        self.root.destroy()

    # ----------------------------------------------------------- monitors
    def next_monitor(self):
        if not IS_WINDOWS:
            return
        mons = all_monitors()
        if len(mons) < 2:
            self.flash("Only one monitor found")
            return
        cur = self.mon["device"]
        idx = next((i for i, m in enumerate(mons) if m["device"] == cur), -1)
        m = mons[(idx + 1) % len(mons)]
        self.root.attributes("-fullscreen", False)
        self.root.geometry(f"800x600+{m['left'] + 40}+{m['top'] + 40}")
        self.root.update()
        self.root.attributes("-fullscreen", True)
        self.root.update()
        self.update_scale()
        if self.ppm_x is None:
            self.open_settings(first_run=True)
        self.render_page(keep_position=True)

    # ---------------------------------------------------------------- PDF
    def open_dialog(self):
        path = filedialog.askopenfilename(
            parent=self.root, title="Open PDF",
            initialdir=self.s.get("last_dir") or os.path.expanduser("~"),
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")])
        if path:
            self.load_pdf(path)

    def load_pdf(self, path):
        try:
            doc = fitz.open(path)
            if doc.needs_pass:
                messagebox.showerror(APP_NAME, "This PDF is password protected.", parent=self.root)
                return
        except Exception as ex:
            messagebox.showerror(APP_NAME, f"Could not open the file:\n\n{ex}", parent=self.root)
            return
        if self.doc:
            self.doc.close()
        self.doc = doc
        self.doc_path = path
        self.s["last_dir"] = os.path.dirname(path)
        save_settings(self.s)
        self.page_index = 0
        self.pr["fx"] = self.pr["fy"] = 0.0
        self.render_page()
        self.panel.refresh_info()

    def goto_page(self, i):
        i = max(0, min(len(self.doc) - 1, i))
        if i != self.page_index:
            self.page_index = i
            self.render_page()
            self.panel.refresh_info()

    # ----------------------------------------------- print geometry (in mm)
    def print_scale(self):
        return self.pr["scale"] if self.print_mode else 1.0

    def px_per_print_mm(self):
        """Screen pixels per millimetre of the (scaled) printed result."""
        return self.ppm_x * self.view_zoom, self.ppm_y * self.view_zoom

    def paper_dims(self):
        p = self.pr
        if p["paper"] == "Custom":
            w, h = float(p["custom_w"]), float(p["custom_h"])
        else:
            w, h = PAPER_SIZES[p["paper"]]
        w, h = min(w, h), max(w, h)
        return (h, w) if p["orient"] == "landscape" else (w, h)

    def printable_dims(self):
        w, h = self.paper_dims()
        m = self.pr["margin"]
        return max(1.0, w - 2 * m), max(1.0, h - 2 * m)

    def printed_page_mm(self, page=None):
        """Size of a page after applying the print scale."""
        if page is None:
            return self.page_w_mm * self.pr["scale"], self.page_h_mm * self.pr["scale"]
        r = page.rect
        return (r.width * 25.4 / 72 * self.pr["scale"], r.height * 25.4 / 72 * self.pr["scale"])

    def compute_tiles(self, pw, ph):
        """Top-left corners (printed mm) of the printable areas: [(x, y, row, col)]."""
        fw, fh = self.printable_dims()
        fx, fy = self.pr["fx"], self.pr["fy"]
        if not self.pr["poster"]:
            return [(fx, fy, 0, 0)], 1, 1
        ov = max(0.0, min(self.pr["overlap"], min(fw, fh) * 0.5))
        sx, sy = fw - ov, fh - ov

        def axis(start, step, total):
            x = start
            while x > 0:
                x -= step
            out = []
            while x < total - 0.01:
                out.append(x)
                x += step
            return out or [start]

        xs, ys = axis(fx, sx, pw), axis(fy, sy, ph)
        tiles = [(x, y, r, c) for r, y in enumerate(ys) for c, x in enumerate(xs)]
        return tiles, len(xs), len(ys)

    def move_frame(self, fx, fy, snap=False):
        pw, ph = self.printed_page_mm()
        fw, fh = self.printable_dims()
        if snap and self.ppm_x:
            sx, sy = self.px_per_print_mm()
            for target in (0.0, (pw - fw) / 2, pw - fw):
                if abs(fx - target) * sx < 14:
                    fx = target
                    break
            for target in (0.0, (ph - fh) / 2, ph - fh):
                if abs(fy - target) * sy < 14:
                    fy = target
                    break
        # keep at least a bit of the sheet on the page
        fx = max(-fw + 5, min(fx, pw - 5))
        fy = max(-fh + 5, min(fy, ph - 5))
        self.pr["fx"], self.pr["fy"] = fx, fy
        self.clamp()
        self.redraw()
        self.panel.refresh_info()

    def anchor_frame(self, ax, ay):
        """ax, ay in {0, 0.5, 1}: left/center/right, top/middle/bottom."""
        pw, ph = self.printed_page_mm()
        fw, fh = self.printable_dims()
        self.move_frame(ax * (pw - fw), ay * (ph - fh))
        self.show_frame()

    def show_frame(self):
        """Scroll the view so the sheet (or first tile) is visible."""
        if not (self.print_mode and self.doc and self.ppm_x):
            return
        sx, sy = self.px_per_print_mm()
        fw, fh = self.printable_dims()
        L = self.left_inset()
        vw, vh = self.W - L, self.H
        cx = (self.pr["fx"] + fw / 2) * sx
        cy = (self.pr["fy"] + fh / 2) * sy
        self.off_x = cx - vw / 2
        self.off_y = cy - vh / 2
        self.clamp()
        self.redraw()

    def content_bounds_mm(self):
        """Area (printed mm, relative to the page's top-left) that may need to be shown."""
        pw, ph = (self.printed_page_mm() if self.print_mode else (self.page_w_mm, self.page_h_mm))
        b = [0.0, 0.0, pw, ph]
        if self.print_mode:
            m = self.pr["margin"]
            fw, fh = self.printable_dims()
            tiles, _, _ = self.compute_tiles(pw, ph)
            for x, y, _, _ in tiles:
                b[0] = min(b[0], x - m)
                b[1] = min(b[1], y - m)
                b[2] = max(b[2], x + fw + m)
                b[3] = max(b[3], y + fh + m)
        return b

    def content_bounds_px(self):
        sx, sy = self.px_per_print_mm()
        b = self.content_bounds_mm()
        return b[0] * sx, b[1] * sy, b[2] * sx, b[3] * sy

    def compute_view_zoom(self):
        if not (self.overview and self.ppm_x and self.doc):
            self.view_zoom = 1.0
            return
        b = self.content_bounds_mm()
        vw = (self.W - self.left_inset()) * 0.92
        vh = self.H * 0.92
        self.view_zoom = min(vw / ((b[2] - b[0]) * self.ppm_x), vh / ((b[3] - b[1]) * self.ppm_y))

    def toggle_overview(self):
        if not self.doc:
            return
        self.overview = not self.overview
        self.render_page(keep_position=True)

    # -------------------------------------------------------- print mode
    def toggle_print_mode(self):
        if not self.print_mode and not self.doc:
            self.flash("Open a PDF first (press O)")
            return
        if not self.ppm_x:
            self.open_settings(first_run=True)
            return
        self.print_mode = not self.print_mode
        if self.print_mode:
            self.print_btn.place_forget()
            self.panel.show()
        else:
            self.panel.hide()
            self.print_btn.place(x=12, y=12)
            self.save_print_settings()
        self.root.update_idletasks()
        self.render_page(keep_position=False)
        if self.print_mode:
            self.show_frame()

    def print_changed(self, old_scale=None):
        """Called by the panel after any setting changed."""
        if old_scale and old_scale != self.pr["scale"]:
            k = self.pr["scale"] / old_scale
            self.pr["fx"] *= k
            self.pr["fy"] *= k
        self.save_print_settings()
        self.render_page(keep_position=True)
        self.panel.refresh_info()

    def save_print_settings(self):
        keep = {k: v for k, v in self.pr.items() if k not in ("fx", "fy")}
        self.s["print"] = keep
        save_settings(self.s)

    def build_print_pdf(self):
        pr = self.pr
        mm2pt = 72.0 / 25.4
        paper_w, paper_h = self.paper_dims()
        m = pr["margin"]
        fw, fh = self.printable_dims()
        s = pr["scale"]
        out = fitz.open()
        pages = range(len(self.doc)) if pr["all_pages"] else [self.page_index]
        name = os.path.basename(self.doc_path)
        for pi in pages:
            src = self.doc[pi]
            r = src.rect
            pw, ph = self.printed_page_mm(src)
            tiles, ncols, nrows = self.compute_tiles(pw, ph)
            for (tx, ty, row, col) in tiles:
                newp = out.new_page(width=paper_w * mm2pt, height=paper_h * mm2pt)
                clip = fitz.Rect(r.x0 + tx / s * mm2pt, r.y0 + ty / s * mm2pt,
                                 r.x0 + (tx + fw) / s * mm2pt, r.y0 + (ty + fh) / s * mm2pt)
                inter = fitz.Rect(max(clip.x0, r.x0), max(clip.y0, r.y0),
                                  min(clip.x1, r.x1), min(clip.y1, r.y1))
                if inter.width > 0.01 and inter.height > 0.01:
                    ox, oy = m * mm2pt, m * mm2pt
                    target = fitz.Rect(ox + (inter.x0 - clip.x0) * s, oy + (inter.y0 - clip.y0) * s,
                                       ox + (inter.x1 - clip.x0) * s, oy + (inter.y1 - clip.y0) * s)
                    newp.show_pdf_page(target, self.doc, pi, clip=inter, keep_proportion=False)
                if pr["poster"] and m >= 3:
                    self.draw_corner_marks(newp, m * mm2pt, fw * mm2pt, fh * mm2pt)
                if pr["label"] and m >= 4:
                    txt = f"{name}  |  page {pi + 1}  |  scale {s * 100:.2f}%"
                    if pr["poster"]:
                        txt += f"  |  sheet row {row + 1} col {col + 1}  ({nrows} x {ncols})"
                    newp.insert_text((m * mm2pt, paper_h * mm2pt - m * mm2pt * 0.4), txt,
                                     fontsize=min(7.0, m * mm2pt * 0.45), color=(0.45, 0.45, 0.45))
        return out

    @staticmethod
    def draw_corner_marks(page, o, w, h):
        """Little marks in the margin at the corners of the printed area (for glueing posters)."""
        L = min(o * 0.8, 12)
        col = (0.5, 0.5, 0.5)
        for (x, y) in ((o, o), (o + w, o), (o, o + h), (o + w, o + h)):
            dx = -1 if x == o else 1
            dy = -1 if y == o else 1
            page.draw_line((x, y + dy * 2), (x, y + dy * L), color=col, width=0.3)
            page.draw_line((x + dx * 2, y), (x + dx * L, y), color=col, width=0.3)

    def default_out_name(self):
        stem = os.path.splitext(os.path.basename(self.doc_path))[0]
        pr = self.pr
        return f"{stem}_{pr['paper']}_{pr['scale'] * 100:g}pct.pdf".replace("%", "")

    def save_print_pdf(self):
        if not self.doc:
            return
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Save scaled PDF", defaultextension=".pdf",
            initialdir=self.s.get("last_dir") or os.path.expanduser("~"),
            initialfile=self.default_out_name(), filetypes=[("PDF files", "*.pdf")])
        if not path:
            return
        try:
            out = self.build_print_pdf()
            n = len(out)
            out.save(path, garbage=3, deflate=True)
            out.close()
        except Exception as ex:
            messagebox.showerror(APP_NAME, f"Could not save the PDF:\n\n{ex}", parent=self.root)
            return
        self.flash(f"Saved {n} sheet(s): {os.path.basename(path)}")

    def print_now(self):
        if not self.doc:
            return
        try:
            out = self.build_print_pdf()
            path = os.path.join(tempfile.gettempdir(), self.default_out_name())
            out.save(path, garbage=3, deflate=True)
            out.close()
        except Exception as ex:
            messagebox.showerror(APP_NAME, f"Could not prepare the print:\n\n{ex}", parent=self.root)
            return
        messagebox.showinfo(
            APP_NAME,
            "The ready-to-print file will now open in your PDF viewer.\n\n"
            "In its Print dialog choose:\n"
            f"   - Paper: {self.pr['paper']}\n"
            "   - Size: \"Actual size\" / 100%  (NOT \"Fit\")\n\n"
            "Everything is already scaled and positioned, so nothing else needs changing.",
            parent=self.root)
        try:
            os.startfile(path)
        except Exception as ex:
            messagebox.showerror(APP_NAME, f"Could not open the PDF viewer:\n\n{ex}\n\n"
                                 f"The file is here:\n{path}", parent=self.root)

    # ---------------------------------------------------------- rendering
    def zoom_factors(self):
        # PDF unit = 1 point = 1/72 inch = 25.4/72 mm
        k = self.print_scale() * self.view_zoom
        return self.ppm_x * 25.4 / 72.0 * k, self.ppm_y * 25.4 / 72.0 * k

    def render_page(self, keep_position=False):
        self.full_img = None
        self.clip_img = None
        if not (self.doc and self.ppm_x):
            self.redraw()
            return
        page = self.doc[self.page_index]
        rect = page.rect        # = CropBox, i.e. what gets printed
        self.page_w_mm = rect.width * 25.4 / 72.0
        self.page_h_mm = rect.height * 25.4 / 72.0
        self.compute_view_zoom()
        zx, zy = self.zoom_factors()
        self.page_w_px = rect.width * zx
        self.page_h_px = rect.height * zy

        self.canvas.config(cursor="watch")
        self.root.update_idletasks()
        try:
            if self.page_w_px * self.page_h_px <= FULL_RENDER_LIMIT:
                pix = page.get_pixmap(matrix=fitz.Matrix(zx, zy), alpha=False)
                self.full_img = tk.PhotoImage(data=pix.tobytes("ppm"))
        except Exception as ex:
            messagebox.showerror(APP_NAME, f"Could not render page:\n\n{ex}", parent=self.root)
        self.canvas.config(cursor="fleur")

        if not keep_position:
            b = self.content_bounds_px()
            self.off_x, self.off_y = b[0], b[1]
        self.clamp()
        self.redraw()

    def page_origin(self):
        """Screen position of the page's top-left corner."""
        L = self.left_inset()
        vw, vh = self.W - L, self.H
        bx0, by0, bx1, by1 = self.content_bounds_px()
        if bx1 - bx0 <= vw:
            x0 = L + (vw - (bx1 - bx0)) / 2 - bx0
        else:
            x0 = L - self.off_x
        if by1 - by0 <= vh:
            y0 = (vh - (by1 - by0)) / 2 - by0
        else:
            y0 = -self.off_y
        return x0, y0

    def clamp(self):
        if not (self.doc and self.ppm_x and self.page_w_px):
            return
        L = self.left_inset()
        bx0, by0, bx1, by1 = self.content_bounds_px()
        self.off_x = max(bx0, min(self.off_x, max(bx0, bx1 - (self.W - L))))
        self.off_y = max(by0, min(self.off_y, max(by0, by1 - self.H)))

    def pan(self, dx, dy):
        ox, oy = self.off_x, self.off_y
        self.off_x += dx
        self.off_y += dy
        self.clamp()
        if (ox, oy) != (self.off_x, self.off_y):
            self.redraw()

    def render_clip(self, x0, y0):
        """For gigantic pages: render only the part that is on screen."""
        page = self.doc[self.page_index]
        zx, zy = self.zoom_factors()
        vx0 = max(0.0, -x0)
        vy0 = max(0.0, -y0)
        vx1 = min(self.page_w_px, self.W - x0)
        vy1 = min(self.page_h_px, self.H - y0)
        if vx1 <= vx0 or vy1 <= vy0:
            return None
        r = page.rect
        clip = fitz.Rect(r.x0 + vx0 / zx, r.y0 + vy0 / zy, r.x0 + vx1 / zx, r.y0 + vy1 / zy)
        pix = page.get_pixmap(matrix=fitz.Matrix(zx, zy), clip=clip, alpha=False)
        self.clip_img = tk.PhotoImage(data=pix.tobytes("ppm"))
        return x0 + vx0, y0 + vy0

    # ------------------------------------------------------------- drawing
    def redraw(self):
        c = self.canvas
        c.delete("all")
        if self.mode == "calibrate":
            self.draw_calibration()
            return

        if not self.ppm_x:
            self.center_text("Screen size unknown - press S to open Settings")
        elif not self.doc:
            self.center_text("Press  O  to open a PDF\n\nP = print & scale    S = settings    "
                             "C = calibrate    F1 = help    Esc = quit")
        else:
            x0, y0 = self.page_origin()
            # soft shadow + paper
            c.create_rectangle(x0 + 6, y0 + 6, x0 + self.page_w_px + 6, y0 + self.page_h_px + 6,
                               fill="#141416", outline="")
            if self.full_img is not None:
                c.create_image(round(x0), round(y0), image=self.full_img, anchor="nw")
            else:
                pos = self.render_clip(x0, y0)
                if pos:
                    c.create_image(round(pos[0]), round(pos[1]), image=self.clip_img, anchor="nw")
            if self.print_mode:
                self.draw_sheets(x0, y0)
            if self.s["show_ruler"]:
                self.draw_ruler(x0, y0)
            bx0, by0, bx1, by1 = self.content_bounds_px()
            if bx1 - bx0 > self.W - self.left_inset() or by1 - by0 > self.H:
                self.draw_minimap(x0, y0)

        if self.s["show_hud"]:
            self.draw_hud()
        if self.mode == "help":
            self.draw_help()

    def draw_sheets(self, x0, y0):
        c = self.canvas
        sx, sy = self.px_per_print_mm()
        pw, ph = self.printed_page_mm()
        fw, fh = self.printable_dims()
        m = self.pr["margin"]
        tiles, ncols, nrows = self.compute_tiles(pw, ph)

        if not self.pr["poster"]:
            # dim everything that will NOT be printed
            fx, fy = tiles[0][0], tiles[0][1]
            ax0, ay0 = x0 + fx * sx, y0 + fy * sy
            ax1, ay1 = ax0 + fw * sx, ay0 + fh * sy
            W, H = self.W, self.H
            for r in ((0, 0, W, ay0), (0, ay1, W, H), (0, ay0, ax0, ay1), (ax1, ay0, W, ay1)):
                if r[2] > r[0] and r[3] > r[1]:
                    c.create_rectangle(*r, fill="#000000", stipple="gray50", outline="")

        for i, (tx, ty, row, col) in enumerate(tiles):
            ax0, ay0 = x0 + tx * sx, y0 + ty * sy
            ax1, ay1 = ax0 + fw * sx, ay0 + fh * sy
            if m > 0:   # paper edge
                c.create_rectangle(ax0 - m * sx, ay0 - m * sy, ax1 + m * sx, ay1 + m * sy,
                                   outline="#ff9f1a", dash=(6, 4), width=2)
            c.create_rectangle(ax0, ay0, ax1, ay1, outline=ACCENT, width=3)
            if self.pr["poster"]:
                c.create_text(ax0 + 10, ay0 + 8, anchor="nw", fill=ACCENT,
                              text=f"{i + 1}", font=("Segoe UI", 16, "bold"))

        p = self.pr
        txt = (f"{p['paper']} {p['orient']}  @ {p['scale'] * 100:.2f}%   "
               + (f"poster: {len(tiles)} sheets ({nrows} x {ncols})" if p["poster"]
                  else "drag to choose the printed area"))
        fx, fy = tiles[0][0], tiles[0][1]
        tx = x0 + fx * sx
        ty = y0 + (fy - m) * sy - 14
        tx = max(self.left_inset() + 10, tx)
        ty = max(14, ty)
        t = c.create_text(tx, ty, anchor="sw" if ty > 30 else "nw", text=txt, fill="#ffffff",
                          font=("Segoe UI", 10, "bold"))
        bx = c.bbox(t)
        r = c.create_rectangle(bx[0] - 6, bx[1] - 3, bx[2] + 6, bx[3] + 3, fill=ACCENT, outline="")
        c.tag_raise(t, r)

    def center_text(self, text):
        L = self.left_inset()
        self.canvas.create_text(L + (self.W - L) / 2, self.H / 2, text=text, fill="#d0d0d0",
                                font=("Segoe UI", 16), justify="center")

    def draw_hud(self):
        lines = []
        if self.overview:
            lines.append("OVERVIEW - NOT real size  (press Z for real size)")
        if self.doc:
            name = os.path.basename(self.doc_path)
            lines.append(f"{name}    page {self.page_index + 1} / {len(self.doc)}")
            lines.append(f"Page: {self.page_w_mm:.1f} x {self.page_h_mm:.1f} mm  "
                         f"({paper_name(self.page_w_mm, self.page_h_mm)})")
            if self.print_mode:
                pw, ph = self.printed_page_mm()
                lines.append(f"Printed at {self.pr['scale'] * 100:.2f}%: {pw:.1f} x {ph:.1f} mm")
        if self.screen_mm:
            sw, sh = self.screen_mm
            diag = math.hypot(sw, sh) / 25.4
            lines.append(f"Screen: {sw:.0f} x {sh:.0f} mm ({diag:.1f}\") {self.size_source}"
                         f"  |  {self.mon['w']} x {self.mon['h']} px")
            if abs(self.s["cal_x"] - 1) > 1e-6 or abs(self.s["cal_y"] - 1) > 1e-6:
                lines.append(f"Calibration: X {self.s['cal_x'] * 100:.2f}%  "
                             f"Y {self.s['cal_y'] * 100:.2f}%")
        lines.append("F1 help   H hide this")
        text = "\n".join(lines)
        x = self.left_inset() + 14
        y = 12 if self.print_mode else 12 + self.print_btn.winfo_height() + 12
        t = self.canvas.create_text(x, y, text=text, anchor="nw",
                                    fill="#ff6b6b" if self.overview else "#e8e8e8",
                                    font=("Segoe UI", 10))
        bx = self.canvas.bbox(t)
        r = self.canvas.create_rectangle(bx[0] - 8, bx[1] - 6, bx[2] + 8, bx[3] + 6,
                                         fill="#1b1b1e", outline="#3a3a40")
        self.canvas.tag_raise(t, r)

    def draw_minimap(self, x0, y0):
        c = self.canvas
        bx0, by0, bx1, by1 = self.content_bounds_px()
        maxs = 170
        sc = min(maxs / (bx1 - bx0), maxs / (by1 - by0))
        mw, mh = (bx1 - bx0) * sc, (by1 - by0) * sc
        mx = self.W - mw - 20
        my = self.H - mh - 20
        c.create_rectangle(mx - 6, my - 6, mx + mw + 6, my + mh + 6, fill="#1b1b1e",
                           outline="#3a3a40")

        def box(ax0, ay0, ax1, ay1, **kw):   # coordinates relative to page origin (px)
            c.create_rectangle(mx + (ax0 - bx0) * sc, my + (ay0 - by0) * sc,
                               mx + (ax1 - bx0) * sc, my + (ay1 - by0) * sc, **kw)

        box(0, 0, self.page_w_px, self.page_h_px, fill="#f4f4f4", outline="#888")
        if self.print_mode:
            sx, sy = self.px_per_print_mm()
            pw, ph = self.printed_page_mm()
            fw, fh = self.printable_dims()
            for tx, ty, _, _ in self.compute_tiles(pw, ph)[0]:
                box(tx * sx, ty * sy, (tx + fw) * sx, (ty + fh) * sy, outline="#ff9f1a", width=1)
        L = self.left_inset()
        box(L - x0, -y0, self.W - x0, self.H - y0, outline=ACCENT, width=2)

    def draw_ruler(self, x0, y0):
        """mm rulers along the top and left screen edges, measured from the page corner."""
        c = self.canvas
        th = 34
        L = self.left_inset()
        sx, sy = self.px_per_print_mm()
        c.create_rectangle(L, 0, self.W, th, fill="#fffbe6", outline="#999")
        c.create_rectangle(L, 0, L + th, self.H, fill="#fffbe6", outline="#999")

        def step_for(px_per_mm):
            for st in (1, 5, 10, 50, 100, 500):
                if st * px_per_mm >= 4:
                    return st
            return 1000

        st = step_for(sx)
        mm0 = math.floor((L - x0) / sx / st) * st
        mm1 = math.ceil((self.W - x0) / sx)
        for mm in range(int(mm0), int(mm1) + 1, st):
            x = x0 + mm * sx
            if x < L + th:
                continue
            big = mm % (10 * st) == 0
            ln = 18 if big else (11 if mm % (5 * st) == 0 else 6)
            c.create_line(x, 0, x, ln, fill="#222")
            if big:
                c.create_text(x + 2, 20, text=str(mm // 10), anchor="nw", fill="#222",
                              font=("Segoe UI", 7))
        st = step_for(sy)
        mm0 = math.floor(-y0 / sy / st) * st
        mm1 = math.ceil((self.H - y0) / sy)
        for mm in range(int(mm0), int(mm1) + 1, st):
            y = y0 + mm * sy
            if y < th:
                continue
            big = mm % (10 * st) == 0
            ln = 18 if big else (11 if mm % (5 * st) == 0 else 6)
            c.create_line(L, y, L + ln, y, fill="#222")
            if big:
                c.create_text(L + 20, y + 2, text=str(mm // 10), anchor="nw", fill="#222",
                              font=("Segoe UI", 7))
        c.create_rectangle(L, 0, L + th, th, fill="#fffbe6", outline="#999")
        c.create_text(L + th / 2, th / 2, text="cm", fill="#555", font=("Segoe UI", 7))

    def draw_help(self):
        text = (
            "TrueSize PDF  -  keyboard & mouse\n\n"
            "O                     open a PDF\n"
            "Arrow keys            move around the page (10 mm)\n"
            "Shift + arrows        move faster (50 mm)\n"
            "Ctrl + arrows         jump to that edge of the page\n"
            "Mouse drag / wheel    move around (Shift + wheel = sideways)\n"
            "Page Down / Space     next page\n"
            "Page Up / Backspace   previous page\n"
            "Home / End            first / last page\n"
            "P                     Print & Scale panel\n"
            "Z                     overview (zoomed out, NOT real size) / real size\n"
            "R                     show / hide mm ruler\n"
            "H                     show / hide info box\n"
            "S                     settings (screen size)\n"
            "C                     fine-calibrate with a real ruler or bank card\n"
            "M                     move to the next monitor\n"
            "Esc                   close print panel / quit\n\n"
            "In Print & Scale mode:\n"
            "  left-drag            move the paper sheet over the drawing\n"
            "  right-drag / wheel   move the view\n"
            "  the screen shows the printed result at its real size\n\n"
            "Tip: when printing, choose \"Actual size\" / 100% - not \"Fit to page\".\n\n"
            "Press any key to close this help")
        L = self.left_inset()
        t = self.canvas.create_text(L + (self.W - L) / 2, self.H / 2, text=text, fill="#f0f0f0",
                                    font=("Consolas", 12), justify="left")
        bx = self.canvas.bbox(t)
        r = self.canvas.create_rectangle(bx[0] - 30, bx[1] - 24, bx[2] + 30, bx[3] + 24,
                                         fill="#1b1b1e", outline=ACCENT, width=2)
        self.canvas.tag_raise(t, r)

    def flash(self, msg):
        L = self.left_inset()
        t = self.canvas.create_text(L + (self.W - L) / 2, self.H - 60, text=msg, fill="#fff",
                                    font=("Segoe UI", 13))
        bx = self.canvas.bbox(t)
        r = self.canvas.create_rectangle(bx[0] - 14, bx[1] - 8, bx[2] + 14, bx[3] + 8,
                                         fill="#333", outline=ACCENT)
        self.canvas.tag_raise(t, r)
        self.root.after(2200, self.redraw)

    # --------------------------------------------------------- calibration
    def start_calibration(self):
        if not self.ppm_x:
            self.open_settings(first_run=True)
            return
        self.cal_backup = (self.s["cal_x"], self.s["cal_y"])
        self.mode = "calibrate"
        self.print_btn.place_forget()
        self.panel.hide()
        self.redraw()

    def end_calibration(self):
        self.mode = "view"
        if self.print_mode:
            self.panel.show()
        else:
            self.print_btn.place(x=12, y=12)
        self.root.update_idletasks()

    def calibrate_key(self, k, shift):
        step = 0.0005 if shift else 0.005
        if k == "Right":
            self.s["cal_x"] *= 1 + step
        elif k == "Left":
            self.s["cal_x"] /= 1 + step
        elif k == "Up":
            self.s["cal_y"] *= 1 + step
        elif k == "Down":
            self.s["cal_y"] /= 1 + step
        elif k in ("plus", "equal", "KP_Add"):
            self.s["cal_x"] *= 1 + step
            self.s["cal_y"] *= 1 + step
        elif k in ("minus", "KP_Subtract"):
            self.s["cal_x"] /= 1 + step
            self.s["cal_y"] /= 1 + step
        elif k == "BackSpace":
            self.s["cal_x"] = self.s["cal_y"] = 1.0
        elif k in ("Return", "KP_Enter"):
            self.end_calibration()
            save_settings(self.s)
            self.update_scale()
            self.render_page(keep_position=True)
            return
        elif k == "Escape":
            self.s["cal_x"], self.s["cal_y"] = self.cal_backup
            self.end_calibration()
            self.update_scale()
            self.render_page(keep_position=True)
            return
        else:
            return
        self.update_scale()
        self.redraw()

    def draw_calibration(self):
        c = self.canvas
        W, H = self.W, self.H
        px, py = self.ppm_x, self.ppm_y
        c.create_rectangle(0, 0, W, H, fill="#ffffff", outline="")

        # horizontal 100 mm ruler
        L = 100
        x0 = W / 2 - L * px / 2
        y0 = H * 0.18
        c.create_line(x0, y0, x0 + L * px, y0, fill="#000", width=1)
        for mm in range(L + 1):
            ln = 26 if mm % 10 == 0 else (16 if mm % 5 == 0 else 9)
            c.create_line(x0 + mm * px, y0, x0 + mm * px, y0 + ln, fill="#000")
            if mm % 10 == 0:
                c.create_text(x0 + mm * px, y0 + 38, text=str(mm // 10), fill="#000",
                              font=("Segoe UI", 9))
        c.create_text(W / 2, y0 - 22, text="This line must be exactly 10 cm  (Left / Right)",
                      fill="#000", font=("Segoe UI", 12, "bold"))

        # vertical 100 mm ruler
        vx = W * 0.15
        vy0 = H / 2 - L * py / 2 + 40
        c.create_line(vx, vy0, vx, vy0 + L * py, fill="#000")
        for mm in range(L + 1):
            ln = 26 if mm % 10 == 0 else (16 if mm % 5 == 0 else 9)
            c.create_line(vx, vy0 + mm * py, vx + ln, vy0 + mm * py, fill="#000")
            if mm % 10 == 0:
                c.create_text(vx + 40, vy0 + mm * py, text=str(mm // 10), fill="#000",
                              font=("Segoe UI", 9))
        c.create_text(vx - 20, vy0 + L * py / 2, text="10 cm  (Up / Down)", angle=90,
                      fill="#000", font=("Segoe UI", 12, "bold"))

        # bank-card outline (ISO/IEC 7810 ID-1: 85.60 x 53.98 mm)
        cw, ch = 85.60 * px, 53.98 * py
        cx0 = W / 2 - cw / 2 + 60
        cy0 = H / 2 - ch / 2 + 50
        c.create_rectangle(cx0, cy0, cx0 + cw, cy0 + ch, outline=ACCENT, width=2)
        c.create_text(cx0 + cw / 2, cy0 + ch / 2,
                      text="Bank / ID card\n85.60 x 53.98 mm\nhold a card here",
                      fill=ACCENT, font=("Segoe UI", 11), justify="center")

        info = (f"Hold a real ruler or any bank card against the screen.\n"
                f"Left/Right = horizontal, Up/Down = vertical, +/- = both   "
                f"(hold Shift for tiny steps)\n"
                f"Enter = save    Esc = cancel    Backspace = reset\n\n"
                f"X {self.s['cal_x'] * 100:.2f}%     Y {self.s['cal_y'] * 100:.2f}%")
        c.create_text(W / 2, H - 90, text=info, fill="#333", font=("Segoe UI", 11),
                      justify="center")

    # ------------------------------------------------------------ settings
    def open_settings(self, first_run=False):
        SettingsDialog(self, first_run)

    def apply_settings(self):
        save_settings(self.s)
        self.update_scale()
        self.render_page(keep_position=True)


# ==========================================================================
#  Print & Scale side panel
# ==========================================================================
PBG = "#f3f3f3"


class PrintPanel:
    def __init__(self, app):
        self.app = app
        p = app.pr
        f = self.frame = tk.Frame(app.root, bg=PBG, padx=16, pady=12,
                                  highlightthickness=1, highlightbackground="#b8b8b8")
        fn = ("Segoe UI", 10)
        fb = ("Segoe UI", 10, "bold")
        fs = ("Segoe UI", 9)
        self.fn = fn

        self.v_paper = tk.StringVar(value=p["paper"])
        self.v_orient = tk.StringVar(value=p["orient"])
        self.v_cw = tk.StringVar(value=f"{p['custom_w']:g}")
        self.v_ch = tk.StringVar(value=f"{p['custom_h']:g}")
        self.v_margin = tk.StringVar(value=f"{p['margin']:g}")
        self.v_scale = tk.StringVar(value=f"{p['scale'] * 100:.2f}")
        self.v_from = tk.StringVar(value="100")
        self.v_to = tk.StringVar(value="50")
        self.v_poster = tk.BooleanVar(value=p["poster"])
        self.v_overlap = tk.StringVar(value=f"{p['overlap']:g}")
        self.v_all = tk.BooleanVar(value=p["all_pages"])
        self.v_label = tk.BooleanVar(value=p["label"])

        r = 0
        head = tk.Frame(f, bg=PBG)
        head.grid(row=r, column=0, columnspan=4, sticky="we")
        tk.Label(head, text="Print & Scale", font=("Segoe UI", 14, "bold"), bg=PBG).pack(side="left")
        tk.Button(head, text="\u2715", font=fn, relief="flat", bg=PBG, cursor="hand2",
                  command=app.toggle_print_mode).pack(side="right")
        r += 1

        # ---- paper
        r = self.section(r, "PAPER")
        tk.Label(f, text="Size:", font=fn, bg=PBG).grid(row=r, column=0, sticky="w")
        cb = ttk.Combobox(f, textvariable=self.v_paper, values=PRINT_PAPERS, width=10,
                          state="readonly", font=fn)
        cb.grid(row=r, column=1, columnspan=2, sticky="w", pady=2)
        cb.bind("<<ComboboxSelected>>", lambda e: self.apply())
        r += 1
        tk.Label(f, text="Custom (mm):", font=fn, bg=PBG).grid(row=r, column=0, sticky="w")
        cf = tk.Frame(f, bg=PBG)
        cf.grid(row=r, column=1, columnspan=3, sticky="w")
        self.e_cw = self.entry(cf, self.v_cw, 6)
        self.e_cw.pack(side="left")
        tk.Label(cf, text=" x ", font=fn, bg=PBG).pack(side="left")
        self.e_ch = self.entry(cf, self.v_ch, 6)
        self.e_ch.pack(side="left")
        r += 1
        of = tk.Frame(f, bg=PBG)
        of.grid(row=r, column=0, columnspan=4, sticky="w", pady=2)
        for val, txt in (("portrait", "Portrait"), ("landscape", "Landscape")):
            tk.Radiobutton(of, text=txt, value=val, variable=self.v_orient, font=fn, bg=PBG,
                           command=self.apply).pack(side="left", padx=(0, 10))
        r += 1
        tk.Label(f, text="Margin (mm):", font=fn, bg=PBG).grid(row=r, column=0, sticky="w")
        self.entry(f, self.v_margin, 6).grid(row=r, column=1, sticky="w", pady=2)
        r += 1
        tk.Label(f, text="Most printers can't print right to the edge - keep about 5 mm.",
                 font=fs, fg="#666", bg=PBG, wraplength=int(290 * app.ui), justify="left").grid(
            row=r, column=0, columnspan=4, sticky="w")
        r += 1

        # ---- scale
        r = self.section(r, "SCALE")
        tk.Label(f, text="Scale (%):", font=fn, bg=PBG).grid(row=r, column=0, sticky="w")
        self.entry(f, self.v_scale, 8).grid(row=r, column=1, sticky="w", pady=2)
        r += 1
        sf = tk.Frame(f, bg=PBG)
        sf.grid(row=r, column=0, columnspan=4, sticky="w", pady=2)
        tk.Label(sf, text="Drawing 1:", font=fn, bg=PBG).pack(side="left")
        self.entry(sf, self.v_from, 5, apply=False).pack(side="left")
        tk.Label(sf, text="  \u2192  1:", font=fn, bg=PBG).pack(side="left")
        self.entry(sf, self.v_to, 5, apply=False).pack(side="left")
        tk.Button(sf, text="Apply", font=fs, command=self.apply_ratio).pack(side="left", padx=6)
        r += 1
        bf = tk.Frame(f, bg=PBG)
        bf.grid(row=r, column=0, columnspan=4, sticky="w", pady=2)
        tk.Button(bf, text="100%", font=fs, command=lambda: self.set_scale(1.0)).pack(side="left")
        tk.Button(bf, text="Fit page on sheet", font=fs, command=self.fit).pack(side="left", padx=6)
        r += 1

        # ---- position
        r = self.section(r, "PART TO PRINT")
        gf = tk.Frame(f, bg=PBG)
        gf.grid(row=r, column=0, columnspan=2, sticky="w", rowspan=1)
        arrows = [["\u2196", "\u2191", "\u2197"], ["\u2190", "\u25cf", "\u2192"],
                  ["\u2199", "\u2193", "\u2198"]]
        for yy in range(3):
            for xx in range(3):
                tk.Button(gf, text=arrows[yy][xx], width=3, font=fn, cursor="hand2",
                          command=lambda ax=xx / 2, ay=yy / 2: app.anchor_frame(ax, ay)).grid(
                    row=yy, column=xx, padx=1, pady=1)
        tk.Label(f, text="Click a corner/side,\nor drag the blue sheet\nwith the mouse.",
                 font=fs, fg="#555", bg=PBG, justify="left").grid(row=r, column=2, columnspan=2,
                                                                  sticky="w", padx=8)
        r += 1

        # ---- poster / options
        r = self.section(r, "OPTIONS")
        pf = tk.Frame(f, bg=PBG)
        pf.grid(row=r, column=0, columnspan=4, sticky="w")
        tk.Checkbutton(pf, text="Poster: whole drawing on several sheets", variable=self.v_poster,
                       font=fn, bg=PBG, command=self.apply).pack(side="left")
        r += 1
        tk.Label(f, text="Overlap (mm):", font=fn, bg=PBG).grid(row=r, column=0, sticky="w")
        self.entry(f, self.v_overlap, 6).grid(row=r, column=1, sticky="w", pady=2)
        r += 1
        tk.Checkbutton(f, text="All pages (same area on each page)", variable=self.v_all, font=fn,
                       bg=PBG, command=self.apply).grid(row=r, column=0, columnspan=4, sticky="w")
        r += 1
        tk.Checkbutton(f, text="Print file name & scale in the margin", variable=self.v_label,
                       font=fn, bg=PBG, command=self.apply).grid(row=r, column=0, columnspan=4,
                                                                sticky="w")
        r += 1

        self.info = tk.Label(f, font=fs, bg="#e6eefb", fg="#1d3557", justify="left", anchor="w",
                             padx=8, pady=6, wraplength=int(290 * app.ui))
        self.info.grid(row=r, column=0, columnspan=4, sticky="we", pady=(12, 6))
        r += 1

        tk.Button(f, text="Overview / real size  (Z)", font=fn, command=app.toggle_overview).grid(
            row=r, column=0, columnspan=4, sticky="we", pady=2)
        r += 1
        af = tk.Frame(f, bg=PBG)
        af.grid(row=r, column=0, columnspan=4, sticky="we", pady=(8, 0))
        tk.Button(af, text="Save as PDF...", font=fb, command=app.save_print_pdf).pack(
            side="left", expand=True, fill="x", padx=(0, 4))
        tk.Button(af, text="Print...", font=fb, bg=ACCENT, fg="white", activebackground="#2f7fd8",
                  activeforeground="white", command=app.print_now).pack(
            side="left", expand=True, fill="x", padx=(4, 0))
        r += 1
        self.update_custom_state()

    # ---------------------------------------------------------- helpers
    def section(self, r, title):
        tk.Label(self.frame, text=title, font=("Segoe UI", 9, "bold"), fg="#666", bg=PBG).grid(
            row=r, column=0, columnspan=4, sticky="w", pady=(12, 2))
        return r + 1

    def entry(self, parent, var, width, apply=True):
        e = tk.Entry(parent, textvariable=var, width=width, font=self.fn)
        if apply:
            e.bind("<Return>", lambda ev: self.apply())
            e.bind("<FocusOut>", lambda ev: self.apply())
        return e

    def show(self):
        self.frame.place(x=0, y=0, relheight=1.0)
        self.refresh_info()

    def hide(self):
        self.frame.place_forget()

    def update_custom_state(self):
        st = "normal" if self.v_paper.get() == "Custom" else "disabled"
        self.e_cw.config(state=st)
        self.e_ch.config(state=st)

    @staticmethod
    def num(var, default):
        try:
            return float(var.get().replace(",", ".").replace("%", "").strip())
        except ValueError:
            return default

    def apply(self):
        p = self.app.pr
        old_scale = p["scale"]
        new = dict(p)
        new["paper"] = self.v_paper.get()
        new["orient"] = self.v_orient.get()
        new["custom_w"] = max(20.0, self.num(self.v_cw, p["custom_w"]))
        new["custom_h"] = max(20.0, self.num(self.v_ch, p["custom_h"]))
        new["margin"] = max(0.0, min(50.0, self.num(self.v_margin, p["margin"])))
        new["scale"] = max(0.01, min(50.0, self.num(self.v_scale, p["scale"] * 100) / 100.0))
        new["poster"] = bool(self.v_poster.get())
        new["overlap"] = max(0.0, self.num(self.v_overlap, p["overlap"]))
        new["all_pages"] = bool(self.v_all.get())
        new["label"] = bool(self.v_label.get())
        # show the cleaned-up values
        self.v_cw.set(f"{new['custom_w']:g}")
        self.v_ch.set(f"{new['custom_h']:g}")
        self.v_margin.set(f"{new['margin']:g}")
        self.v_scale.set(f"{new['scale'] * 100:.2f}")
        self.v_overlap.set(f"{new['overlap']:g}")
        self.update_custom_state()
        if new == p:
            return
        p.update(new)
        self.app.print_changed(old_scale)

    def set_scale(self, s):
        self.v_scale.set(f"{s * 100:.2f}")
        self.apply()

    def apply_ratio(self):
        a = self.num(self.v_from, 0)
        b = self.num(self.v_to, 0)
        if a <= 0 or b <= 0:
            messagebox.showerror(APP_NAME, "Enter both scales, e.g. 1:100 \u2192 1:50",
                                 parent=self.app.root)
            return
        self.set_scale(a / b)

    def fit(self):
        app = self.app
        if not app.doc:
            return
        fw, fh = app.printable_dims()
        s = min(fw / app.page_w_mm, fh / app.page_h_mm)
        s = math.floor(s * 10000) / 10000
        self.set_scale(s)
        app.anchor_frame(0.5, 0.5)

    def refresh_info(self):
        app = self.app
        if not app.doc or not app.page_w_mm:
            self.info.config(text="Open a PDF to start.")
            return
        pw, ph = app.printed_page_mm()
        paper_w, paper_h = app.paper_dims()
        fw, fh = app.printable_dims()
        tiles, ncols, nrows = app.compute_tiles(pw, ph)
        pages = len(app.doc) if app.pr["all_pages"] else 1
        lines = [f"Drawing printed at {app.pr['scale'] * 100:.2f}%:  {pw:.0f} x {ph:.0f} mm",
                 f"Sheet: {paper_w:.0f} x {paper_h:.0f} mm  (printable {fw:.0f} x {fh:.0f})"]
        if app.pr["poster"]:
            lines.append(f"Poster: {len(tiles)} sheet(s), {nrows} rows x {ncols} columns")
        else:
            fx, fy = app.pr["fx"], app.pr["fy"]
            lines.append(f"Area from the drawing's top-left: {fx:.0f} mm right, {fy:.0f} mm down")
            if pw <= fw + 0.5 and ph <= fh + 0.5:
                lines.append("The whole page fits on the sheet.")
        if pages > 1:
            lines.append(f"Applied to all {pages} pages.")
        self.info.config(text="\n".join(lines))


class SettingsDialog:
    def __init__(self, app, first_run=False):
        self.app = app
        s = app.s
        d = self.d = tk.Toplevel(app.root)
        d.title("Settings - " + APP_NAME)
        d.configure(bg="#f3f3f3", padx=22, pady=18)
        d.resizable(False, False)
        d.transient(app.root)
        d.attributes("-topmost", True)

        f_bold = ("Segoe UI", 11, "bold")
        f = ("Segoe UI", 10)

        row = 0
        if first_run:
            tk.Label(d, text="Your screen size could not be detected automatically.\n"
                             "Please enter it below (you only need to do this once).",
                     fg="#b00", bg="#f3f3f3", font=f, justify="left").grid(
                row=row, column=0, columnspan=4, sticky="w", pady=(0, 10))
            row += 1

        tk.Label(d, text="Screen size", font=f_bold, bg="#f3f3f3").grid(
            row=row, column=0, columnspan=4, sticky="w")
        row += 1
        tk.Label(d, text=f"Resolution of this monitor: {app.mon['w']} x {app.mon['h']} pixels",
                 font=f, bg="#f3f3f3").grid(row=row, column=0, columnspan=4, sticky="w")
        row += 1

        self.mode = tk.StringVar(value=s["mode"] if app.detected else "manual")
        if app.detected:
            w, h, name = app.detected
            det = (f"Auto-detect:  {name or 'monitor'}  -  {w:.0f} x {h:.0f} mm  "
                   f"({math.hypot(w, h) / 25.4:.1f}\")")
            state = "normal"
        else:
            det = "Auto-detect:  not available for this monitor"
            state = "disabled"
        tk.Radiobutton(d, text=det, variable=self.mode, value="auto", font=f, bg="#f3f3f3",
                       state=state, command=self.toggle).grid(
            row=row, column=0, columnspan=4, sticky="w", pady=(8, 0))
        row += 1
        tk.Radiobutton(d, text="Enter manually:", variable=self.mode, value="manual", font=f,
                       bg="#f3f3f3", command=self.toggle).grid(
            row=row, column=0, columnspan=4, sticky="w")
        row += 1

        mw = s["manual_w_mm"] or (app.detected[0] if app.detected else "")
        mh = s["manual_h_mm"] or (app.detected[1] if app.detected else "")
        self.diag = tk.StringVar()
        self.wmm = tk.StringVar(value=f"{float(mw):.1f}" if mw else "")
        self.hmm = tk.StringVar(value=f"{float(mh):.1f}" if mh else "")

        tk.Label(d, text="      Diagonal (inches):", font=f, bg="#f3f3f3").grid(
            row=row, column=0, sticky="w")
        self.e_diag = tk.Entry(d, textvariable=self.diag, width=8, font=f)
        self.e_diag.grid(row=row, column=1, sticky="w")
        self.b_diag = tk.Button(d, text="Calculate width/height", font=f,
                                command=self.from_diagonal)
        self.b_diag.grid(row=row, column=2, columnspan=2, sticky="w", padx=6)
        row += 1
        tk.Label(d, text="      or exact visible area:", font=f, bg="#f3f3f3").grid(
            row=row, column=0, sticky="w", pady=(6, 0))
        row += 1
        tk.Label(d, text="      Width (mm):", font=f, bg="#f3f3f3").grid(row=row, column=0,
                                                                         sticky="w")
        self.e_w = tk.Entry(d, textvariable=self.wmm, width=8, font=f)
        self.e_w.grid(row=row, column=1, sticky="w")
        tk.Label(d, text="Height (mm):", font=f, bg="#f3f3f3").grid(row=row, column=2,
                                                                    sticky="e", padx=6)
        self.e_h = tk.Entry(d, textvariable=self.hmm, width=8, font=f)
        self.e_h.grid(row=row, column=3, sticky="w")
        row += 1
        tk.Label(d, text="      Tip: measuring the lit picture area with a tape measure is the most "
                         "accurate.", font=("Segoe UI", 9), fg="#666", bg="#f3f3f3").grid(
            row=row, column=0, columnspan=4, sticky="w", pady=(2, 0))
        row += 1

        tk.Label(d, text="Fine calibration", font=f_bold, bg="#f3f3f3").grid(
            row=row, column=0, columnspan=4, sticky="w", pady=(16, 0))
        row += 1
        self.cal_lbl = tk.Label(d, font=f, bg="#f3f3f3")
        self.cal_lbl.grid(row=row, column=0, columnspan=2, sticky="w")
        tk.Button(d, text="Reset", font=f, command=self.reset_cal).grid(
            row=row, column=2, sticky="w", padx=6)
        tk.Button(d, text="Calibrate with ruler...", font=f, command=self.calibrate).grid(
            row=row, column=3, sticky="w")
        row += 1
        self.update_cal_label()

        bf = tk.Frame(d, bg="#f3f3f3")
        bf.grid(row=row, column=0, columnspan=4, sticky="e", pady=(20, 0))
        tk.Button(bf, text="Cancel", width=10, font=f, command=d.destroy).pack(side="right")
        tk.Button(bf, text="Save", width=10, font=f, command=self.save).pack(side="right", padx=8)

        self.toggle()
        d.bind("<Return>", lambda e: self.save())
        d.bind("<Escape>", lambda e: d.destroy())
        d.update_idletasks()
        x = app.mon["left"] + (app.mon["w"] - d.winfo_width()) // 2
        y = app.mon["top"] + (app.mon["h"] - d.winfo_height()) // 2
        d.geometry(f"+{x}+{y}")
        d.grab_set()
        d.focus_force()

    def toggle(self):
        st = "normal" if self.mode.get() == "manual" else "disabled"
        for w in (self.e_diag, self.b_diag, self.e_w, self.e_h):
            w.config(state=st)

    def from_diagonal(self):
        try:
            dia = float(self.diag.get().replace(",", "."))
        except ValueError:
            messagebox.showerror(APP_NAME, "Enter the diagonal as a number, e.g. 24 or 15.6",
                                 parent=self.d)
            return
        pw, ph = self.app.mon["w"], self.app.mon["h"]
        k = dia * 25.4 / math.hypot(pw, ph)      # assumes square pixels
        self.wmm.set(f"{pw * k:.1f}")
        self.hmm.set(f"{ph * k:.1f}")

    def update_cal_label(self):
        s = self.app.s
        self.cal_lbl.config(text=f"X {s['cal_x'] * 100:.2f}%    Y {s['cal_y'] * 100:.2f}%")

    def reset_cal(self):
        self.app.s["cal_x"] = self.app.s["cal_y"] = 1.0
        self.update_cal_label()

    def collect(self):
        s = self.app.s
        if self.mode.get() == "manual":
            try:
                if not self.wmm.get().strip() and self.diag.get().strip():
                    self.from_diagonal()
                w = float(self.wmm.get().replace(",", "."))
                h = float(self.hmm.get().replace(",", "."))
                if w < 50 or h < 50 or w > 5000 or h > 5000:
                    raise ValueError
            except ValueError:
                messagebox.showerror(APP_NAME, "Please enter a valid width and height in mm "
                                     "(or a diagonal and press Calculate).", parent=self.d)
                return False
            changed = (s["mode"] != "manual" or s["manual_w_mm"] != w or s["manual_h_mm"] != h)
            s["mode"], s["manual_w_mm"], s["manual_h_mm"] = "manual", w, h
        else:
            changed = s["mode"] != "auto"
            s["mode"] = "auto"
        if changed:   # a new base size makes the old fine-tuning meaningless
            s["cal_x"] = s["cal_y"] = 1.0
        return True

    def save(self):
        if self.collect():
            self.d.destroy()
            self.app.apply_settings()

    def calibrate(self):
        if self.collect():
            self.d.destroy()
            self.app.apply_settings()
            self.app.start_calibration()


def main():
    initial = None
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        initial = sys.argv[1]
    root = tk.Tk()
    TrueSizeApp(root, initial)
    root.mainloop()


if __name__ == "__main__":
    main()
