#!/usr/bin/env python3
import ctypes
import sys

if len(sys.argv) != 5:
    raise SystemExit("usage: x11ResizeWindow.py <display> <window_id> <width> <height>")

display_name = sys.argv[1].encode("utf8")
window_id = int(sys.argv[2], 0)
width = int(sys.argv[3])
height = int(sys.argv[4])

lib = ctypes.cdll.LoadLibrary("libX11.so.6")
lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
lib.XOpenDisplay.restype = ctypes.c_void_p
lib.XResizeWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_uint, ctypes.c_uint]
lib.XResizeWindow.restype = ctypes.c_int
lib.XFlush.argtypes = [ctypes.c_void_p]
lib.XFlush.restype = ctypes.c_int
lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
lib.XCloseDisplay.restype = ctypes.c_int

display = lib.XOpenDisplay(display_name)
if not display:
    raise SystemExit(f"could not open X display {sys.argv[1]}")
try:
    lib.XResizeWindow(display, window_id, width, height)
    lib.XFlush(display)
finally:
    lib.XCloseDisplay(display)
