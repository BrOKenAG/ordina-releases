#!/usr/bin/env python3
"""Render restrained drag-to-install artwork at 1x and 2x."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.build/dmg-libs'))
from PIL import Image, ImageDraw

root = Path(__file__).resolve().parents[1]


def render(scale):
    factor = scale * 4
    image = Image.new('RGB', (720 * factor, 480 * factor), 'white')
    draw = ImageDraw.Draw(image)

    def stroke(points, width=3):
        points = [(x * factor, y * factor) for x, y in points]
        draw.line(points, fill='#24282D', width=round(width * factor), joint='curve')
        radius = width * factor / 2
        for x, y in [points[0], points[-1]]:
            draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill='#24282D')

    points = []
    for step in range(101):
        t = step / 100
        x = (1-t)**3*298 + 3*(1-t)**2*t*340 + 3*(1-t)*t*t*382 + t**3*422
        y = (1-t)**3*235 + 3*(1-t)**2*t*219 + 3*(1-t)*t*t*219 + t**3*233
        points.append((x, y))
    stroke(points)
    stroke([(405, 219), (423, 233), (405, 244)])
    target = root / 'installer' / ('drop@2x.png' if scale == 2 else 'drop.png')
    target.parent.mkdir(exist_ok=True)
    image.resize((720*scale, 480*scale), Image.Resampling.LANCZOS).save(target)
    print(target)


if __name__ == '__main__':
    for scale in (1, 2):
        render(scale)
