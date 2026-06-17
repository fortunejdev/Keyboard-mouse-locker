"""
Keyboard & Mouse Locker (Windows)
=================================

A small Tkinter GUI that locks the keyboard and mouse. When you press "Lock":
  * all keyboard and mouse input is blocked system-wide
  * the GUI window disappears
  * the only thing the machine listens for is the chosen unlock hotkey

Pick your unlock hotkey from the dropdown (default: CTRL + ALT + U).

How it works
------------
Windows low-level hooks (WH_KEYBOARD_LL / WH_MOUSE_LL) are installed in a
dedicated thread. Every keyboard / mouse event is swallowed (the hook returns
1 instead of calling the next hook), so nothing reaches any application.
The keyboard hook still *sees* every key, so it can watch for the unlock combo
even while everything is blocked.

Notes
-----
* Windows only (uses the Win32 API through ctypes). No admin rights needed.
* No third-party packages required (just the standard library).
* The GUI is DPI-aware and auto-sizes/centres itself, so it stays crisp and
  correctly proportioned on any resolution or display-scaling setting.
"""

import ctypes
import threading
import tkinter as tk
from tkinter import ttk
from ctypes import wintypes

# --------------------------------------------------------------------------- #
#  Win32 constants
# --------------------------------------------------------------------------- #
WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

# Virtual-key code groups for the modifiers (generic, left, right variants)
VK_CTRL = frozenset({0x11, 0xA2, 0xA3})    # CONTROL, LCONTROL, RCONTROL
VK_ALT = frozenset({0x12, 0xA4, 0xA5})     # MENU(ALT), LMENU, RMENU
VK_SHIFT = frozenset({0x10, 0xA0, 0xA1})   # SHIFT, LSHIFT, RSHIFT

# Available unlock hotkeys.  Each entry is (list-of-required-modifier-groups,
# main-key-virtual-code).  The combo fires when at least one key from every
# required group is held AND the main key is held.
HOTKEYS = {
    "Ctrl + Alt + U":          ([VK_CTRL, VK_ALT], 0x55),           # default
    "Ctrl + Alt + L":          ([VK_CTRL, VK_ALT], 0x4C),
    "Ctrl + Shift + U":        ([VK_CTRL, VK_SHIFT], 0x55),
    "Ctrl + Alt + Shift + K":  ([VK_CTRL, VK_ALT, VK_SHIFT], 0x4B),
    "Ctrl + Alt + End":        ([VK_CTRL, VK_ALT], 0x23),
    "Ctrl + Alt + Pause":      ([VK_CTRL, VK_ALT], 0x13),
}
DEFAULT_HOTKEY = "Ctrl + Alt + U"

# --------------------------------------------------------------------------- #
#  ctypes type setup (important on 64-bit so pointers aren't truncated)
# --------------------------------------------------------------------------- #
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
ULONG_PTR = wintypes.WPARAM

HOOKPROC = ctypes.CFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD
]
user32.CallNextHookEx.restype = LRESULT
user32.CallNextHookEx.argtypes = [
    wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT
]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]


def enable_dpi_awareness():
    """Make the process DPI-aware so Tk renders crisp text instead of a
    blurry bitmap-stretched window on high-DPI / scaled displays."""
    try:
        # Per-monitor-aware v2 (Win 10+) gives the sharpest result.
        ctypes.windll.user32.SetProcessDpiAwarenessContext(
            wintypes.HANDLE(-4)
        )
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # system DPI aware
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


# --------------------------------------------------------------------------- #
#  The locker engine
# --------------------------------------------------------------------------- #
class InputLocker:
    """Installs low-level keyboard/mouse hooks in a background thread."""

    def __init__(self, on_unlock=None):
        self._on_unlock = on_unlock
        self._thread = None
        self._kbd_hook = None
        self._mouse_hook = None
        self._pressed = set()                # currently-held virtual keys
        self._mod_groups = []                # required modifier groups
        self._main_vk = None                 # required main key
        # Keep strong references so the callbacks aren't garbage-collected.
        self._kbd_proc = HOOKPROC(self._keyboard_proc)
        self._mouse_proc = HOOKPROC(self._mouse_proc_fn)
        self.unlocked = threading.Event()

    # ---- hook callbacks (run in the hook thread) ------------------------- #
    def _keyboard_proc(self, nCode, wParam, lParam):
        if nCode == 0:  # HC_ACTION
            kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            vk = kb.vkCode
            if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                self._pressed.add(vk)
                if self._is_unlock_combo():
                    self._trigger_unlock()
            elif wParam in (WM_KEYUP, WM_SYSKEYUP):
                self._pressed.discard(vk)
        return 1  # swallow the event -> keyboard is locked

    def _mouse_proc_fn(self, nCode, wParam, lParam):
        if nCode == 0:
            return 1  # swallow the event -> mouse is locked
        return user32.CallNextHookEx(self._mouse_hook, nCode, wParam, lParam)

    def _is_unlock_combo(self):
        if self._main_vk not in self._pressed:
            return False
        return all(self._pressed & group for group in self._mod_groups)

    def _trigger_unlock(self):
        # Runs inside the hook thread -> safe to unhook here.
        self._remove_hooks()
        user32.PostQuitMessage(0)
        self.unlocked.set()
        if self._on_unlock:
            self._on_unlock()

    # ---- thread body ----------------------------------------------------- #
    def _run(self):
        hmod = kernel32.GetModuleHandleW(None)
        self._kbd_hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._kbd_proc, hmod, 0
        )
        self._mouse_hook = user32.SetWindowsHookExW(
            WH_MOUSE_LL, self._mouse_proc, hmod, 0
        )
        if not self._kbd_hook or not self._mouse_hook:
            self._remove_hooks()
            self.unlocked.set()
            return

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _remove_hooks(self):
        if self._kbd_hook:
            user32.UnhookWindowsHookEx(self._kbd_hook)
            self._kbd_hook = None
        if self._mouse_hook:
            user32.UnhookWindowsHookEx(self._mouse_hook)
            self._mouse_hook = None

    # ---- public API ------------------------------------------------------ #
    def lock(self, mod_groups, main_vk):
        self._mod_groups = list(mod_groups)
        self._main_vk = main_vk
        self._pressed.clear()
        self.unlocked.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()


# --------------------------------------------------------------------------- #
#  GUI
# --------------------------------------------------------------------------- #
class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Keyboard & Mouse Locker")

        # Scale Tk's internal units to the real screen DPI so points map to
        # correct physical sizes and the layout looks right at any resolution.
        dpi = self.root.winfo_fpixels("1i")
        self.root.tk.call("tk", "scaling", dpi / 72.0)

        self.locker = InputLocker()
        self.hotkey_var = tk.StringVar(value=DEFAULT_HOTKEY)

        pad = {"padx": 20}
        tk.Label(
            self.root,
            text="Keyboard & Mouse Locker",
            font=("Segoe UI", 15, "bold"),
        ).pack(pady=(20, 2), **pad)

        tk.Label(
            self.root,
            text="Lock all input. Disappear. Unlock with your hotkey.",
            font=("Segoe UI", 9),
            fg="#555",
        ).pack(pady=(0, 16), **pad)

        tk.Label(
            self.root,
            text="Unlock hotkey",
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", **pad)

        combo = ttk.Combobox(
            self.root,
            textvariable=self.hotkey_var,
            values=list(HOTKEYS.keys()),
            state="readonly",
            font=("Segoe UI", 10),
        )
        combo.pack(fill="x", pady=(2, 18), **pad)

        tk.Button(
            self.root,
            text="🔒  Lock Now",
            font=("Segoe UI", 12, "bold"),
            bg="#c0392b",
            fg="white",
            activebackground="#e74c3c",
            activeforeground="white",
            relief="flat",
            padx=10,
            pady=10,
            command=self.lock,
        ).pack(fill="x", pady=(0, 20), **pad)

        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)
        self._center_window()

    def _center_window(self):
        # Auto-size to content, then centre on the active screen.
        self.root.update_idletasks()
        w = self.root.winfo_reqwidth()
        h = self.root.winfo_reqheight()
        x = (self.root.winfo_screenwidth() - w) // 2
        y = (self.root.winfo_screenheight() - h) // 3
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def lock(self):
        mod_groups, main_vk = HOTKEYS[self.hotkey_var.get()]
        self.root.withdraw()          # hide the GUI
        self.root.update()
        self.locker.lock(mod_groups, main_vk)
        self._poll_for_unlock()

    def _poll_for_unlock(self):
        if self.locker.unlocked.is_set():
            self.root.deiconify()     # bring the GUI back
            self.root.lift()
            self.root.focus_force()
        else:
            self.root.after(100, self._poll_for_unlock)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    import sys

    if sys.platform != "win32":
        raise SystemExit("This script only works on Windows.")
    enable_dpi_awareness()
    App().run()
