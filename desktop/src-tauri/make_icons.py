"""Cut the mascot's head out of the repo's logo.png and render the desktop icon set.

ponytail: run by hand when the artwork changes, not at build time — the output is committed, so a
build needs no Pillow and no source PNG. `python3 src-tauri/make_icons.py` from desktop/.
"""
from collections import deque
from PIL import Image, ImageDraw
import os

SRC = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "logo.png")
BOX = (170, 64, 264, 196)
JAW = 0.80  # fraction of the masked height where the head stops and the body starts

def head():
	im = Image.open(SRC).convert("RGBA").crop(BOX)
	w, h = im.size
	px = im.load()
	dark = [[max(px[x, y][:3]) < 120 for x in range(w)] for y in range(h)]

	# the silhouette is the largest connected dark blob; strays (the magnifier, the cape) are dropped
	seen, best = [[False]*w for _ in range(h)], []
	for sy in range(h):
		for sx in range(w):
			if dark[sy][sx] and not seen[sy][sx]:
				comp, q = [], deque([(sx, sy)])
				seen[sy][sx] = True
				while q:
					x, y = q.popleft(); comp.append((x, y))
					for dx, dy in ((1,0),(-1,0),(0,1),(0,-1)):
						nx, ny = x+dx, y+dy
						if 0 <= nx < w and 0 <= ny < h and dark[ny][nx] and not seen[ny][nx]:
							seen[ny][nx] = True; q.append((nx, ny))
				if len(comp) > len(best): best = comp
	body = [[False]*w for _ in range(h)]
	for x, y in best: body[y][x] = True

	# fill its holes: the face whites are enclosed by the silhouette, the background is not
	outside, q = [[False]*w for _ in range(h)], deque()
	for x in range(w):
		for y in (0, h-1):
			if not body[y][x] and not outside[y][x]: outside[y][x] = True; q.append((x, y))
	for y in range(h):
		for x in (0, w-1):
			if not body[y][x] and not outside[y][x]: outside[y][x] = True; q.append((x, y))
	while q:
		x, y = q.popleft()
		for dx, dy in ((1,0),(-1,0),(0,1),(0,-1)):
			nx, ny = x+dx, y+dy
			if 0 <= nx < w and 0 <= ny < h and not body[ny][nx] and not outside[ny][nx]:
				outside[ny][nx] = True; q.append((nx, ny))

	for y in range(h):
		for x in range(w):
			if outside[y][x]:
				px[x, y] = (0, 0, 0, 0)
			else:
				r, g, b, _ = px[x, y]
				px[x, y] = (13, 11, 10, 255) if max(r, g, b) < 150 else (253, 250, 246, 255)
	im = im.crop(im.getbbox())
	return im.crop((0, 0, im.width, int(im.height * JAW))).crop(
		im.crop((0, 0, im.width, int(im.height * JAW))).getbbox())

def badge(size, pad=0.13, radius=0.23, bg=(240, 163, 92, 255)):
	"""The head on a rounded accent tile, rendered at 4x and downsampled so the curve stays smooth."""
	S = size * 4
	tile = Image.new("RGBA", (S, S), (0, 0, 0, 0))
	mask = Image.new("L", (S, S), 0)
	ImageDraw.Draw(mask).rounded_rectangle([0, 0, S-1, S-1], radius=int(S*radius), fill=255)
	tile.paste(Image.new("RGBA", (S, S), bg), (0, 0), mask)
	h = head()
	box = int(S * (1 - 2*pad))
	scale = min(box / h.width, box / h.height)
	h = h.resize((max(1, int(h.width*scale)), max(1, int(h.height*scale))), Image.LANCZOS)
	tile.paste(h, ((S - h.width)//2, (S - h.height)//2), h)
	return tile.resize((size, size), Image.LANCZOS)

if __name__ == "__main__":
	here = os.path.dirname(os.path.realpath(__file__))
	h = head()
	h.resize((h.width * 6, h.height * 6), Image.LANCZOS).save(os.path.join(here, "..", "src", "head.png"))
	icons = os.path.join(here, "icons")
	badge(1024).save(os.path.join(icons, "icon.png"))
	for name, size in [("32x32.png", 32), ("128x128.png", 128), ("128x128@2x.png", 256),
	                   ("Square30x30Logo.png", 30), ("Square44x44Logo.png", 44), ("Square71x71Logo.png", 71),
	                   ("Square89x89Logo.png", 89), ("Square107x107Logo.png", 107), ("Square142x142Logo.png", 142),
	                   ("Square150x150Logo.png", 150), ("Square284x284Logo.png", 284),
	                   ("Square310x310Logo.png", 310), ("StoreLogo.png", 50)]:
		badge(size).save(os.path.join(icons, name))
	# .ico carries its own sizes; .icns is macOS-only and left as the tauri CLI made it
	badge(256).save(os.path.join(icons, "icon.ico"),
	                sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
	print("icons written to", icons)
