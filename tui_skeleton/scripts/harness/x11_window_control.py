#!/usr/bin/env python3
import argparse
import sys
import time
from Xlib import X, display, XK
from Xlib.ext import xtest

SHIFT_CHARS = '~!@#$%^&*()_+{}|:"<>?'
PUNCT_SHIFT_MAP = {
    '!': '1', '@': '2', '#': '3', '$': '4', '%': '5', '^': '6', '&': '7', '*': '8', '(': '9', ')': '0',
    '_': '-', '+': '=', '{': '[', '}': ']', '|': '\\', ':': ';', '"': "'", '<': ',', '>': '.', '?': '/'
}
PUNCT_KEYSYM_MAP = {
    '`': 'grave', '-': 'minus', '=': 'equal', '[': 'bracketleft', ']': 'bracketright',
    '\\': 'backslash', ';': 'semicolon', "'": 'apostrophe', ',': 'comma', '.': 'period',
    '/': 'slash',
}

def parse_window_id(value: str) -> int:
    value = value.strip()
    if value.lower().startswith('0x'):
        return int(value, 16)
    return int(value)

def get_window(disp, window_id: str):
    return disp.create_resource_object('window', parse_window_id(window_id))

def focus_window(disp, win):
    try:
        win.set_input_focus(X.RevertToParent, X.CurrentTime)
    except Exception:
        pass
    try:
        win.configure(stack_mode=X.Above)
    except Exception:
        pass
    disp.sync()
    time.sleep(0.05)

def click_window(disp, win):
    try:
        geom = win.get_geometry()
        translated = win.translate_coords(disp.screen().root, 0, 0)
        center_x = translated.x + max(1, geom.width // 2)
        center_y = translated.y + max(1, geom.height // 2)
        xtest.fake_input(disp, X.MotionNotify, x=center_x, y=center_y)
        xtest.fake_input(disp, X.ButtonPress, 1)
        xtest.fake_input(disp, X.ButtonRelease, 1)
        disp.sync()
        time.sleep(0.08)
    except Exception:
        pass

def send_keycode(disp, keycode: int, shift: bool = False, ctrl: bool = False):
    shift_code = disp.keysym_to_keycode(XK.string_to_keysym('Shift_L')) if shift else 0
    ctrl_code = disp.keysym_to_keycode(XK.string_to_keysym('Control_L')) if ctrl else 0
    if ctrl_code:
        xtest.fake_input(disp, X.KeyPress, ctrl_code)
    if shift_code:
        xtest.fake_input(disp, X.KeyPress, shift_code)
    xtest.fake_input(disp, X.KeyPress, keycode)
    xtest.fake_input(disp, X.KeyRelease, keycode)
    if shift_code:
        xtest.fake_input(disp, X.KeyRelease, shift_code)
    if ctrl_code:
        xtest.fake_input(disp, X.KeyRelease, ctrl_code)
    disp.sync()

def type_text(disp, text: str):
    for ch in text:
        if ch == '\n':
            keycode = disp.keysym_to_keycode(XK.string_to_keysym('Return'))
            send_keycode(disp, keycode)
            continue
        shift = False
        source = ch
        if 'A' <= ch <= 'Z':
            shift = True
            source = ch.lower()
        elif ch in SHIFT_CHARS:
            shift = True
            source = PUNCT_SHIFT_MAP.get(ch, ch)
        if source == ' ':
            keysym = XK.XK_space
        elif source in PUNCT_KEYSYM_MAP:
            keysym = XK.string_to_keysym(PUNCT_KEYSYM_MAP[source])
        else:
            keysym = XK.string_to_keysym(source)
        if keysym == 0:
            continue
        keycode = disp.keysym_to_keycode(keysym)
        if keycode == 0:
            continue
        send_keycode(disp, keycode, shift=shift)
        time.sleep(0.01)

def press_named_key(disp, name: str):
    lowered = name.lower()
    if lowered in ('enter', 'return'):
        send_keycode(disp, disp.keysym_to_keycode(XK.string_to_keysym('Return')))
        return
    if lowered in ('escape', 'esc'):
        send_keycode(disp, disp.keysym_to_keycode(XK.string_to_keysym('Escape')))
        return
    if lowered.startswith('ctrl+') and len(lowered) == len('ctrl+') + 1 and 'a' <= lowered[-1] <= 'z':
        send_keycode(disp, disp.keysym_to_keycode(XK.string_to_keysym(lowered[-1])), ctrl=True)
        return
    if lowered == 'ctrl+shift+o':
        send_keycode(disp, disp.keysym_to_keycode(XK.string_to_keysym('o')), ctrl=True, shift=True)
        return
    if lowered == 'ctrl+shift+t':
        send_keycode(disp, disp.keysym_to_keycode(XK.string_to_keysym('t')), ctrl=True, shift=True)
        return
    if lowered == 'backspace':
        send_keycode(disp, disp.keysym_to_keycode(XK.string_to_keysym('BackSpace')))
        return
    if len(name) == 1:
        type_text(disp, name)
        return
    raise SystemExit(f'unsupported key: {name}')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--display', required=True)
    parser.add_argument('--window', required=True)
    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser('focus')
    p_type = sub.add_parser('type')
    p_type.add_argument('--text', required=True)
    p_press = sub.add_parser('press')
    p_press.add_argument('--key', required=True)
    sub.add_parser('click')
    p_resize = sub.add_parser('resize')
    p_resize.add_argument('--width', type=int, required=True)
    p_resize.add_argument('--height', type=int, required=True)

    args = parser.parse_args()
    disp = display.Display(args.display)
    win = get_window(disp, args.window)
    focus_window(disp, win)

    if args.command == 'focus':
        return 0
    if args.command == 'click':
        click_window(disp, win)
        return 0
    if args.command == 'type':
        type_text(disp, args.text)
        return 0
    if args.command == 'press':
        press_named_key(disp, args.key)
        return 0
    if args.command == 'resize':
        win.configure(width=args.width, height=args.height)
        disp.sync()
        time.sleep(0.15)
        return 0
    return 0

if __name__ == '__main__':
    sys.exit(main())
