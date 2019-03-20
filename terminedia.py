import termios, fcntl, sys, os
import threading
import time
from contextlib import contextmanager
from enum import Enum
from math import ceil

# Keyboard reading code copied and evolved from
# https://stackoverflow.com/a/6599441/108205
# (@mheyman, Mar, 2011)

@contextmanager
def realtime_keyb():
    """Waits for a single keypress on stdin.

    This is a silly function to call if you need to do it a lot because it has
    to store stdin's current setup, setup stdin for reading single keystrokes
    then read the single keystroke then revert stdin back after reading the
    keystroke.

    Returns a tuple of characters of the key that was pressed - on Linux,
    pressing keys like up arrow results in a sequence of characters. Returns
    ('\x03',) on KeyboardInterrupt which can happen when a signal gets
    handled.

    """
    fd = sys.stdin.fileno()
    # save old state
    flags_save = fcntl.fcntl(fd, fcntl.F_GETFL)
    attrs_save = termios.tcgetattr(fd)
    # make raw - the way to do this comes from the termios(3) man page.
    attrs = list(attrs_save) # copy the stored version to update
    # iflag
    attrs[0] &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK
                  | termios.ISTRIP | termios.INLCR | termios. IGNCR
                  | termios.ICRNL | termios.IXON )
    # oflag
    attrs[1] &= ~termios.OPOST
    # cflag
    attrs[2] &= ~(termios.CSIZE | termios. PARENB)
    attrs[2] |= termios.CS8
    # lflag
    attrs[3] &= ~(termios.ECHONL | termios.ECHO | termios.ICANON
                  | termios.ISIG | termios.IEXTEN)
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags_save | os.O_NONBLOCK)
    yield
    # restore old state
    termios.tcsetattr(fd, termios.TCSAFLUSH, attrs_save)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags_save)


def inkey(break_=True):
    keycode = ""
    while True:
        c = sys.stdin.read(1) # returns a single character
        if not c:
            break
        if c == "\x03" and break_:
            raise KeyboardInterrupt
        keycode += c
    return keycode


def testkeys():
    with realtime_keyb():
        while True:
            try:
                key = inkey()
            except KeyboardInterrupt:
                break
            if key:
                print("", key.encode("utf-8"), end="", flush=True)
            print(".", end="", flush=True)
            time.sleep(0.3)



DEFAULT_BG = 0xfffe
DEFAULT_FG = 0xffff

class Directions(Enum):
    UP = (0, -1)
    RIGHT = (1, 0)
    DOWN = (0, 1)
    LEFT = (-1, 0)


class BlockChars:
    QUADRANT_UPPER_LEFT = '\u2598'
    QUADRANT_UPPER_RIGHT = '\u259D'
    UPPER_HALF_BLOCK = '\u2580'
    QUADRANT_LOWER_LEFT = '\u2596'
    LEFT_HALF_BLOCK = '\u258C'
    QUADRANT_UPPER_RIGHT_AND_LOWER_LEFT = '\u259E'
    QUADRANT_UPPER_LEFT_AND_UPPER_RIGHT_AND_LOWER_LEFT = '\u259B'
    QUADRANT_LOWER_RIGHT = '\u2597'
    QUADRANT_UPPER_LEFT_AND_LOWER_RIGHT = '\u259A'
    QUADRANT_UPPER_LEFT_AND_UPPER_RIGHT_AND_LOWER_RIGHT = '\u259C'
    LOWER_HALF_BLOCK = u'\2584'
    QUADRANT_UPPER_LEFT_AND_LOWER_LEFT_AND_LOWER_RIGHT = '\u2599'
    FULL_BLOCK = '\u2588'


class ScreenCommands:

    def print(self, *args, sep='', end='', flush=True):
        print(*args, sep=sep, end=end, flush=flush)

    def CSI(self, *args):
        command = args[-1]
        args = ';'.join(str(arg) for arg in args[:-1]) if args else ''
        self.print("\x1b[", args, command)

    def SGR(self, *args):
        self.CSI(*args, 'm')

    def clear(self):
        self.CSI(2, 'J')

    def moveto(self, pos):
        x, y = pos
        self.CSI(f'{y + 1};{x + 1}H')

    def print_at(self, pos, txt):
        self.moveto(pos)
        self.print(txt)

    def _normalize_color(self, color):
        if isinstance(color, int):
            return color
        if 0 <= color[0] < 1.0 or color[0] == 1.0 and all(c <= 1.0 for c in color[1:]):
            color = tuple(int(c * 255) for c in color)
        return color

    def reset_colors(self):
        self.SGR(0)

    def set_colors(self, foreground, background):
        if foreground == DEFAULT_FG:
            fg_seq = 39,
        else:
            foreground = self._normalize_color(foreground)
            fg_seq = 38, 2, *foreground
        if background == DEFAULT_BG:
            bg_seq = 49,
        else:
            background = self._normalize_color(background)
            bg_seq = 49, 2, *background

        self.SGR(*fg_seq, *bg_seq)

    def set_fg_color(self, color):
        color = self._normalize_color(color)
        self.SGR(38, 2, *color)

    def set_bg_color(self, color):
        color = self._normalize_color(color)
        self.SGR(48, 2, *color)


class Drawing:
    """Intended to be used as a namespace for drawing, including primitives"""

    def __init__(self, set_fn, reset_fn, size_fn, context):
        self.set = set_fn
        self.reset = reset_fn
        self.size = property(size_fn)
        self.context = context

    def line(self, pos1, pos2):
        x1, y1 = pos1
        x2, y2 = pos2
        self.set(pos1)

        max_manh = max(abs(x2 - x1), abs(y2 - y1))
        if max_manh == 0:
            return
        step_x = (x2 - x1) / max_manh
        step_y = (y2 - y1) / max_manh
        total_manh = 0
        while total_manh < max_manh:
            x1 += step_x
            y1 += step_y
            total_manh += max(abs(step_x), abs(step_y))
            self.set((round(x1), round(y1)))

    def rect(self, pos1, pos2, fill=False):
        x1, y1 = pos1
        x2, y2 = pos2
        self.line(pos1, (x2, y1))
        self.line((x1, y2), pos2)
        if fill and y2 != y1:
            direction = int((y2 - y1) / abs(y2 - y1))
            for y in range(y1 + 1, y2, direction):
                self.line((x1, y), (x2, y))
        else:
            self.line(pos1, (x1, y2))
            self.line((x2, y1), pos2)

    def blit(self, pos, shape, color_map=None, erase=False):
        """Blits a blocky image in the associated screen at POS

        Any character but space (\x20) or "." is considered a block.
        Shape can be a "\n" separated string or a list of strings.
        If a color_map is not given, any non-space character is
        set with the context color. Otherwise, color_map
        should be a mapping from characters to RGB colors
        for each block.

        If "erase" is given, spaces are erased, instead of being ignored.

        ("." is allowed as white-space to allow drawing shapes
        inside Python multi-line strings when editors
        and linters are set to remove trailing spaces)
        """
        if isinstance(shape, str):
            shape = shape.split("\n")
        last_color = self.context.color
        for y, line in enumerate(shape, start=pos[1]):
            for x, char in enumerate(line, start=pos[0]):
                if char not in " .":
                    if color_map:
                        color = color_map[char]
                        if color != last_color:
                            self.context.color = last_color = color
                    self.set((x, y))
                elif erase:
                    self.reset((x, y))


class Screen:
    lock = threading.Lock()

    def __init__(self, size=()):
        if not size:
            self.get_size = os.get_terminal_size
            size = os.get_terminal_size()
        else:
            self.get_size = lambda: size

        self.context = threading.local()

        self.draw = Drawing(self.set_at, self.reset_at, self.get_size, self.context)
        self.width, self.height = self.size = size

        self.commands = ScreenCommands()
        self.clear(True)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.commands.clear()
        self.commands.reset_colors()

    def clear(self, wet_run=True):
        self.data = [" "] * self.width * self.height
        self.color_data = [(DEFAULT_FG, DEFAULT_BG)] * self.width * self.height
        self.context.color = DEFAULT_FG
        self.context.background = DEFAULT_BG
        self.context.direction = Directions.RIGHT
        # To use when we allow custom chars along with blocks:
        # self.char_data = " " * self.width * self.height
        if wet_run:
            with self.lock:
                self.commands.clear()

    def set_at(self, pos, color=None):
        if color:
            self.context.color = color
        self[pos] = BlockChars.FULL_BLOCK

    def reset_at(self, pos):
        self[pos] = " "

    def line_at(self, pos, length, sequence=BlockChars.FULL_BLOCK):
        x, y = pos
        for i, char in zip(range(length), sequence * (ceil(length / len(sequence)))):
            self[x, y] = char
            x += self.context.direction.value[0]
            y += self.context.direction.value[1]

    def __getitem__(self, pos):
        return self.data[pos[0] + pos[1] * self.width]

    def __setitem__(self, pos, value):
        index = pos[0] + pos[1] * self.width
        self.data[index] = value
        with self.lock:
            colors = self.context.color, self.context.background
            self.color_data[index] = colors
            self.commands.set_colors(*colors)
            self.commands.print_at(pos, value)




def test_lines(scr):
        scr.draw.line((30, 15), (30,1))
        scr.draw.line((30, 15), (1, 1))
        scr.draw.line((30, 15), (60, 1))
        scr.draw.line((30, 15), (60, 13))
        scr.draw.line((30, 15), (1, 17))
        scr.draw.line((30, 15), (26, 29))
        scr.draw.line((30, 15), (34, 29))
        scr.draw.line((30, 15), (19, 25))
        scr.draw.line((30, 15), (42, 25))


shape1 = """\
           .
     *     .
    * *    .
   ** **   .
  *** ***  .
 ********* .
           .
"""

shape2 = """\
                   .
    *    **    *   .
   **   ****   **  .
  **   **##**   ** .
  **   **##**   ** .
  **   **##**   ** .
  **************** .
  **************** .
    !!   !!   !!   .
   %  % %  % %  %  .
                   .
"""

c_map = {
    '*': DEFAULT_FG,
    '#': (.5, 0.8, 0.8),
    '!': (1, 0, 0),
    '%': (1, 0.7, 0),
}

def main():
    with realtime_keyb(), Screen() as scr:
        scr.draw.rect((5, 5), (45, 20))
        scr.draw.rect((55, 10), (95, 25), fill=True)

        scr.draw.blit((8, 8), shape1)
        scr.draw.blit((57, 12), shape1, erase=True)

        scr.draw.blit((20, 8), shape2, c_map)
        scr.draw.blit((70, 12), shape2, c_map, erase=True)


        scr[0, scr.height -1] = ' '
        while True:
            if inkey() == '\x1b':
                break
            time.sleep(0.05)


if __name__ == "__main__":
    # testkeys()
    main()