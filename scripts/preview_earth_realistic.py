#!/usr/bin/env python3
"""Preview a geographically grounded west-to-east Earth rotation.

Uses Natural Earth country boundaries to rasterize real land onto a fixed
black globe with white land cutouts. The central meridian sequence is:
120E (East Asia/Australia) -> 30E (Europe/Africa) -> 90W (Americas).
Frame 0 is forced to the existing icon_earth pixels so the current icon remains
unchanged at startup; subsequent frames use the real geographic texture.
"""
from pathlib import Path
import json, math
from PIL import Image, ImageDraw
from shapely.geometry import shape, Point
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "preview"
DATA = OUT / "ne_110m_admin_0_countries.geojson"
SIZE = 48
SCALE = 8
FRAMES = 12
CX = CY = 24.0
R = 20.0

if not DATA.exists():
    raise SystemExit(f"missing {DATA}; download Natural Earth GeoJSON first")

# Merge country polygons so internal borders do not appear as white seams.
geo = json.loads(DATA.read_text(encoding="utf-8"))
land_geoms = []
for feature in geo["features"]:
    props = feature.get("properties", {})
    if props.get("CONTINENT") == "Antarctica":
        continue
    land_geoms.append(shape(feature["geometry"]))
LAND = unary_union(land_geoms)

# Read the current icon for the immutable first frame.
src = (ROOT / "firmware" / "components" / "ui_app" / "icons.c").read_text(
    encoding="utf-8", errors="replace"
)
import re
nums = [int(x) for x in re.findall(r"\d+", re.search(r"earth_map\[\]\s*=\s*\{([^}]*)\}", src).group(1))]
existing = Image.new("L", (SIZE, SIZE))
existing.putdata(nums)
existing_screen = Image.eval(existing, lambda v: 255 - v)


def is_land(lon, lat):
    # Avoid treating longitude at the antimeridian as a discontinuity.
    lon = ((lon + 180.0) % 360.0) - 180.0
    return LAND.covers(Point(lon, lat))


def render(center_lon):
    # Screen appearance: white background, black ocean/globe.
    im = Image.new("L", (SIZE, SIZE), 255)
    d = ImageDraw.Draw(im)
    for y in range(SIZE):
        for x in range(SIZE):
            nx = (x - CX) / R
            ny = (CY - y) / R
            rr = nx * nx + ny * ny
            if rr > 1:
                continue
            d.point((x, y), fill=0)
            if math.sqrt((x - CX) ** 2 + (y - CY) ** 2) >= R - 1.0:
                continue
            z = math.sqrt(max(0.0, 1.0 - rr))
            view_lon = math.degrees(math.atan2(nx, z))
            lat = math.degrees(math.asin(max(-1.0, min(1.0, ny))))
            # Positive screen phase moves the central meridian westward, so
            # surface features travel left->right: west-to-east rotation.
            lon = center_lon + view_lon
            if is_land(lon, lat):
                d.point((x, y), fill=255)
    return im


centers = [120.0 - (360.0 * i / FRAMES) for i in range(FRAMES)]
frames = []
for i, center in enumerate(centers):
    if i == 0:
        screen = existing_screen
    else:
        screen = render(center)
    frame = screen.resize((SIZE * SCALE, SIZE * SCALE), Image.NEAREST)
    frame.save(OUT / f"earth_real_f{i}.png")
    frames.append(frame)

sheet = Image.new("L", (SIZE * SCALE * FRAMES, SIZE * SCALE), 255)
for i, frame in enumerate(frames):
    sheet.paste(frame, (i * SIZE * SCALE, 0))
sheet.save(OUT / "earth_real_contact.png")

print("dataset:", DATA)
print("center longitudes:", [round(c, 1) for c in centers])
print("key views: frame0 existing East Asia/Australia; frame3 Europe/Africa; frame7 Americas")
print("frame0 preserved: True")
print("wrote earth_real_contact.png")
