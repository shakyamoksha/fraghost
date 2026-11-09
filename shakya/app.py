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

    shell = _Shell()

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

    def __init__(self) -> None:
        self.window = None
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
            menu = ContextMenuStrip()
            show_item = ToolStripMenuItem("Show fraghost")
            quit_item = ToolStripMenuItem("Quit")

            def on_show(_sender, _args) -> None:
                try:
                    self.show()
                except Exception as exc:
                    print(f"Could not show the window ({exc}).", flush=True)

            def on_quit(_sender, _args) -> None:
                try:
                    self.quit()
                except Exception as exc:
                    print(f"Could not quit ({exc}).", flush=True)

            show_item.Click += on_show
            quit_item.Click += on_quit
            menu.Items.Add(show_item)
            menu.Items.Add(quit_item)
            menu.ShowImageMargin = False
            menu.ShowCheckMargin = False
            from System.Drawing import Font
            from System.Windows.Forms import Padding

            menu.Font = Font("Segoe UI", 10.0)
            menu.Padding = Padding(6)
            for item in (show_item, quit_item):
                item.Padding = Padding(10, 6, 10, 6)
                item.AutoSize = True

            def on_opening(_sender, _args) -> None:
                palette = _tray_palette()
                menu.BackColor = palette["back"]
                menu.ForeColor = palette["fg"]
                show_item.ForeColor = palette["fg"]
                quit_item.ForeColor = palette["fg"]
                menu.Renderer = _tray_renderer(palette)
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
            self._kept.extend([icon, small, menu, show_item, quit_item, tray, menu.Font])
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


class _NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = (("rgrc", _RECT * 3), ("lppos", ctypes.c_void_p))


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
    _clr()
    from System import IntPtr
    from System.Windows.Forms import FormWindowState, NativeWindow, Screen

    hwnd = int(form.Handle.ToInt64())
    GWL_STYLE = -16
    WS_CAPTION = 0x00C00000
    WS_THICKFRAME = 0x00040000
    WS_MINIMIZEBOX = 0x00020000
    WS_MAXIMIZEBOX = 0x00010000
    WS_SYSMENU = 0x00080000
    user32 = ctypes.windll.user32
    try:
        get_long = user32.GetWindowLongPtrW
        set_long = user32.SetWindowLongPtrW
    except AttributeError:
        get_long = user32.GetWindowLongW
        set_long = user32.SetWindowLongW
    style = int(get_long(hwnd, GWL_STYLE)) & 0xFFFFFFFF
    set_long(hwnd, GWL_STYLE, style | WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU)

    class Hook(NativeWindow):
        def WndProc(self, message) -> None:
            if message.Msg == 0x0083 and int(message.WParam.ToInt32()) != 0:
                if form.WindowState == FormWindowState.Maximized:
                    params = ctypes.cast(int(message.LParam.ToInt64()), ctypes.POINTER(_NCCALCSIZE_PARAMS)).contents
                    work = Screen.FromHandle(form.Handle).WorkingArea
                    params.rgrc[0].left = int(work.X)
                    params.rgrc[0].top = int(work.Y)
                    params.rgrc[0].right = int(work.X + work.Width)
                    params.rgrc[0].bottom = int(work.Y + work.Height)
                message.Result = IntPtr.Zero
                return
            NativeWindow.WndProc(self, message)

    hook = Hook()
    hook.AssignHandle(form.Handle)
    user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0020)
    return hook


def _clr() -> None:
    import clr

    clr.AddReference("System.Drawing")
    clr.AddReference("System.Windows.Forms")


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
    from System.Drawing.Drawing2D import GraphicsPath, SmoothingMode
    from System.Windows.Forms import ToolStripProfessionalRenderer

    class Renderer(ToolStripProfessionalRenderer):
        def __init__(self, palette: dict) -> None:
            super().__init__()
            self.palette = palette

        def OnRenderToolStripBackground(self, event) -> None:
            event.Graphics.Clear(self.palette["back"])

        def OnRenderToolStripBorder(self, event) -> None:
            bounds = event.AffectedBounds
            pen = Pen(self.palette["border"])
            event.Graphics.DrawRectangle(pen, 0, 0, int(bounds.Width) - 1, int(bounds.Height) - 1)
            pen.Dispose()

        def OnRenderMenuItemBackground(self, event) -> None:
            item = event.Item
            if not item.Selected and not item.Pressed:
                return
            graphics = event.Graphics
            graphics.SmoothingMode = SmoothingMode.AntiAlias
            inset = 4
            width = max(1, int(item.Width) - inset * 2)
            height = max(1, int(item.Height) - 2)
            radius = min(8, height // 2)
            path = GraphicsPath()
            diameter = radius * 2
            x = inset
            y = 1
            path.AddArc(x, y, diameter, diameter, 180, 90)
            path.AddArc(x + width - diameter, y, diameter, diameter, 270, 90)
            path.AddArc(x + width - diameter, y + height - diameter, diameter, diameter, 0, 90)
            path.AddArc(x, y + height - diameter, diameter, diameter, 90, 90)
            path.CloseFigure()
            brush = SolidBrush(self.palette["hover"])
            graphics.FillPath(brush, path)
            brush.Dispose()
            path.Dispose()

        def OnRenderItemText(self, event) -> None:
            event.TextColor = self.palette["fg"]
            ToolStripProfessionalRenderer.OnRenderItemText(self, event)

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
