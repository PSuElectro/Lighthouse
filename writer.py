# writer.py
# Simple monochrome font writer for MicroPython framebuf displays.
# Compatible with font modules produced for the font_to_py-style interface.

import framebuf


class Writer:
    def __init__(self, device, font, verbose=True):
        if not isinstance(device, framebuf.FrameBuffer):
            raise ValueError("Device must be derived from FrameBuffer.")

        self.device = device
        self.font = font
        self.row = 0
        self.col = 0

        if font.hmap():
            self.map = framebuf.MONO_HMSB if font.reverse() else framebuf.MONO_HLSB
        else:
            raise ValueError("Font must be horizontally mapped.")

        if font.height() > device.height:
            raise ValueError("Font too tall for display.")

        if verbose:
            print("Font height:", font.height())
            print("Font width:", font.max_width())

    @staticmethod
    def set_textpos(device, row=None, col=None):
        # Kept for compatibility with the usual Writer API.
        # The actual position is set on the Writer instance by setpos().
        return row, col

    def setpos(self, row, col):
        self.row = row
        self.col = col

    def printstring(self, string, invert=False):
        for ch in string:
            if ch == "\n":
                self.row += self.font.height()
                self.col = 0
                continue

            glyph, height, width = self.font.get_ch(ch)

            # Make a writable copy because FrameBuffer expects a buffer.
            buf = bytearray(glyph)

            if invert:
                for i in range(len(buf)):
                    buf[i] ^= 0xFF

            fb = framebuf.FrameBuffer(
                buf,
                width,
                height,
                self.map
            )

            self.device.blit(fb, self.col, self.row)
            self.col += width

    def stringlen(self, string):
        return sum(self.font.get_ch(ch)[2] for ch in string)
