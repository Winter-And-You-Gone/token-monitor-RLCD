#!/usr/bin/env python3
# Generate LVGL v9 A8 image descriptors for the RLCD UI icons.
from pathlib import Path
import cairosvg, io, math
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT_C = ROOT / "firmware" / "components" / "ui_app" / "icons.c"
OUT_H = ROOT / "firmware" / "components" / "ui_app" / "icons.h"

def svg_alpha(path, size):
    png = cairosvg.svg2png(url=path, output_width=size, output_height=size)
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    return im.split()[3]  # alpha channel as L image

def weather_alpha(kind, size):
    # draw black shapes on transparent, return alpha
    S=4; im=Image.new("L",(size*S,size*S),0); d=ImageDraw.Draw(im)
    cx=cy=size*S/2; W=2*S
    def E(x0,y0,x1,y1,w=W,f=None): d.ellipse([x0,y0,x1,y1],outline=255,width=w,fill=f)
    def L(x0,y0,x1,y1,w=W): d.line([x0,y0,x1,y1],fill=255,width=w)
    su=size*S*0.30
    if kind in("clear","partly"):
        sx,sy=(cx-size*S*0.16,cy-size*S*0.13) if kind=="partly" else (cx,cy)
        E(sx-su,sy-su,sx+su,sy+su)
        if kind=="clear":
            for a in range(0,360,45):
                dx,dy=math.cos(math.radians(a)),math.sin(math.radians(a))
                L(sx+dx*(su+3*S),sy+dy*(su+3*S),sx+dx*(su+8*S),sy+dy*(su+8*S))
    if kind in("partly","cloud","rain","snow","fog"):
        ox,oy=(cx+size*S*0.13,cy+size*S*0.13) if kind=="partly" else (cx,cy)
        r=size*S*0.22
        E(ox-r*1.6,oy-r*0.2,ox+r*0.2,oy+r*1.4)
        E(ox-r*0.6,oy-r*1.2,ox+r*1.4,oy+r*0.9)
        E(ox+r*0.4,oy-r*0.1,ox+r*2.0,oy+r*1.4)
        d.rectangle([ox-r*1.6,oy+r*0.6,ox+r*2.0,oy+r*1.4],fill=255)
        d.rectangle([ox-r*1.3,oy+r*0.7,ox+r*1.8,oy+r*1.25],fill=0)
        if kind=="rain":
            for i in range(3): L(ox-r+ i*r,oy+r*1.6,ox-r-3*S+i*r,oy+r*2.5)
        if kind=="snow":
            for i in range(3): d.text((ox-r+i*r,oy+r*1.5),"*",fill=255)
    return im.resize((size,size),Image.LANCZOS)

ICONS={}
ICONS["claudecode"]=svg_alpha(str(ROOT / "docs" / "assets" / "claudecode.svg"),34)
ICONS["deepseek"]=svg_alpha(str(ROOT / "docs" / "assets" / "deepseek.svg"),34)
ICONS["codex"]=svg_alpha(str(ROOT / "docs" / "assets" / "codex.svg"),34)
for k in ("clear","partly","cloud","rain","snow","fog"):
    ICONS["wx_"+k]=weather_alpha(k,26)

def emit(name,img):
    w,h=img.size; data=img.tobytes()
    arr=",".join(str(b) for b in data)
    cdef=(f"static const uint8_t {name}_map[] = {{{arr}}};\n"
          f"const lv_image_dsc_t icon_{name} = {{\n"
          f"  .header = {{ .magic = LV_IMAGE_HEADER_MAGIC, .cf = LV_COLOR_FORMAT_A8,\n"
          f"               .flags = 0, .w = {w}, .h = {h}, .stride = {w} }},\n"
          f"  .data_size = {w*h}, .data = {name}_map,\n}};\n")
    return cdef

with open(OUT_C,"w") as c:
    c.write('#include "icons.h"\n\n')
    for n,im in ICONS.items(): c.write(emit(n,im)+"\n")
with open(OUT_H,"w") as h:
    h.write("#pragma once\n#include \"lvgl.h\"\n\n")
    for n in ICONS: h.write(f"extern const lv_image_dsc_t icon_{n};\n")
print("generated",len(ICONS),"icons:",", ".join(ICONS))
