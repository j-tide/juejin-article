"""Generate a clearly synthetic checkout clip, not a recording of a real customer."""
import argparse
import json
import hashlib
import subprocess
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

p=argparse.ArgumentParser()
p.add_argument('--font',required=True,help='CJK font file; no font redistributed')
a=p.parse_args()
folder=Path(__file__).resolve().parents[1]/'fixtures/media'
folder.mkdir(exist_ok=True)
font=ImageFont.truetype(a.font,32);small=ImageFont.truetype(a.font,23)
def frame(notice):
    image=Image.new('RGB',(720,960),'#f4f5f6');d=ImageDraw.Draw(image)
    d.text((42,35),'合成演示 · 茶饮结算',font=font,fill='#18232c')
    d.rounded_rectangle((30,110,690,680),radius=20,fill='white')
    for y,text in [(145,'水果茶 · 少冰'),(220,'两杯饮品   ￥32.00'),(310,'优惠券   待核对'),(415,'应付金额   ￥32.00')]:
        d.text((60,y),text,font=font,fill='#24323d')
    d.rounded_rectangle((50,530,670,610),radius=12,fill='#e7a744')
    d.text((280,548),'确认支付',font=font,fill='#14202a')
    if notice:
        d.rounded_rectangle((70,700,650,785),radius=12,fill='#24323d')
        d.text((116,724),'价格已更新，请重新确认',font=small,fill='white')
    d.text((45,865),'测试夹具，无真实品牌和顾客数据',font=small,fill='#64707b')
    return image
with tempfile.TemporaryDirectory() as temp:
    for i in range(30):
        frame(11<=i<14).save(Path(temp)/f'{i:03d}.png')
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-framerate','10','-i',str(Path(temp)/'%03d.png'),
                    '-c:v','libx264','-pix_fmt','yuv420p','-an',str(folder/'checkout.mp4')],check=True)
frame(True).save(folder/'notice.png')
assets=[]
for name,kind in [('checkout.mp4','video'),('notice.png','image')]:
    assets.append({'id':name.split('.')[0],'file':name,'kind':kind,'brand':'demo-brand','store':'store-001',
                   'sha256':hashlib.sha256((folder/name).read_bytes()).hexdigest(),'message_id':'synthetic-media-message',
                   'observed_at':'2026-09-18T14:10:00+08:00'})
(folder/'catalog.json').write_text(json.dumps({'synthetic':True,'assets':assets},ensure_ascii=False,indent=2)+'\n')
(folder/'ground-truth.json').write_text(json.dumps({'synthetic':True,'duration_s':3,'fps':10,'notice_interval_s':[1.1,1.4],
    'notice':'价格已更新，请重新确认','audio':False,'purpose':'Known input for sampling experiment; not passed into OCR.'},ensure_ascii=False,indent=2)+'\n')
