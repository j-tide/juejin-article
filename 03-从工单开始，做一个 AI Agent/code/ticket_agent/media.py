"""Bounded local attachment inspection with frame timestamps and native OCR."""
import argparse
import hashlib
import json
import math
import subprocess
import tempfile
from pathlib import Path
from .domain import Scope


class MediaError(Exception):pass


def command(arguments):
    try:
        result=subprocess.run(arguments,capture_output=True,text=True,timeout=30,check=True)
        return result.stdout
    except (OSError,subprocess.SubprocessError):
        raise MediaError('dependency_or_processing_failure') from None


def sample_frames(timestamps,fps,start=0,end=None,budget=40):
    if not all(math.isfinite(x) for x in [fps,start,*timestamps]) or not 0<fps<=10 or start<0:
        raise ValueError('invalid sample parameters')
    if end is not None and (not math.isfinite(end) or end<=start):raise ValueError('invalid end')
    picked=[];target=start
    for index,stamp in enumerate(timestamps):
        if stamp<start or (end is not None and stamp>=end):continue
        if stamp+1e-7>=target:
            picked.append((index,stamp));target=stamp+1/fps
    if len(picked)>budget:raise MediaError('frame_budget_exceeded')
    return picked


def observe(lines,asset,frame_index,stamp,threshold=0.8):
    result=[]
    for index,line in enumerate(lines):
        if not isinstance(line.get('text'),str) or not isinstance(line.get('confidence'),(int,float)):
            raise MediaError('invalid_ocr_output')
        certainty='recognized_text' if line['confidence']>=threshold else 'uncertain_text'
        where='截图' if stamp is None else f'视频 {stamp:.3f}s 帧 {frame_index}'
        quote=f"{where} OCR 候选文字：{line['text']}；标记={certainty}，不是后台执行证据"
        result.append({'id':f"media:{asset['id']}:{frame_index}:{index}",'source':f"attachment://{asset['id']}#frame={frame_index}",
            'observed_at':asset['observed_at'],'event_at':None,'media_time_s':stamp,'frame_index':frame_index,
            'quote':quote,'text':line['text'],'confidence':line['confidence'],'box_normalized_bottom_left':line['box'],
            'certainty':certainty,'asset_sha256':asset['sha256'],'message_id':asset['message_id']})
    return result


class MediaReader:
    def __init__(self,ocr_binary,folder=None):
        self.ocr_binary=str(Path(ocr_binary).resolve())
        self.folder=(Path(folder) if folder else Path(__file__).resolve().parents[1]/'fixtures/media').resolve()
        self.assets={x['id']:x for x in json.loads((self.folder/'catalog.json').read_text())['assets']}

    def inspect(self,asset_id,scope,fps=1,start=0,end=None):
        asset=self.assets.get(asset_id)
        if not asset or (asset['brand'],asset['store'])!=(scope.brand,scope.store):
            return {'status':'not_found','evidence':[]}
        path=(self.folder/asset['file']).resolve()
        if self.folder not in path.parents or not path.is_file():return {'status':'attachment_unavailable','evidence':[]}
        if path.stat().st_size>20_000_000:return {'status':'asset_too_large','evidence':[]}
        if hashlib.sha256(path.read_bytes()).hexdigest()!=asset['sha256']:return {'status':'hash_mismatch','evidence':[]}
        try:
            evidence=[]
            if asset['kind']=='image':
                output=json.loads(command([self.ocr_binary,str(path)]))
                evidence=observe(output['lines'],asset,0,None)
                count=1;audio=False
            elif asset['kind']=='video':
                metadata=json.loads(command(['ffprobe','-v','error','-protocol_whitelist','file,pipe','-show_format','-show_streams','-of','json',str(path)]))
                duration=float(metadata['format']['duration'])
                video=next(x for x in metadata['streams'] if x['codec_type']=='video')
                if not math.isfinite(duration) or duration>20 or video['width']*video['height']>4_000_000:
                    return {'status':'media_budget_exceeded','evidence':[]}
                audio=any(x['codec_type']=='audio' for x in metadata['streams'])
                frames=json.loads(command(['ffprobe','-v','error','-protocol_whitelist','file,pipe','-select_streams','v:0',
                    '-show_entries','frame=best_effort_timestamp_time','-of','json',str(path)]))['frames']
                times=[float(f['best_effort_timestamp_time']) for f in frames]
                selected=sample_frames(times,fps,start,end)
                count=len(selected)
                if selected:
                    with tempfile.TemporaryDirectory(prefix='ticket-frames-') as temp:
                        selection='+'.join(f'eq(n,{i})' for i,t in selected)
                        command(['ffmpeg','-hide_banner','-loglevel','error','-protocol_whitelist','file,pipe','-i',str(path),
                                 '-vf',f'select={selection.replace(",",chr(92)+",")}','-fps_mode','vfr',str(Path(temp)/'%03d.png')])
                        extracted=sorted(Path(temp).glob('*.png'))
                        if len(extracted)!=len(selected):raise MediaError('frame_alignment_failed')
                        for image,(index,stamp) in zip(extracted,selected):
                            output=json.loads(command([self.ocr_binary,str(image)]))
                            evidence.extend(observe(output['lines'],asset,index,stamp))
            else:return {'status':'unsupported_media','evidence':[]}
            return {'status':'observed' if evidence else 'no_text_observed','evidence':evidence,
                    'sampled_frames':count,'audio_present':audio,'audio_transcribed':False,
                    'limitations':['OCR candidates only','unsampled intervals unknown','no backend evidence']}
        except (MediaError,ValueError,KeyError,StopIteration) as exc:
            return {'status':str(exc) if isinstance(exc,MediaError) else 'invalid_media_metadata','evidence':[]}


MEDIA_TOOL={'type':'function','function':{'name':'inspect_media','description':'查询已授权附件中匹配指定文字的 OCR 候选；低置信度、采样空白和后台状态均保持未知。',
    'parameters':{'type':'object','properties':{'query':{'type':'string','description':'要回查的画面文字片段，最多 80 字'}},'required':['query'],'additionalProperties':False}}}


def media_handler(reader,asset_id,fps=1,start=0,end=None):
    """Bind attachment and sampling budget outside the model's arguments."""
    def inspect(arguments,scope):
        if (not isinstance(arguments,dict) or set(arguments)!={'query'}
                or not isinstance(arguments['query'],str) or not 1<=len(arguments['query'])<=80):
            return {'status':'invalid_arguments','evidence':[]}
        result=reader.inspect(asset_id,scope,fps,start,end)
        if result['status'] not in ('observed','no_text_observed'):return result
        matched=[e for e in result['evidence'] if arguments['query'] in e['text']]
        return {'status':'matched_candidates' if matched else 'no_matching_text',
                'evidence':matched[:6],'sampled_frames':result['sampled_frames'],
                'truncated':len(matched)>6,'limitations':result['limitations']}
    return inspect


def main():
    p=argparse.ArgumentParser(description='本地附件 OCR；不下载飞书文件，不调用云模型')
    p.add_argument('asset');p.add_argument('--ocr',required=True)
    p.add_argument('--fps',type=float,default=1);p.add_argument('--start',type=float,default=0);p.add_argument('--end',type=float)
    p.add_argument('--brand',default='demo-brand');p.add_argument('--store',default='store-001')
    a=p.parse_args();result=MediaReader(a.ocr).inspect(a.asset,Scope(a.brand,a.store),a.fps,a.start,a.end)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['status'] in ('observed','no_text_observed') else 1


if __name__=='__main__':raise SystemExit(main())
