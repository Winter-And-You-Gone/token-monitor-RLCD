#!/usr/bin/env python3
from pathlib import Path
import re
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
src=(ROOT/'firmware/components/ui_app/icons.c').read_text(encoding='utf-8',errors='replace')
nums=[int(x) for x in re.findall(r'\d+',re.search(r'earth_map\[\]\s*=\s*\{([^}]*)\}',src).group(1))]
im=Image.new('L',(48,48)); im.putdata(nums)
# A8 alpha becomes black ink on white UI, so invert for screen preview.
Image.eval(im,lambda v:255-v).resize((384,384),Image.NEAREST).save(ROOT/'docs/preview/earth_existing_screen.png')
print('wrote docs/preview/earth_existing_screen.png')
