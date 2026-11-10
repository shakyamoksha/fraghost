from __future__ import annotations

import argparse
import ctypes
import sys
import threading
from pathlib import Path

from . import __version__
from .engine import Engine
from .server import serve
from .settings import load_settings

def _bundle_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent.parent


ROOT = _bundle_root()
DIST = ROOT / "ui" / "dist"
_ICON = Path(__file__).resolve().parent / "assets" / "fraghost.ico"
_INSTALL_MUTEX = None
APP_NAME = "fraghost"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="fraghost", description="fraghost BitTorrent client")
    parser.add_argument("--server", action="store_true", help="Serve the local UI without opening a window")
    parser.add_argument("--dev", action="store_true", help="Open the Vite dev server at http://127.0.0.1:5173")
    parser.add_argument("--port", type=int, default=47371)
    args = parser.parse_args(argv)

    global _INSTALL_MUTEX
    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_NAME)
        _INSTALL_MUTEX = ctypes.windll.kernel32.CreateMutexW(None, False, "fraghostSingleton")

    engine = Engine()
    engine.start()
    httpd, url = serve(engine, port=args.port, dist_dir=DIST)
    print(f"{APP_NAME} {__version__}", flush=True)
    print(f"Local UI  {url}", flush=True)
    if args.dev:
        print("Dev UI    http://127.0.0.1:5173/", flush=True)

    if args.server:
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        finally:
            engine.shutdown()
            httpd.shutdown()
        return

    target = "http://127.0.0.1:5173/" if args.dev else url
    try:
        import webview
    except Exception as exc:
        print(f"Desktop window is unavailable ({exc}).")
        print(f"Open {url} in a browser.")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        finally:
            engine.shutdown()
            httpd.shutdown()
        return

    shell = _Shell(engine)

    class Desktop:
        def pick_torrents(self):
            chosen = webview.windows[0].create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=True,
                file_types=("Torrent files (*.torrent)", "All files (*.*)"),
            )
            return list(chosen) if chosen else []

        def pick_folder(self):
            chosen = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG)
            return _first(chosen)

        def pick_save(self, name="new.torrent"):
            chosen = webview.windows[0].create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename=name or "new.torrent",
                file_types=("Torrent files (*.torrent)", "All files (*.*)"),
            )
            return _first(chosen)

        def window_minimize(self):
            shell.minimize()

        def window_toggle_maximize(self):
            shell.toggle_maximize()

        def window_toggle_fullscreen(self):
            shell.toggle_fullscreen()

        def window_hide(self):
            shell.hide()

        def window_resize(self, edge, dx, dy):
            shell.resize(str(edge or ""), float(dx or 0), float(dy or 0))

        def window_drag(self, screen_x, screen_y, ratio_x, grab_y):
            shell.drag_to(float(screen_x or 0), float(screen_y or 0), float(ratio_x or 0), float(grab_y or 0))

        def notify_update(self, title, body):
            shell.notify(str(title or APP_NAME), str(body or ""))

    window = webview.create_window(
        APP_NAME,
        target,
        js_api=Desktop(),
        width=1280,
        height=840,
        min_size=(640, 480),
        background_color=_window_color(),
        text_select=True,
        frameless=True,
        easy_drag=False,
        confirm_close=False,
    )
    shell.window = window

    def on_shown():
        shell.install_tray()

    def on_closing():
        if shell.quitting or not shell.tray_ready:
            return True
        shell.hide_after_cancel()
        return False

    def on_closed():
        shell.dispose_tray()
        engine.shutdown()
        httpd.shutdown()

    try:
        window.events.shown += on_shown
        window.events.closing += on_closing
        window.events.closed += on_closed
    except Exception:
        pass
    try:
        webview.start(icon=str(_ICON) if _ICON.is_file() else None)
    finally:
        shell.dispose_tray()
        engine.shutdown()
        try:
            httpd.shutdown()
        except Exception:
            pass


def _window_color() -> str:
    theme = str(load_settings().get("theme") or "system")
    if theme == "light":
        return "#f6f6f6"
    if theme == "dark":
        return "#1c1c1c"
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            light, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return "#f6f6f6" if light else "#1c1c1c"
    except OSError:
        return "#1c1c1c"


class _Shell:
    """Frameless window controls and a tray icon so closing the window leaves fraghost running."""

    def __init__(self, engine=None) -> None:
        self.window = None
        self.engine = engine
        self.quitting = False
        self.tray_ready = False
        self._tray = None
        self._kept: list[object] = []

    def _form(self):
        if self.window is None:
            return None
        try:
            from webview.platforms.winforms import BrowserView
        except Exception:
            return None
        return BrowserView.instances.get(self.window.uid)

    def install_tray(self) -> None:
        form = self._form()
        if form is None:
            return
        try:
            from System import Action
            from System.Drawing import Icon
            from System.Windows.Forms import ContextMenuStrip, NotifyIcon, ToolStripMenuItem
        except Exception as exc:
            print(f"Tray icon is unavailable ({exc}).", flush=True)
            return

        def install() -> None:
            from System.Drawing import Size

            if not _ICON.is_file():
                raise FileNotFoundError(_ICON)
            # Icon(path) owns its handle. FromHandle().Clone() does not, and the
            # taskbar then shows a blank button once that handle is destroyed.
            icon = Icon(str(_ICON), Size(256, 256))
            small = Icon(str(_ICON), Size(16, 16))
            _assign_window_icon(form, icon, small)
            from System.Drawing import Font
            from System.Windows.Forms import Padding, ToolStripSeparator

            menu = ContextMenuStrip()
            menu.ShowImageMargin = False
            menu.ShowCheckMargin = False
            menu.Font = Font("Segoe UI", 10.0)
            menu.Padding = Padding(4, 6, 4, 6)
            entries = [
                ("show", "Show fraghost"),
                ("sep", ""),
                ("add", "Add torrent..."),
                ("url", "Add torrent from URL..."),
                ("create", "Create new torrent..."),
                ("sep", ""),
                ("start", "Start all torrents"),
                ("pause", "Pause all torrents"),
                ("stop", "Stop all torrents"),
                ("sep", ""),
            ]
            items = []
            show_item = None
            for kind, label in entries:
                if kind == "sep":
                    menu.Items.Add(ToolStripSeparator())
                    continue
                item = ToolStripMenuItem(label)
                item.Padding = Padding(12, 4, 16, 4)
                item.AutoSize = True
                item.Click += _tray_click(self, kind)
                menu.Items.Add(item)
                items.append(item)
                if kind == "show":
                    show_item = item

            down_menu, down_choices = _speed_menu(self, "down", "Download limit")
            up_menu, up_choices = _speed_menu(self, "up", "Upload limit")
            menu.Items.Add(down_menu)
            menu.Items.Add(up_menu)
            menu.Items.Add(ToolStripSeparator())
            options_item = ToolStripMenuItem("Options...")
            exit_item = ToolStripMenuItem("Exit")
            for extra, kind in ((options_item, "options"), (exit_item, "exit")):
                extra.Padding = Padding(12, 4, 16, 4)
                extra.AutoSize = True
                extra.Click += _tray_click(self, kind)
                menu.Items.Add(extra)
                items.append(extra)
            items.extend([down_menu, up_menu, *(choice[2] for choice in down_choices), *(choice[2] for choice in up_choices)])

            def on_opening(_sender, _args) -> None:
                palette = _tray_palette()
                menu.BackColor = palette["back"]
                menu.ForeColor = palette["fg"]
                if show_item is not None:
                    show_item.Text = "Hide fraghost" if _window_open(self) else "Show fraghost"
                current_down, current_up = (0, 0)
                if self.engine is not None:
                    try:
                        current_down, current_up = self.engine.rate_limits()
                    except Exception:
                        pass
                _mark_speeds(down_choices, current_down)
                _mark_speeds(up_choices, current_up)
                menu.Renderer = _tray_renderer(palette)
                for item in items:
                    item.ForeColor = palette["fg"]
                for dropdown in (down_menu.DropDown, up_menu.DropDown):
                    dropdown.ShowImageMargin = False
                    dropdown.ShowCheckMargin = False
                    dropdown.BackColor = palette["back"]
                    dropdown.ForeColor = palette["fg"]
                    dropdown.Renderer = menu.Renderer
                self._kept.append(menu.Renderer)

            def on_opened(_sender, _args) -> None:
                _round_menu(menu, 12)

            menu.Opening += on_opening
            menu.Opened += on_opened
            on_opening(None, None)
            tray = NotifyIcon()
            tray.Icon = small
            tray.Text = APP_NAME
            tray.Visible = True
            tray.ContextMenuStrip = menu

            def on_double(_sender, _args) -> None:
                try:
                    self.show()
                except Exception as exc:
                    print(f"Could not show the window ({exc}).", flush=True)

            def on_balloon(_sender, _args) -> None:
                try:
                    self.show()
                except Exception as exc:
                    print(f"Could not show the window ({exc}).", flush=True)

            tray.DoubleClick += on_double
            tray.BalloonTipClicked += on_balloon
            form.MinimizeBox = True
            form.ShowInTaskbar = True
            hook = _enable_taskbar_toggle(form)
            _assign_window_icon(form, icon, small)
            self._kept.append(hook)
            self._tray = tray
            self._kept.extend([icon, small, menu, tray, menu.Font, *items])
            self.tray_ready = True

        try:
            form.Invoke(Action(install))
        except Exception as exc:
            print(f"Tray icon is unavailable ({exc}).", flush=True)

    def dispose_tray(self) -> None:
        tray = self._tray
        self._tray = None
        self.tray_ready = False
        if tray is None:
            return
        try:
            tray.Visible = False
            tray.Dispose()
        except Exception:
            pass

    def _run(self, action) -> None:
        form = self._form()
        if form is None:
            return
        from System import Action

        if form.InvokeRequired:
            form.BeginInvoke(Action(action))
        else:
            action()

    def _run_sync(self, action) -> None:
        form = self._form()
        if form is None:
            return
        from System import Action

        if form.InvokeRequired:
            form.Invoke(Action(action))
        else:
            action()

    def notify(self, title: str, body: str) -> None:
        def go() -> None:
            tray = self._tray
            if tray is None:
                return
            from System.Windows.Forms import ToolTipIcon

            tray.BalloonTipTitle = (title or APP_NAME)[:63]
            tray.BalloonTipText = (body or "")[:255]
            tray.BalloonTipIcon = ToolTipIcon.Info
            tray.ShowBalloonTip(8000)

        self._run(go)

    def show(self) -> None:
        def go() -> None:
            from System.Windows.Forms import FormWindowState

            form = self._form()
            if form is None:
                return
            form.ShowInTaskbar = True
            form.Show()
            if form.WindowState == FormWindowState.Minimized:
                form.WindowState = FormWindowState.Normal
            form.Activate()

        self._run(go)

    def hide(self) -> None:
        self._run(lambda: self._form() and self._form().Hide())

    def hide_after_cancel(self) -> None:
        form = self._form()
        if form is None:
            return
        from System import Action

        form.BeginInvoke(Action(lambda: form.Hide()))

    def minimize(self) -> None:
        if self.window is not None:
            self.window.minimize()

    def toggle_maximize(self) -> None:
        def go() -> None:
            from System.Drawing import Rectangle
            from System.Windows.Forms import FormWindowState, Screen

            form = self._form()
            if form is None:
                return
            if getattr(form, "is_fullscreen", False):
                form.toggle_fullscreen()
            screen = Screen.FromControl(form)
            if form.WindowState == FormWindowState.Maximized:
                form.WindowState = FormWindowState.Normal
                return
            # A borderless window otherwise maximizes over the taskbar.
            form.MaximizedBounds = Rectangle(
                int(screen.WorkingArea.X),
                int(screen.WorkingArea.Y),
                int(screen.WorkingArea.Width),
                int(screen.WorkingArea.Height),
            )
            form.WindowState = FormWindowState.Maximized

        self._run(go)

    def toggle_fullscreen(self) -> None:
        def go() -> None:
            from System.Drawing import Rectangle
            from System.Windows.Forms import FormWindowState, Screen

            form = self._form()
            if form is None:
                return
            screen = Screen.FromControl(form)
            if getattr(form, "is_fullscreen", False):
                form.toggle_fullscreen()
                form.MaximizedBounds = Rectangle(
                    int(screen.WorkingArea.X),
                    int(screen.WorkingArea.Y),
                    int(screen.WorkingArea.Width),
                    int(screen.WorkingArea.Height),
                )
                return
            if form.WindowState == FormWindowState.Maximized:
                form.WindowState = FormWindowState.Normal
            form.MaximizedBounds = Rectangle(
                int(screen.Bounds.X),
                int(screen.Bounds.Y),
                int(screen.Bounds.Width),
                int(screen.Bounds.Height),
            )
            form.toggle_fullscreen()

        self._run(go)

    def drag_to(self, screen_x: float, screen_y: float, ratio_x: float, grab_y: float) -> None:
        def go() -> None:
            from System.Drawing import Point
            from System.Windows.Forms import FormWindowState

            form = self._form()
            if form is None:
                return
            if getattr(form, "is_fullscreen", False):
                form.toggle_fullscreen()
            if form.WindowState == FormWindowState.Maximized:
                form.WindowState = FormWindowState.Normal
            scale = float(getattr(form, "_scale", 1) or 1)
            width = max(1, int(form.Width))
            left = int(round(screen_x * scale - max(0.0, min(1.0, ratio_x)) * width))
            top = int(round((screen_y - grab_y) * scale))
            form.Location = Point(left, top)

        self._run_sync(go)

    def resize(self, edge: str, dx: float, dy: float) -> None:
        def go() -> None:
            from System.Drawing import Rectangle
            from System.Windows.Forms import FormWindowState

            form = self._form()
            if form is None or form.WindowState == FormWindowState.Maximized or getattr(form, "is_fullscreen", False):
                return
            scale = float(getattr(form, "_scale", 1) or 1)
            step_x = int(round(dx * scale))
            step_y = int(round(dy * scale))
            minimum = form.MinimumSize
            left, top, width, height = _resize_rect(
                int(form.Left),
                int(form.Top),
                int(form.Width),
                int(form.Height),
                edge,
                step_x,
                step_y,
                int(minimum.Width),
                int(minimum.Height),
            )
            form.Bounds = Rectangle(left, top, width, height)

        self._run(go)

    def quit(self) -> None:
        self.quitting = True
        self.dispose_tray()

        def go() -> None:
            form = self._form()
            if form is not None:
                form.Close()

        self._run(go)


def _resize_rect(
    left: int,
    top: int,
    width: int,
    height: int,
    edge: str,
    step_x: int,
    step_y: int,
    min_width: int,
    min_height: int,
) -> tuple[int, int, int, int]:
    """Move one edge. At the minimum size the opposite edge stays put, so the window does not slide."""
    sides = set(edge)
    right = left + width
    bottom = top + height
    if "w" in sides:
        left = min(left + step_x, right - min_width)
    if "e" in sides:
        right = max(right + step_x, left + min_width)
    if "n" in sides:
        top = min(top + step_y, bottom - min_height)
    if "s" in sides:
        bottom = max(bottom + step_y, top + min_height)
    return left, top, right - left, bottom - top


class _RECT(ctypes.Structure):
    _fields_ = (
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    )


class _MONITORINFO(ctypes.Structure):
    _fields_ = (
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", ctypes.c_ulong),
    )


_WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t,
    ctypes.c_void_p,
    ctypes.c_uint,
    ctypes.c_void_p,
    ctypes.c_void_p,
)


def _assign_window_icon(form, large, small) -> None:
    """Put the icon on the window and on both taskbar sizes. The objects must stay referenced."""
    form.Icon = large
    send = ctypes.windll.user32.SendMessageW
    send.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_void_p]
    send.restype = ctypes.c_void_p
    hwnd = int(form.Handle.ToInt64())
    send(hwnd, 0x0080, 1, int(large.Handle.ToInt64()))
    send(hwnd, 0x0080, 0, int(small.Handle.ToInt64()))


def _enable_taskbar_toggle(form):
    """Keep caption styles so the taskbar can minimize and restore, without drawing the system frame."""
    hwnd = int(form.Handle.ToInt64())
    GWL_STYLE = -16
    GWL_WNDPROC = -4
    WM_NCCALCSIZE = 0x0083
    WS_CAPTION = 0x00C00000
    WS_THICKFRAME = 0x00040000
    WS_MINIMIZEBOX = 0x00020000
    WS_MAXIMIZEBOX = 0x00010000
    WS_SYSMENU = 0x00080000
    user32 = ctypes.windll.user32
    get_long = user32.GetWindowLongPtrW
    set_long = user32.SetWindowLongPtrW
    get_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
    get_long.restype = ctypes.c_void_p
    set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    set_long.restype = ctypes.c_void_p
    call_proc = user32.CallWindowProcW
    call_proc.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    call_proc.restype = ctypes.c_ssize_t
    user32.IsZoomed.argtypes = [ctypes.c_void_p]
    user32.IsZoomed.restype = ctypes.c_int
    user32.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    user32.MonitorFromWindow.restype = ctypes.c_void_p
    user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.GetMonitorInfoW.restype = ctypes.c_int
    user32.SetWindowPos.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]

    previous = get_long(hwnd, GWL_WNDPROC)

    def _wndproc(handle, msg, wparam, lparam):
        if msg == WM_NCCALCSIZE and int(wparam or 0) != 0:
            if user32.IsZoomed(handle):
                monitor = user32.MonitorFromWindow(handle, 2)
                info = _MONITORINFO()
                info.cbSize = ctypes.sizeof(info)
                if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                    rect = ctypes.cast(lparam, ctypes.POINTER(_RECT)).contents
                    rect.left = info.rcWork.left
                    rect.top = info.rcWork.top
                    rect.right = info.rcWork.right
                    rect.bottom = info.rcWork.bottom
            return 0
        return call_proc(previous, handle, msg, wparam, lparam)

    callback = _WNDPROC(_wndproc)
    style = int(get_long(hwnd, GWL_STYLE) or 0) & 0xFFFFFFFF
    set_long(hwnd, GWL_STYLE, style | WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU)
    set_long(hwnd, GWL_WNDPROC, ctypes.cast(callback, ctypes.c_void_p))
    user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0020)
    return callback


def _clr() -> None:
    import clr

    clr.AddReference("System.Drawing")
    clr.AddReference("System.Windows.Forms")


_SPEEDS = (
    (0, "Unlimited"),
    (50, "50 KB/s"),
    (100, "100 KB/s"),
    (250, "250 KB/s"),
    (500, "500 KB/s"),
    (1024, "1 MB/s"),
    (5120, "5 MB/s"),
)


def _speed_menu(shell, direction: str, title: str):
    from System.Windows.Forms import Padding, ToolStripMenuItem, ToolStripSeparator

    parent = ToolStripMenuItem(title)
    parent.Padding = Padding(12, 4, 16, 4)
    parent.AutoSize = True
    choices = []
    for value, label in _SPEEDS:
        item = ToolStripMenuItem(label)
        item.Padding = Padding(12, 4, 16, 4)
        item.AutoSize = True
        item.Click += _tray_click(shell, f"{direction}:{value}")
        parent.DropDownItems.Add(item)
        choices.append((value, label, item))
    parent.DropDownItems.Add(ToolStripSeparator())
    other = ToolStripMenuItem("Other...")
    other.Padding = Padding(12, 4, 16, 4)
    other.AutoSize = True
    other.Click += _tray_click(shell, "limits")
    parent.DropDownItems.Add(other)
    choices.append((-1, "Other...", other))
    return parent, choices


def _mark_speeds(choices, current: int) -> None:
    known = {value for value, _label, _item in choices if value >= 0}
    for value, label, item in choices:
        picked = value == current or (value < 0 and current not in known)
        item.Text = f"✓  {label}" if picked else f"     {label}"


def _tray_click(shell, kind: str):
    def handle(_sender, _args) -> None:
        try:
            _run_tray(shell, kind)
        except Exception as exc:
            print(f"Tray action failed ({exc}).", flush=True)

    return handle


def _window_open(shell) -> bool:
    form = shell._form()
    if form is None:
        return False
    try:
        from System.Windows.Forms import FormWindowState

        return bool(form.Visible) and form.WindowState != FormWindowState.Minimized
    except Exception:
        return False


def _run_tray(shell, kind: str) -> None:
    if kind == "show":
        if _window_open(shell):
            shell.hide()
        else:
            shell.show()
        return
    if kind == "exit":
        shell.quit()
        return
    if kind in ("start", "pause", "stop"):
        manual = {"start": "started", "pause": "paused", "stop": "stopped"}[kind]
        engine = shell.engine
        if engine is not None:
            threading.Thread(target=engine.set_all, args=(manual,), daemon=True).start()
        return
    if kind.startswith(("down:", "up:")):
        direction, _, raw = kind.partition(":")
        engine = shell.engine
        if engine is not None and raw.isdigit():
            threading.Thread(target=engine.set_rate_limit, args=(direction, int(raw)), daemon=True).start()
        return
    if kind not in ("add", "url", "create", "options", "limits"):
        return
    shell.show()
    window = shell.window
    if window is None:
        return

    def tell() -> None:
        try:
            window.evaluate_js(f"window.fraghostTray && window.fraghostTray('{kind}')")
        except Exception as exc:
            print(f"Could not open {kind} ({exc}).", flush=True)

    threading.Thread(target=tell, daemon=True).start()


def _tray_palette() -> dict:
    _clr()
    theme = str(load_settings().get("theme") or "system")
    dark = theme == "dark" or (theme == "system" and _window_color() == "#1c1c1c")
    from System.Drawing import Color

    if dark:
        return {
            "back": Color.FromArgb(255, 36, 36, 36),
            "fg": Color.FromArgb(255, 236, 236, 236),
            "hover": Color.FromArgb(255, 48, 48, 48),
            "border": Color.FromArgb(255, 58, 58, 58),
        }
    return {
        "back": Color.FromArgb(255, 255, 255, 255),
        "fg": Color.FromArgb(255, 26, 26, 26),
        "hover": Color.FromArgb(255, 244, 244, 244),
        "border": Color.FromArgb(255, 226, 226, 226),
    }


def _round_path(x: int, y: int, width: int, height: int, radius: int):
    _clr()
    from System.Drawing.Drawing2D import GraphicsPath

    path = GraphicsPath()
    radius = max(1, min(radius, width // 2, height // 2))
    diameter = radius * 2
    path.AddArc(x, y, diameter, diameter, 180, 90)
    path.AddArc(x + width - diameter, y, diameter, diameter, 270, 90)
    path.AddArc(x + width - diameter, y + height - diameter, diameter, diameter, 0, 90)
    path.AddArc(x, y + height - diameter, diameter, diameter, 90, 90)
    path.CloseFigure()
    return path


def _round_menu(menu, radius: int) -> None:
    _clr()
    from System.Drawing import Region
    from System.Drawing.Drawing2D import GraphicsPath

    width = int(menu.Width)
    height = int(menu.Height)
    if width < radius * 2 or height < radius * 2:
        return
    path = GraphicsPath()
    diameter = radius * 2
    path.AddArc(0, 0, diameter, diameter, 180, 90)
    path.AddArc(width - diameter, 0, diameter, diameter, 270, 90)
    path.AddArc(width - diameter, height - diameter, diameter, diameter, 0, 90)
    path.AddArc(0, height - diameter, diameter, diameter, 90, 90)
    path.CloseFigure()
    menu.Region = Region(path)


def _tray_renderer(palette: dict):
    global _renderer_type
    if _renderer_type is None:
        _renderer_type = _install_tray_renderer()
    return _renderer_type(palette)


_renderer_type = None


def _install_tray_renderer() -> type:
    _clr()
    from System.Drawing import Pen, SolidBrush
    from System.Drawing.Drawing2D import SmoothingMode
    from System.Windows.Forms import ToolStripProfessionalRenderer

    class Renderer(ToolStripProfessionalRenderer):
        def __init__(self, palette: dict) -> None:
            super().__init__()
            self.palette = palette

        def OnRenderToolStripBackground(self, event) -> None:
            event.Graphics.Clear(self.palette["back"])

        def OnRenderToolStripBorder(self, event) -> None:
            graphics = event.Graphics
            graphics.SmoothingMode = SmoothingMode.AntiAlias
            bounds = event.AffectedBounds
            path = _round_path(0, 0, max(1, int(bounds.Width) - 1), max(1, int(bounds.Height) - 1), 12)
            pen = Pen(self.palette["border"])
            graphics.DrawPath(pen, path)
            pen.Dispose()
            path.Dispose()

        def OnRenderMenuItemBackground(self, event) -> None:
            item = event.Item
            if not item.Selected and not item.Pressed:
                return
            graphics = event.Graphics
            graphics.SmoothingMode = SmoothingMode.AntiAlias
            inset = 4
            width = max(1, int(item.Width) - inset * 2)
            height = max(1, int(item.Height) - 2)
            brush = SolidBrush(self.palette["hover"])
            radius = min(8, width // 2, height // 2)
            if radius < 1:
                graphics.FillRectangle(brush, inset, 1, width, height)
            else:
                path = _round_path(inset, 1, width, height, radius)
                graphics.FillPath(brush, path)
                path.Dispose()
            brush.Dispose()

        def OnRenderItemText(self, event) -> None:
            from System.Drawing import RectangleF, SolidBrush, StringAlignment, StringFormat, StringTrimming
            from System.Drawing import StringFormatFlags

            item = event.Item
            graphics = event.Graphics
            bounds = event.TextRectangle
            rect = RectangleF(float(bounds.X), 0.0, float(max(1, bounds.Width)), float(item.Height))
            fmt = StringFormat()
            fmt.Alignment = StringAlignment.Near
            fmt.LineAlignment = StringAlignment.Center
            fmt.Trimming = StringTrimming.EllipsisCharacter
            fmt.FormatFlags = StringFormatFlags.NoWrap
            brush = SolidBrush(self.palette["fg"])
            graphics.DrawString(str(item.Text), item.Font, brush, rect, fmt)
            brush.Dispose()
            fmt.Dispose()

        def OnRenderSeparator(self, event) -> None:
            pen = Pen(self.palette["border"])
            y = int(event.Item.Height) // 2
            event.Graphics.DrawLine(pen, 12, y, int(event.Item.Width) - 12, y)
            pen.Dispose()

        def OnRenderImageMargin(self, event) -> None:
            return

    return Renderer


def _first(result) -> str:
    if not result:
        return ""
    if isinstance(result, (list, tuple)):
        return str(result[0] or "")
    return str(result)


if __name__ == "__main__":
    main()
