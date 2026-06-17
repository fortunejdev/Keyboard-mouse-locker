# Keyboard & Mouse Locker

A small Windows GUI app (Python + Tkinter) that locks your keyboard and mouse
and hides itself, then unlocks with a hotkey of your choice.

## Features

- 🔒 Locks **all** keyboard and mouse input system-wide
- 🪄 GUI disappears when you lock
- ⌨️ Pick your unlock hotkey from a dropdown (default: **Ctrl + Alt + U**)
- 🖥️ DPI-aware and auto-sizing — stays crisp on any resolution / display scaling
- 📦 No third-party packages required (standard library only)
- 🔑 No administrator rights needed

## Requirements

- Windows
- Python 3.8+

## Usage

```bash
python keyboard_mouse_lock.py
```

Choose your unlock hotkey, click **Lock Now**, and the window vanishes while
input is blocked. Press your hotkey to unlock and bring the window back.

## How it works

Windows low-level hooks (`WH_KEYBOARD_LL` / `WH_MOUSE_LL`) are installed in a
background thread via `ctypes`. Every keyboard/mouse event is swallowed so it
never reaches any application, but the keyboard hook still *sees* keys, letting
it watch for the unlock combo even while everything is locked.

## License

MIT
