from terminedia import shape, Screen, pause

from pathlib import Path

basepath = Path(__file__).parent


with Screen() as scr:
    for img_name in "moon_ascii_bw.pgm", "moon_ascii_color.ppm", "moon_bin_color.pnm", "moon_bin_bw.pgm":
        img = shape(basepath / img_name)
        scr.draw.blit((0,0), img)
        pause()
        scr.clear()

