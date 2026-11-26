"""
DesktopHUD - 3x Supersampled 60 FPS Native Layered Floating Pill for HushFlow
- Native Windows 32-bit ARGB Layered Window (UpdateLayeredWindow)
- 3x supersampled RGBA rendering downsampled via LANCZOS for razor-sharp anti-aliased curves & text
- Per-Monitor V2 DPI awareness (zero scaling blur on 125%/150% displays)
- HWND_TOPMOST Z-order enforcement + SPI_GETWORKAREA screen clamping
- Smooth expand on hover, real-time 5-bar waveform modulation, draggable dock position
"""

import os
import time
import math
import threading
import ctypes
from ctypes import wintypes
import win32gui
import win32con
import win32api
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app_paths import load_config, save_config

# Set Per-Monitor DPI Awareness before creating any windows
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

RENDER_SCALE = 3


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_byte),
        ("BlendFlags", ctypes.c_byte),
        ("SourceConstantAlpha", ctypes.c_byte),
        ("AlphaFormat", ctypes.c_byte),
    ]


def get_screen_work_area():
    """Retrieve true desktop work area excluding Windows taskbars and docked appbars."""
    try:
        rect = wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(win32con.SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
        return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        w = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
        h = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)
        return 0, 0, w, h


class DesktopHUD:
    def __init__(self, config_path=None, visible=True):
        self.config_path = config_path
        self.visible = visible
        self.state = "idle"  # idle, recording, transcribing, done
        self.custom_text = None
        self.running = True
        self.is_hovered = False
        self.is_dragging = False
        self.drag_offset_y = 0
        self._request_recreate = False

        # Base dimensions (HiDPI native)
        self.collapsed_w = 46
        self.expanded_w = 236
        self.current_w = float(self.collapsed_w)
        self.target_w = float(self.collapsed_w)
        self.h = 42
        self.x = 4
        self.y = self._load_dock_y()

        self.anim_tick = 0
        self.done_timestamp = 0
        self.last_topmost_reassert = 0
        self.hwnd = None
        self.ready_event = threading.Event()
        self.lock = threading.Lock()

        # Real-time reactive vocal modulation
        self.audio_level = 0.0
        self.smooth_audio_level = 0.0

        # Fonts rendered at RENDER_SCALE (3x) so text downscales crisply
        try:
            self.font_main = ImageFont.truetype("segoeuib.ttf", 13 * RENDER_SCALE)
            self.font_badge = ImageFont.truetype("segoeuib.ttf", 10 * RENDER_SCALE)
        except Exception:
            try:
                self.font_main = ImageFont.truetype("segoeui.ttf", 13 * RENDER_SCALE)
                self.font_badge = ImageFont.truetype("segoeui.ttf", 10 * RENDER_SCALE)
            except Exception:
                self.font_main = self.font_badge = ImageFont.load_default()

        # Start native window thread
        self.thread = threading.Thread(target=self._window_thread, daemon=True)
        self.thread.start()
        self.ready_event.wait(timeout=2.0)

    def _get_safe_y(self, val):
        """Clamp Y strictly inside the visible desktop work area."""
        _, top, _, bottom = get_screen_work_area()
        min_y = top + 10
        max_y = max(min_y, bottom - self.h - 10)
        if val is None or val > max_y or val < min_y:
            return max(min_y, min(max_y, top + int((bottom - top) * 0.35)))
        return val

    def _load_dock_y(self):
        cfg = load_config()
        val = int(cfg.get("dock_y", 360))
        return self._get_safe_y(val)

    def _save_dock_y(self):
        self.y = self._get_safe_y(self.y)
        cfg = load_config()
        cfg["dock_y"] = int(self.y)
        save_config(cfg)

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == win32con.WM_LBUTTONDOWN:
            self.is_dragging = True
            cursor_y = win32api.GetCursorPos()[1]
            self.drag_offset_y = cursor_y - self.y
            win32gui.SetCapture(hwnd)
            return 0

        elif msg == win32con.WM_MOUSEMOVE:
            if self.is_dragging:
                cursor_y = win32api.GetCursorPos()[1]
                self.y = self._get_safe_y(cursor_y - self.drag_offset_y)
            return 0

        elif msg == win32con.WM_LBUTTONUP:
            if self.is_dragging:
                self.is_dragging = False
                win32gui.ReleaseCapture()
                self._save_dock_y()
            return 0

        elif msg == win32con.WM_CLOSE:
            return 0

        elif msg in (win32con.WM_DISPLAYCHANGE, 0x02E0):  # WM_DPICHANGED
            self.y = self._get_safe_y(self.y)
            return 0

        elif msg == win32con.WM_DESTROY:
            win32gui.PostQuitMessage(0)
            return 0

        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def _create_or_restore_window(self):
        """Create or restore the layered HWND on the GUI thread."""
        with self.lock:
            if self.hwnd and win32gui.IsWindow(self.hwnd):
                return

            wc = win32gui.WNDCLASS()
            wc.lpfnWndProc = self._wnd_proc
            wc.lpszClassName = "HushFlow_Layered_HUD_V2"
            wc.hInstance = win32api.GetModuleHandle(None)
            wc.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
            try:
                win32gui.RegisterClass(wc)
            except Exception:
                pass

            ex_style = (
                win32con.WS_EX_LAYERED
                | win32con.WS_EX_TOPMOST
                | win32con.WS_EX_TOOLWINDOW
                | win32con.WS_EX_NOACTIVATE
            )
            style = win32con.WS_POPUP

            self.hwnd = win32gui.CreateWindowEx(
                ex_style,
                "HushFlow_Layered_HUD_V2",
                "HushFlow HUD",
                style,
                self.x,
                self.y,
                self.expanded_w,
                self.h,
                0,
                0,
                wc.hInstance,
                None,
            )

            try:
                self._render_and_update()
            except Exception:
                pass

            if self.visible:
                win32gui.ShowWindow(self.hwnd, win32con.SW_SHOWNOACTIVATE)
                win32gui.SetWindowPos(
                    self.hwnd,
                    win32con.HWND_TOPMOST,
                    self.x,
                    self.y,
                    int(self.current_w),
                    self.h,
                    win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
                )

    def _window_thread(self):
        self._create_or_restore_window()
        self.ready_event.set()

        last_frame_time = time.time()
        while self.running:
            try:
                now = time.time()
                dt = now - last_frame_time
                if dt >= 0.016:  # ~60 FPS
                    last_frame_time = now
                    self.anim_tick += 1

                    if self.state == "done" and self.done_timestamp > 0:
                        if now - self.done_timestamp > 1.35:
                            self.state = "idle"
                            self.custom_text = None
                            self.done_timestamp = 0

                    self.smooth_audio_level += (self.audio_level - self.smooth_audio_level) * 0.35
                    self.audio_level *= 0.85

                    if not self.is_dragging:
                        try:
                            cursor_x, cursor_y = win32api.GetCursorPos()
                            self.is_hovered = (
                                self.x <= cursor_x <= (self.x + int(self.current_w) + 4)
                                and self.y <= cursor_y <= (self.y + self.h)
                            )
                        except Exception:
                            pass
                    else:
                        self.is_hovered = True

                    should_expand = self.is_hovered or (self.state in ("recording", "transcribing", "done"))
                    self.target_w = float(self.expanded_w if should_expand else self.collapsed_w)

                    self.current_w += (self.target_w - self.current_w) * 0.28
                    if abs(self.current_w - self.target_w) < 0.5:
                        self.current_w = self.target_w

                    if self.visible:
                        if self._request_recreate or not self.hwnd or not win32gui.IsWindow(self.hwnd):
                            self._request_recreate = False
                            self._create_or_restore_window()

                        if self.hwnd and win32gui.IsWindow(self.hwnd):
                            try:
                                self._render_and_update()
                                w_current = max(self.collapsed_w, int(round(self.current_w)))

                                if now - self.last_topmost_reassert > 0.3:
                                    self.last_topmost_reassert = now
                                    win32gui.SetWindowPos(
                                        self.hwnd,
                                        win32con.HWND_TOPMOST,
                                        self.x,
                                        self.y,
                                        w_current,
                                        self.h,
                                        win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
                                    )
                                else:
                                    win32gui.SetWindowPos(
                                        self.hwnd,
                                        0,
                                        self.x,
                                        self.y,
                                        w_current,
                                        self.h,
                                        win32con.SWP_NOACTIVATE
                                        | win32con.SWP_NOMOVE
                                        | win32con.SWP_NOZORDER
                                        | win32con.SWP_NOREDRAW,
                                    )
                            except Exception:
                                pass

                try:
                    win32gui.PumpWaitingMessages()
                except Exception:
                    pass
                time.sleep(0.005)

            except Exception:
                time.sleep(0.02)

        if self.hwnd:
            try:
                win32gui.DestroyWindow(self.hwnd)
            except Exception:
                pass

    def _draw_mini_wave_emblem(self, d: ImageDraw.ImageDraw, cx: float, cy: float, s: int, color_main, color_accent):
        """Draw crisp 5-bar HushFlow icon centered at (cx, cy) on scaled canvas."""
        bar_w = 2.1 * s
        gap = 1.8 * s
        heights = [5.0 * s, 10.0 * s, 13.0 * s, 8.0 * s, 4.5 * s]
        total_w = len(heights) * bar_w + (len(heights) - 1) * gap
        sx = cx - total_w / 2
        for i, bh in enumerate(heights):
            bx0 = sx + i * (bar_w + gap)
            by0 = cy - bh / 2
            by1 = cy + bh / 2
            col = color_accent if i == 3 else color_main
            d.rounded_rectangle((bx0, by0, bx0 + bar_w, by1), radius=bar_w / 2, fill=col)

    def _render_and_update(self):
        if not self.hwnd or not win32gui.IsWindow(self.hwnd):
            return

        w = max(self.collapsed_w, int(round(self.current_w)))
        h = self.h
        s = RENDER_SCALE
        sw, sh = w * s, h * s

        hi = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        d = ImageDraw.Draw(hi)

        # Diffused multi-pass outer shadow
        for offset in range(1, 3):
            ox = offset * s
            d.rounded_rectangle(
                (ox, ox, sw - ox, sh - ox),
                radius=19 * s,
                fill=None,
                outline=(0, 0, 0, int(42 / offset)),
                width=s,
            )

        # Smooth executive dark obsidian pill with subtle slate hairline border
        pad = 2 * s
        bg_color = (16, 20, 30, 248)
        if self.state == "recording":
            border_color = (239, 68, 68, 220)
        elif self.state == "transcribing":
            border_color = (245, 158, 11, 210)
        elif self.state == "done":
            border_color = (52, 211, 153, 220)
        else:
            border_color = (44, 54, 78, 235)

        d.rounded_rectangle((pad, pad, sw - pad, sh - pad), radius=18 * s, fill=bg_color, outline=border_color, width=s)

        cx = 23 * s
        cy = (h / 2.0) * s

        if self.state == "recording":
            pulse_rad = (10.5 + 2.2 * math.sin(self.anim_tick * 0.16)) * s
            glow_alpha = int(110 + 70 * math.sin(self.anim_tick * 0.16))
            d.ellipse(
                (cx - pulse_rad, cy - pulse_rad, cx + pulse_rad, cy + pulse_rad),
                outline=(239, 68, 68, glow_alpha),
                width=int(1.8 * s),
            )
            r_dot = 4.5 * s
            d.ellipse((cx - r_dot, cy - r_dot, cx + r_dot, cy + r_dot), fill=(239, 68, 68, 255))

        elif self.state == "transcribing":
            r_ring = 9.5 * s
            d.ellipse((cx - r_ring, cy - r_ring, cx + r_ring, cy + r_ring), outline=(60, 48, 24, 210), width=int(1.8 * s))
            spin_rad = self.anim_tick * 0.18
            for dot_i in range(3):
                da = spin_rad + dot_i * 0.48
                dx = cx + 7.5 * s * math.cos(da)
                dy = cy + 7.5 * s * math.sin(da)
                dr = 2.0 * s
                d.ellipse((dx - dr, dy - dr, dx + dr, dy + dr), fill=(245, 158, 11, 255 - dot_i * 65))

        elif self.state == "done":
            r_ok = 9.5 * s
            d.ellipse((cx - r_ok, cy - r_ok, cx + r_ok, cy + r_ok), fill=(16, 185, 129, 255))
            d.line(
                (cx - 4 * s, cy, cx - 1 * s, cy + 3 * s),
                fill=(10, 14, 20, 255),
                width=int(2.2 * s),
            )
            d.line(
                (cx - 1 * s, cy + 3 * s, cx + 4.5 * s, cy - 3.5 * s),
                fill=(10, 14, 20, 255),
                width=int(2.2 * s),
            )

        else:
            # Crisp mini HushFlow soundwave mark (zero bullseye rings)
            self._draw_mini_wave_emblem(
                d, cx, cy, s,
                color_main=(241, 245, 249, 255),
                color_accent=(52, 211, 153, 255),
            )

        if w > 105:
            text_x = 46 * s
            if self.state == "recording":
                label = self.custom_text or "Listening..."
                d.text((text_x, 12 * s), label, fill=(248, 250, 252, 255), font=self.font_main)
                wave_start_x = (w - 60) * s
                for bi in range(5):
                    phase = math.sin(self.anim_tick * 0.22 + bi * 0.85)
                    energy = max(0.14, min(1.0, self.smooth_audio_level * (0.65 + 0.35 * abs(phase))))
                    bar_h = (2.8 + 10.5 * energy) * s
                    bx = wave_start_x + bi * 7 * s
                    d.rounded_rectangle(
                        (bx, cy - bar_h, bx + 3.0 * s, cy + bar_h),
                        radius=int(1.5 * s),
                        fill=(239, 68, 68, 245),
                    )

            elif self.state == "transcribing":
                d.text((text_x, 12 * s), self.custom_text or "Transcribing...", fill=(253, 230, 138, 255), font=self.font_main)
                dot_start_x = (w - 48) * s
                for di in range(3):
                    dphase = math.sin(self.anim_tick * 0.25 - di * 0.8)
                    dalpha = int(120 + 135 * max(0, dphase))
                    drad = (2.6 + 1.2 * max(0, dphase)) * s
                    dx = dot_start_x + di * 11 * s
                    d.ellipse((dx - drad, cy - drad, dx + drad, cy + drad), fill=(245, 158, 11, dalpha))

            elif self.state == "done":
                d.text((text_x, 12 * s), self.custom_text or "Done", fill=(167, 243, 208, 255), font=self.font_main)

            else:
                d.text((text_x, 12 * s), "HushFlow", fill=(241, 245, 249, 255), font=self.font_main)
                kw, kh = 76 * s, 21 * s
                kx, ky = (w * s) - kw - 12 * s, (sh - kh) // 2
                d.rounded_rectangle(
                    (kx, ky, kx + kw, ky + kh),
                    radius=6 * s,
                    fill=(26, 33, 48, 255),
                    outline=(48, 60, 86, 255),
                    width=s,
                )
                d.text((kx + 11 * s, ky + 4 * s), "Ctrl + Alt", fill=(203, 213, 225, 255), font=self.font_badge)

        # Downsample 3x canvas via LANCZOS to exact target dimensions
        img = hi.resize((w, h), Image.Resampling.LANCZOS)

        arr = np.array(img, dtype=np.uint8)
        r = arr[:, :, 0].astype(np.uint32)
        g = arr[:, :, 1].astype(np.uint32)
        b = arr[:, :, 2].astype(np.uint32)
        a = arr[:, :, 3].astype(np.uint32)

        b_pre = ((b * a) // 255).astype(np.uint8)
        g_pre = ((g * a) // 255).astype(np.uint8)
        r_pre = ((r * a) // 255).astype(np.uint8)
        a_pre = a.astype(np.uint8)

        bgra = np.stack([b_pre, g_pre, r_pre, a_pre], axis=2)
        raw_bytes = bgra.tobytes()

        hdc_screen = win32gui.GetDC(0)
        if not hdc_screen:
            return

        hdc_mem = win32gui.CreateCompatibleDC(hdc_screen)
        if not hdc_mem:
            win32gui.ReleaseDC(0, hdc_screen)
            return

        hbmp = None
        old_bmp = None
        try:
            bmi = BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.biWidth = w
            bmi.biHeight = -h
            bmi.biPlanes = 1
            bmi.biBitCount = 32
            bmi.biCompression = 0

            p_bits = ctypes.c_void_p()
            hbmp = ctypes.windll.gdi32.CreateDIBSection(
                hdc_mem, ctypes.byref(bmi), 0, ctypes.byref(p_bits), None, 0
            )

            if not hbmp or not p_bits.value:
                return

            ctypes.memmove(p_bits, raw_bytes, len(raw_bytes))
            old_bmp = win32gui.SelectObject(hdc_mem, hbmp)

            blend = BLENDFUNCTION()
            blend.BlendOp = 0
            blend.BlendFlags = 0
            blend.SourceConstantAlpha = 255
            blend.AlphaFormat = 1  # AC_SRC_ALPHA

            pt_dst = wintypes.POINT(self.x, self.y)
            size_dst = wintypes.SIZE(w, h)
            pt_src = wintypes.POINT(0, 0)

            ctypes.windll.user32.UpdateLayeredWindow(
                self.hwnd,
                hdc_screen,
                ctypes.byref(pt_dst),
                ctypes.byref(size_dst),
                hdc_mem,
                ctypes.byref(pt_src),
                0,
                ctypes.byref(blend),
                2,  # ULW_ALPHA
            )
        finally:
            if old_bmp:
                try:
                    win32gui.SelectObject(hdc_mem, old_bmp)
                except Exception:
                    pass
            if hbmp:
                try:
                    win32gui.DeleteObject(hbmp)
                except Exception:
                    pass
            if hdc_mem:
                try:
                    win32gui.DeleteDC(hdc_mem)
                except Exception:
                    pass
            if hdc_screen:
                try:
                    win32gui.ReleaseDC(0, hdc_screen)
                except Exception:
                    pass

    def ensure_alive(self):
        """Active watchdog called by main loop to keep window & GUI thread healthy."""
        if not self.running:
            return
        if not self.thread or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._window_thread, daemon=True)
            self.thread.start()
        if self.visible:
            if not self.hwnd or not win32gui.IsWindow(self.hwnd):
                self._request_recreate = True
            elif win32gui.IsWindow(self.hwnd):
                try:
                    win32gui.SetWindowPos(
                        self.hwnd,
                        win32con.HWND_TOPMOST,
                        self.x,
                        self.y,
                        int(self.current_w),
                        self.h,
                        win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
                    )
                except Exception:
                    pass

    def set_audio_level(self, level: float):
        """Update live audio level (0.0 to 1.0) for reactive equalizer."""
        self.audio_level = max(0.0, min(1.0, float(level)))

    def set_state(self, state, text=None):
        self.state = state
        self.custom_text = text
        if state == "done":
            self.done_timestamp = time.time()
        if self.visible and self.hwnd and win32gui.IsWindow(self.hwnd):
            try:
                win32gui.SetWindowPos(
                    self.hwnd,
                    win32con.HWND_TOPMOST,
                    self.x,
                    self.y,
                    int(self.current_w),
                    self.h,
                    win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
                )
            except Exception:
                pass

    def set_visible(self, visible):
        self.visible = visible
        if self.hwnd and win32gui.IsWindow(self.hwnd):
            cmd = win32con.SW_SHOWNOACTIVATE if visible else win32con.SW_HIDE
            win32gui.ShowWindow(self.hwnd, cmd)
            if visible:
                win32gui.SetWindowPos(
                    self.hwnd,
                    win32con.HWND_TOPMOST,
                    self.x,
                    self.y,
                    int(self.current_w),
                    self.h,
                    win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
                )

    def close(self):
        self.running = False
        if self.hwnd and win32gui.IsWindow(self.hwnd):
            try:
                win32gui.DestroyWindow(self.hwnd)
            except Exception:
                pass
