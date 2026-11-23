"""
DesktopHUD - High-Resolution 60 FPS Native Layered Floating Widget for WhisperFlow
Replicates Wispr Flow's left-edge docked pill:
- Native Windows 32-bit ARGB Layered Window (UpdateLayeredWindow)
- Per-Monitor V2 DPI awareness (zero scaling blur on 125%/150% laptop screens)
- Topmost window positioning (HWND_TOPMOST) with active Z-order enforcement
- Dynamic Screen Work Area Clamping (SPI_GETWORKAREA): Never slips under taskbar or off-screen
- Anti-Alt-Tab Resistance: Retains topmost priority across all app/desktop switches
- Self-Healing Watchdog: Automatically recreates or restores window if ever closed/hidden
- Thread-Safe Architecture: Dedicated GUI thread handles all window creation and message dispatch
- 60 FPS silky smooth pulse, waveform, and spinner animations
- Vertically draggable along screen edge with persistent position
"""

import os
import sys
import time
import math
import json
import threading
import ctypes
from ctypes import wintypes
import win32gui
import win32con
import win32api
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# Set Per-Monitor DPI Awareness before creating any windows
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

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
        self.expanded_w = 230
        self.current_w = float(self.collapsed_w)
        self.target_w = float(self.collapsed_w)
        self.h = 44
        self.x = 0
        self.y = self._load_dock_y()

        self.anim_tick = 0
        self.done_timestamp = 0
        self.last_topmost_reassert = 0
        self.hwnd = None
        self.ready_event = threading.Event()
        self.lock = threading.Lock()

        try:
            self.font_main = ImageFont.truetype("segoeuib.ttf", 13)
            self.font_badge = ImageFont.truetype("segoeuib.ttf", 10)
        except Exception:
            self.font_main = self.font_badge = ImageFont.load_default()

        # Start native window thread
        self.thread = threading.Thread(target=self._window_thread, daemon=True)
        self.thread.start()
        self.ready_event.wait(timeout=2.0)

    def _get_safe_y(self, val):
        """Clamp Y strictly inside the visible desktop work area (never off-screen or under taskbar)."""
        _, top, _, bottom = get_screen_work_area()
        min_y = top + 10
        max_y = max(min_y, bottom - self.h - 10)
        if val is None or val > max_y or val < min_y:
            # Safe default: ~35% down the screen, comfortably above the taskbar
            return max(min_y, min(max_y, top + int((bottom - top) * 0.35)))
        return val

    def _load_dock_y(self):
        val = 360
        if self.config_path and os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    if "dock_y" in cfg:
                        val = int(cfg["dock_y"])
            except Exception:
                pass
        return self._get_safe_y(val)

    def _save_dock_y(self):
        self.y = self._get_safe_y(self.y)
        if self.config_path and os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r+", encoding="utf-8") as f:
                    cfg = json.load(f)
                    cfg["dock_y"] = int(self.y)
                    f.seek(0)
                    f.truncate()
                    json.dump(cfg, f, indent=2)
            except Exception:
                pass

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
                new_y = cursor_y - self.drag_offset_y
                self.y = self._get_safe_y(new_y)
            return 0

        elif msg == win32con.WM_LBUTTONUP:
            if self.is_dragging:
                self.is_dragging = False
                win32gui.ReleaseCapture()
                self._save_dock_y()
            return 0

        elif msg == win32con.WM_CLOSE:
            # Block WM_CLOSE so Alt+F4 or window managers cannot close the HUD!
            return 0

        elif msg in (win32con.WM_DISPLAYCHANGE, 0x02E0):  # 0x02E0 = WM_DPICHANGED
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
            wc.lpszClassName = "WhisperFlow_Pro_HUD_Class_V2"
            wc.hInstance = win32api.GetModuleHandle(None)
            wc.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
            try:
                win32gui.RegisterClass(wc)
            except Exception:
                pass

            ex_style = (win32con.WS_EX_LAYERED | 
                        win32con.WS_EX_TOPMOST | 
                        win32con.WS_EX_TOOLWINDOW | 
                        win32con.WS_EX_NOACTIVATE)
            style = win32con.WS_POPUP

            self.hwnd = win32gui.CreateWindowEx(
                ex_style,
                "WhisperFlow_Pro_HUD_Class_V2",
                "WhisperFlow HUD",
                style,
                self.x, self.y, self.expanded_w, self.h,
                0, 0, wc.hInstance, None
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
                    self.x, self.y, int(self.current_w), self.h,
                    win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW
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
                        if now - self.done_timestamp > 1.2:
                            self.state = "idle"
                            self.custom_text = None
                            self.done_timestamp = 0

                    # Real-time cursor polling for instant hover expand & collapse
                    if not self.is_dragging:
                        try:
                            cursor_x, cursor_y = win32api.GetCursorPos()
                            self.is_hovered = (
                                self.x <= cursor_x <= (self.x + int(self.current_w) + 2) and
                                self.y <= cursor_y <= (self.y + self.h)
                            )
                        except Exception:
                            pass
                    else:
                        self.is_hovered = True

                    should_expand = self.is_hovered or (self.state in ["recording", "transcribing", "done"])
                    self.target_w = float(self.expanded_w if should_expand else self.collapsed_w)

                    self.current_w += (self.target_w - self.current_w) * 0.28
                    if abs(self.current_w - self.target_w) < 0.5:
                        self.current_w = self.target_w

                    if self.visible:
                        # Auto-heal: If window was flagged for recreate or destroyed, restore on GUI thread!
                        if self._request_recreate or not self.hwnd or not win32gui.IsWindow(self.hwnd):
                            self._request_recreate = False
                            self._create_or_restore_window()

                        if self.hwnd and win32gui.IsWindow(self.hwnd):
                            try:
                                self._render_and_update()
                                w_current = max(self.collapsed_w, int(round(self.current_w)))

                                # Re-assert HWND_TOPMOST periodically so Alt+Tab, Chrome, Claude, or CLI
                                # never push the HUD behind active windows!
                                reassert_topmost = (now - self.last_topmost_reassert > 0.3)
                                if reassert_topmost:
                                    self.last_topmost_reassert = now
                                    win32gui.SetWindowPos(
                                        self.hwnd,
                                        win32con.HWND_TOPMOST,
                                        self.x, self.y, w_current, self.h,
                                        win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW
                                    )
                                else:
                                    win32gui.SetWindowPos(
                                        self.hwnd,
                                        0,
                                        self.x, self.y, w_current, self.h,
                                        win32con.SWP_NOACTIVATE | win32con.SWP_NOMOVE | win32con.SWP_NOZORDER | win32con.SWP_NOREDRAW
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

    def _render_and_update(self):
        if not self.hwnd or not win32gui.IsWindow(self.hwnd):
            return

        w = max(self.collapsed_w, int(round(self.current_w)))
        h = self.h

        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)

        # Outer Soft Drop Shadow
        for offset in range(1, 4):
            shadow_alpha = int(30 / offset)
            d.rounded_rectangle(
                (offset, offset, w - offset, h - offset),
                radius=19,
                fill=None,
                outline=(0, 0, 0, shadow_alpha),
                width=1
            )

        # Obsidian Pill Body (#161618 with 1px border)
        bg_color = (22, 22, 24, 250)
        border_color = (60, 60, 65, 255)
        d.rounded_rectangle((2, 2, w - 2, h - 2), radius=19, fill=bg_color, outline=border_color, width=1)

        cx, cy = 22, 22

        if self.state == "recording":
            pulse_rad = 12 + int(3 * math.sin(self.anim_tick * 0.15))
            glow_alpha = int(140 + 70 * math.sin(self.anim_tick * 0.15))
            d.ellipse((cx - pulse_rad, cy - pulse_rad, cx + pulse_rad, cy + pulse_rad), outline=(239, 68, 68, glow_alpha), width=2)
            d.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill=(239, 68, 68, 255))

        elif self.state == "transcribing":
            d.ellipse((cx - 11, cy - 11, cx + 11, cy + 11), outline=(60, 50, 20, 200), width=2)
            spin_rad = self.anim_tick * 0.18
            for dot_i in range(3):
                da = spin_rad + dot_i * 0.4
                sx = cx + int(8 * math.cos(da))
                sy = cy + int(8 * math.sin(da))
                dot_alpha = 255 - dot_i * 60
                d.ellipse((sx - 2, sy - 2, sx + 2, sy + 2), fill=(245, 158, 11, dot_alpha))

        elif self.state == "done":
            d.ellipse((cx - 10, cy - 10, cx + 10, cy + 10), fill=(16, 185, 129, 255))
            d.line((cx - 4, cy, cx - 1, cy + 3), fill=(10, 10, 12, 255), width=2)
            d.line((cx - 1, cy + 3, cx + 4, cy - 3), fill=(10, 10, 12, 255), width=2)

        else:
            # Idle: Bright Ivory / Cyan Concentric Rings (distinctive WhisperFlow icon)
            d.ellipse((cx - 11, cy - 11, cx + 11, cy + 11), outline=(56, 189, 248, 200), width=2)
            d.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill=(255, 255, 240, 255))

        if w > 100:
            text_x = 46
            if self.state == "recording":
                d.text((text_x, 13), self.custom_text or "Listening...", fill=(239, 68, 68, 255), font=self.font_main)
                wave_start_x = w - 65
                for bi in range(5):
                    bar_h = 3 + int(9 * abs(math.sin(self.anim_tick * 0.16 + bi * 1.1)))
                    bar_x = wave_start_x + bi * 7
                    d.rounded_rectangle((bar_x, cy - bar_h, bar_x + 3, cy + bar_h), radius=2, fill=(239, 68, 68, 255))

            elif self.state == "transcribing":
                d.text((text_x, 13), self.custom_text or "Transcribing...", fill=(245, 158, 11, 255), font=self.font_main)

            elif self.state == "done":
                d.text((text_x, 13), self.custom_text or "Pasted!", fill=(16, 185, 129, 255), font=self.font_main)

            else:
                d.text((text_x, 13), "Dictate", fill=(255, 255, 240, 255), font=self.font_main)
                kw, kh = 80, 22
                kx, ky = w - kw - 12, (h - kh) // 2
                d.rounded_rectangle((kx, ky, kx + kw, ky + kh), radius=6, fill=(38, 38, 44, 255), outline=(58, 58, 66, 255), width=1)
                d.text((kx + 8, ky + 4), "Ctrl + Alt", fill=(215, 215, 210, 255), font=self.font_badge)

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
                hdc_mem,
                ctypes.byref(bmi),
                0,
                ctypes.byref(p_bits),
                None,
                0
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
                2  # ULW_ALPHA
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
        """Active watchdog called by main loop to guarantee window and thread stay alive."""
        if not self.running:
            return

        # Ensure thread is alive
        if not self.thread or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._window_thread, daemon=True)
            self.thread.start()

        # Signal GUI thread to recreate window if invalid
        if self.visible:
            if not self.hwnd or not win32gui.IsWindow(self.hwnd):
                self._request_recreate = True
            elif win32gui.IsWindow(self.hwnd):
                try:
                    win32gui.SetWindowPos(
                        self.hwnd,
                        win32con.HWND_TOPMOST,
                        self.x, self.y, int(self.current_w), self.h,
                        win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW
                    )
                except Exception:
                    pass

    def set_state(self, state, text=None):
        self.state = state
        self.custom_text = text
        if state == "done":
            self.done_timestamp = time.time()
        # Immediately re-assert topmost on state transition
        if self.visible and self.hwnd and win32gui.IsWindow(self.hwnd):
            try:
                win32gui.SetWindowPos(
                    self.hwnd,
                    win32con.HWND_TOPMOST,
                    self.x, self.y, int(self.current_w), self.h,
                    win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW
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
                    self.x, self.y, int(self.current_w), self.h,
                    win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW
                )

    def close(self):
        self.running = False
        if self.hwnd and win32gui.IsWindow(self.hwnd):
            try:
                win32gui.DestroyWindow(self.hwnd)
            except Exception:
                pass
