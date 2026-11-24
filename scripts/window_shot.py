"""Capture one window to a PNG with PrintWindow (works while occluded)."""
import ctypes
import sys

from PIL import Image

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32


class RECT(ctypes.Structure):
    _fields_ = [("Left", ctypes.c_long), ("Top", ctypes.c_long),
                ("Right", ctypes.c_long), ("Bottom", ctypes.c_long)]


class HEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long), ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class INFO(ctypes.Structure):
    _fields_ = [("bmiHeader", HEADER), ("bmiColors", ctypes.c_uint32 * 4)]


def capture(hwnd):
    rect = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width = rect.Right - rect.Left
    height = rect.Bottom - rect.Top
    screen_dc = user32.GetDC(0)
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    bmp = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
    gdi32.SelectObject(mem_dc, bmp)
    user32.PrintWindow(hwnd, mem_dc, 2)
    info = INFO()
    info.bmiHeader.biSize = ctypes.sizeof(HEADER)
    info.bmiHeader.biWidth = width
    info.bmiHeader.biHeight = -height
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(mem_dc, bmp, 0, height, buf, ctypes.byref(info), 0)
    image = Image.frombuffer("RGBA", (width, height), buf.raw, "raw",
                             "BGRA", 0, 1).convert("RGB")
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(0, screen_dc)
    return image


title = sys.argv[1] if len(sys.argv) > 1 else "TALCON"
hwnd = user32.FindWindowW(None, title)
if not hwnd:
    raise SystemExit("window not found: %s" % title)
out = sys.argv[2] if len(sys.argv) > 2 else "window_shot.png"
capture(hwnd).save(out)
print("saved", out)
