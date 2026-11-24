# -*- coding: utf-8 -*-
"""Experimental Resolve-native rough cut. No interchange file is used."""
import json
from fractions import Fraction

from math import isfinite


def normalize(items, padding_ms=0, gap_ms=0, duration_ms=None):
    if not isinstance(items, list) or not items:
        raise ValueError('没有剪辑区间。')
    for n in (padding_ms, gap_ms):
        if isinstance(n, bool) or not isinstance(n, (int, float)) or not isfinite(n) or n < 0:
            raise ValueError('预留时间和合并间隔必须为非负数。')
    if duration_ms is not None and (not isinstance(duration_ms, (int,float)) or isinstance(duration_ms,bool) or not isfinite(duration_ms) or duration_ms <= 0):
        raise ValueError('来源时长无效。')
    parsed=[]
    for i, item in enumerate(items, 1):
        if not isinstance(item, dict): raise ValueError('第 %d 个区间无效。' % i)
        a,b=item.get('start_ms'),item.get('end_ms')
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not isfinite(v) for v in (a,b)) or a < 0 or b <= a:
            raise ValueError('第 %d 个区间无效。' % i)
        if duration_ms is not None and b > duration_ms:
            raise ValueError('第 %d 个区间超出来源时长。' % i)
        start=max(0,a-padding_ms)
        end=min(duration_ms,b+padding_ms) if duration_ms is not None else b+padding_ms
        parsed.append((start,end))
    merged=[]
    for start,end in sorted(parsed):
        if merged and start <= merged[-1][1]+gap_ms:
            merged[-1]=(merged[-1][0],max(end,merged[-1][1]))
        else: merged.append((start,end))
    return merged


def frames(ranges, fps, total_frames):
    rates={'23.976':Fraction(24000,1001),'23.98':Fraction(24000,1001),'29.97':Fraction(30000,1001),'59.94':Fraction(60000,1001),'119.88':Fraction(120000,1001)}
    try: rate=rates.get(str(fps)) or Fraction(str(fps))
    except (ValueError,ZeroDivisionError): raise ValueError('帧率无效。')
    if rate<=0 or total_frames<=0: raise ValueError('帧率或时长无效。')
    result=[]
    for a,b in ranges:
        x=Fraction(str(a))*rate/1000; y=Fraction(str(b))*rate/1000
        start=(2*x.numerator+x.denominator)//(2*x.denominator)
        end=(2*y.numerator+y.denominator)//(2*y.denominator)
        if end>total_frames or start>=total_frames or end<=start: raise ValueError('区间超出时间线或不足一帧。')
        if result and start <= result[-1][1]: result[-1]=(result[-1][0],max(end,result[-1][1]))
        else: result.append((start,end))
    return result


def rate(value):
    special={'23.98':Fraction(24000,1001),'23.976':Fraction(24000,1001),'29.97':Fraction(30000,1001),'59.94':Fraction(60000,1001),'119.88':Fraction(120000,1001)}
    return special.get(str(value)) or Fraction(str(value))


def make_plan(timeline, data, padding=0, gap=0, full=False):
    if not isinstance(data, dict) or data.get('schema')!='minutes-roughcut/v1' or data.get('unit')!='ms':
        raise ValueError('不支持此 JSON 格式。')
    fps=rate(timeline.GetSetting('timelineFrameRate'))
    origin=timeline.GetStartFrame(); length=timeline.GetEndFrame()-origin
    ms=float(Fraction(length*1000,1)/fps)
    spans=frames(normalize(data.get('ranges'),padding,gap,ms),str(fps),length)
    tracks={}; clips=[]
    for typ in ('video','audio'):
        tracks[typ]=[]
        for index in range(1,timeline.GetTrackCount(typ)+1):
            subtype=timeline.GetTrackSubType(typ,index) if typ=='audio' else ''
            if typ=='audio' and subtype not in ('mono','stereo'):
                raise ValueError('暂不支持音轨格式：'+str(subtype))
            tracks[typ].append((timeline.GetTrackName(typ,index),subtype,timeline.GetIsTrackEnabled(typ,index)))
            for clip in timeline.GetItemListInTrack(typ,index) or []:
                start=clip.GetStart(True)-origin; end=clip.GetEnd(True)-origin
                if not full and not any(start < b and end > a for a,b in spans): continue
                media=clip.GetMediaPoolItem()
                if not media: raise ValueError('片段缺少素材引用：'+clip.GetName())
                if clip.GetType()!=typ: raise ValueError('暂不支持转场或生成器：'+clip.GetName())
                speed=clip.GetSpeed() or {}
                if abs(float(speed.get('Percentage',100))-100)>0.00001: raise ValueError('暂不支持变速片段：'+clip.GetName())
                try: ratio=rate(media.GetClipProperty('FPS'))/fps
                except Exception: raise ValueError('无法读取素材帧率：'+clip.GetName())
                if ratio <= 0: raise ValueError('素材帧率无效：'+clip.GetName())
                mapping=clip.GetSourceAudioChannelMapping() if typ=='audio' else None
                clips.append({'type':typ,'track':index,'clip':clip,'media':media,'start':start,'end':end,'ratio':float(ratio),'left':clip.GetLeftOffset(True),'mapping':mapping})
    return fps,spans,tracks,clips


def build(resolve, data, padding=0, gap=0, original=None, folder=None, name=None, color_only=False):
    project=resolve.GetProjectManager().GetCurrentProject()
    if not project: raise ValueError('请先打开项目。')
    original=original or project.GetCurrentTimeline()
    if not original: raise ValueError('请先打开来源时间线。')
    fps,spans,tracks,clips=make_plan(original,data,padding,gap,full=color_only)
    pool=project.GetMediaPool()
    name=name or original.GetName()+(' - 区间标色' if color_only else ' - 妙记粗剪')
    previous_folder=pool.GetCurrentFolder()
    if folder and not pool.SetCurrentFolder(folder): raise ValueError('无法选择目标媒体夹。')
    try: timeline=pool.CreateEmptyTimeline(name)
    finally:
        if folder: pool.SetCurrentFolder(previous_folder)
    if not timeline: raise ValueError('无法创建时间线，请检查名称及项目权限。')
    try:
        if not project.SetCurrentTimeline(timeline): raise RuntimeError('无法打开新时间线。')
        # New timelines inherit project FPS; mismatches would invalidate every cut.
        if rate(timeline.GetSetting('timelineFrameRate')) != fps: raise RuntimeError('新时间线与来源帧率不同，请统一项目和时间线帧率。')
        for typ in ('video','audio'):
            existing=timeline.GetTrackCount(typ)
            for _,subtype,_ in tracks[typ]:
                if not timeline.AddTrack(typ,subtype if typ=='audio' else None): raise RuntimeError('无法创建轨道。')
            for i in range(existing,0,-1):
                if not timeline.DeleteTrack(typ,i): raise RuntimeError('无法设置轨道。')
            for index,(trackname,_,enabled) in enumerate(tracks[typ],1):
                timeline.SetTrackName(typ,index,trackname)
                if not timeline.SetTrackEnable(typ,index,enabled): raise RuntimeError('无法设置轨道状态。')
        base=timeline.GetStartFrame();cursor=0; count=0
        if color_only:
            end=original.GetEndFrame()-original.GetStartFrame(); parts=[]; pos=0
            for a,b in spans:
                if a>pos: parts.append((pos,a,False))
                parts.append((a,b,True));pos=b
            if pos<end: parts.append((pos,end,False))
        else: parts=[(a,b,False) for a,b in spans]
        for a,b,highlight in parts:
            for item in clips:
                x=max(a,item['start']); y=min(b,item['end'])
                if x>=y: continue
                ratio=item['ratio']; source=(item['left']+x-item['start'])*ratio
                added=pool.AppendToTimeline([{'mediaPoolItem':item['media'],'startFrame':source,'endFrame':source+(y-x)*ratio,'mediaType':1 if item['type']=='video' else 2,'trackIndex':item['track'],'recordFrame':base+cursor+x-a}]) or []
                if len(added)!=1: raise RuntimeError('无法添加片段：'+item['clip'].GetName())
                out=added[0]
                if highlight:
                    if not out.SetClipColor('Purple'): raise RuntimeError('无法设置片段颜色。')
                else:
                    color=item['clip'].GetClipColor()
                    if color: out.SetClipColor(color)
                if item['type']=='audio' and item['mapping'] and not out.SetSourceAudioChannelMapping(item['mapping']): raise RuntimeError('无法设置音频通道：'+item['clip'].GetName())
                if not out.SetClipEnabled(item['clip'].GetClipEnabled()): raise RuntimeError('无法设置片段状态。')
                expected=item['clip'].GetSourceStartTime()+(x-item['start'])/float(fps)
                if abs(out.GetSourceStartTime()-expected)>0.001: raise RuntimeError('源入点校验失败：'+item['clip'].GetName())
                count+=1
            cursor+=b-a
        if not count: raise RuntimeError('所选区间没有可用片段。')
        return timeline,len(spans)
    except Exception as exc:
        project.SetCurrentTimeline(original)
        if not pool.DeleteTimelines([timeline]): raise RuntimeError(str(exc)+'；无法删除未完成的时间线。')
        raise



def _main(initial_data=None, initial_path=None):
    import os, traceback, threading, time
    fusion=resolve.Fusion()
    if initial_data is None:
        initial_path=fusion.RequestFile(os.path.expanduser('~/Downloads/'), 'JSON Files (*.json)')
        if not initial_path:return
        try:
            with open(initial_path,encoding='utf-8-sig') as stream: initial_data=json.load(stream)
        except Exception as exc: print('无法读取 JSON：'+str(exc));return
    ui=fusion.UIManager;dispatcher=bmd.UIDispatcher(ui)
    project=resolve.GetProjectManager().GetCurrentProject()
    if not project:print('请先打开项目。');return
    pool=project.GetMediaPool();current=project.GetCurrentTimeline()
    settings_path=os.path.expanduser('~/Library/Application Support/Minutes Roughcut/settings.json')
    try:
        with open(settings_path,encoding='utf-8') as f:settings=json.load(f)
        if not isinstance(settings,dict):settings={}
    except (OSError,ValueError):settings={}
    timelines=[project.GetTimelineByIndex(i) for i in range(1,project.GetTimelineCount()+1)]
    folders=[]
    def collect(f,parent=''):
        label=(parent+' / ' if parent else '')+f.GetName();folders.append((label,f))
        for child in f.GetSubFolderList() or []:collect(child,label)
    collect(pool.GetRootFolder())
    def folder_for(t):
        if not t:return 0
        item=t.GetMediaPoolItem();identity=item.GetUniqueId() if item else None
        for i,(_,f) in enumerate(folders):
            if any(c.GetUniqueId()==identity for c in (f.GetClipList() or [])):return i
        return 0
    default_path='Master / 03_Timeline / 01_合板及素材筛选 / 妙计粗剪'
    preferred_folder=next((i for i,(path,_) in enumerate(folders) if path==default_path),None)
    def initial_folder(t):return preferred_folder if preferred_folder is not None else folder_for(t)
    state={'data':initial_data,'folder':initial_folder(current),'mode':'cut','warning':False,'busy':False,'picker':None,'reopen':False,'diagnostic':'','advanced':False}
    css="""QWidget{background:#19191D;color:#E8E8ED;font-size:13px;}
    QLineEdit,QComboBox{background:#25252B;color:#E8E8ED;border:1px solid #414149;border-radius:6px;padding:3px 8px;}
    QPushButton{background:#25252B;color:#E8E8ED;border:1px solid #414149;border-radius:6px;padding:6px 10px;}
    QPushButton:hover{border-color:#B69AEE;} QPushButton:disabled{color:#777780;}
    QPushButton#modeCut{background:#B69AEE;color:#241a30;border:0;font-weight:bold;}
    QPushButton#modeCut:disabled{background:#39333f;color:#8e8497;}
    QLabel#secondary{color:#aaaab4;font-size:12px;} QLabel#risk{color:#e0bd79;font-size:12px;}
    """
    row=lambda label,widget:ui.HGroup({'Weight':0,'Spacing':8,'MinimumSize':[0,42]},[ui.Label({'Text':label,'MinimumSize':[100,34],'MaximumSize':[100,16777215]}),widget])
    win=dispatcher.AddWindow({'ID':'minutesRoughcut','WindowTitle':'Minutes Roughcut','Geometry':[200,120,500,650],'MinimumSize':[480,600],'Margin':20,'StyleSheet':css},ui.VGroup({'Spacing':8},[
      ui.HGroup({'Weight':0},[ui.Label({'ID':'file','Text':'','Weight':1,'WordWrap':True}),ui.Button({'ID':'changeFile','Text':'更换文件','MinimumSize':[80,34]})]),
      ui.HGroup({'Weight':0,'Spacing':12},[ui.Label({'ID':'raw','Text':'原始时长\n—','Weight':1,'WordWrap':True}),ui.Label({'ID':'total','Text':'保留时长\n—','Weight':1,'WordWrap':True}),ui.Label({'ID':'count','Text':'保留区间\n—','Weight':1,'WordWrap':True})]),
      ui.Label({'ID':'preview','Text':'','Weight':0,'MinimumSize':[0,18]}),ui.Label({'ID':'ratio','Text':'','Weight':0}),
      row('当前时间线',ui.ComboBox({'ID':'timeline','MinimumSize':[0,34]})),ui.Label({'ID':'durationStatus','Text':'','Weight':0,'WordWrap':True}),
      ui.CheckBox({'ID':'confirm','Text':'已确认来源与时长','Visible':False,'Weight':0}),
      row('新时间线名称',ui.LineEdit({'ID':'name','MinimumSize':[0,34]})),
      ui.HGroup({'Weight':0,'Spacing':8},[ui.Label({'Text':'保存至媒体夹','MinimumSize':[100,30],'MaximumSize':[100,30]}),ui.Label({'ID':'folder','Text':'','Weight':1,'WordWrap':False}),ui.Button({'ID':'pick','Text':'…','MinimumSize':[38,34]})]),
      ui.Button({'ID':'advanced','Text':'高级设置  ▸','Weight':0,'MinimumSize':[0,30]}),
      ui.HGroup({'ID':'advancedRow','Weight':0,'Visible':False,'Spacing':8,'MaximumSize':[16777215,0]},[ui.Label({'Text':'前后预留（秒）'}),ui.LineEdit({'ID':'pad','Text':'0','MinimumSize':[55,30]}),ui.Label({'Text':'合并间隔（秒）'}),ui.LineEdit({'ID':'gap','Text':'0','MinimumSize':[55,30]})]),
      ui.Label({'ID':'risk','Text':'新时间线不保留部分特效、调色和链接。','WordWrap':True,'Weight':0,'MaximumSize':[16777215,28]}),
      ui.Label({'ID':'status','Text':'','WordWrap':True,'Weight':0,'MaximumSize':[16777215,32]}),
      ui.Button({'ID':'copy','Text':'复制诊断','Visible':False,'Weight':0,'MaximumSize':[16777215,0]}),
      ui.HGroup({'Weight':1,'Spacing':8,'MinimumSize':[0,55]},[ui.Button({'ID':'modeMark','Text':'仅标注','MinimumSize':[0,30],'MaximumSize':[16777215,32]}),ui.Button({'ID':'modeCut','Text':'自动粗剪','MinimumSize':[0,30],'MaximumSize':[16777215,32],'StyleSheet':'QPushButton { background:#B69AEE; color:#241a30; border:1px solid #B69AEE; border-radius:6px; font-weight:bold; padding:6px 10px; } QPushButton:disabled { background:#39333f; color:#8e8497; border-color:#39333f; }'})]),ui.Label({'Text':'原始时间线不会被修改','Weight':0,'StyleSheet':'color:#aaaab4;font-size:12px;'})]))
    it=win.GetItems()
    for t in timelines:it['timeline'].AddItem(t.GetName())
    if current:
        for i,t in enumerate(timelines):
            if t.GetUniqueId()==current.GetUniqueId():it['timeline'].CurrentIndex=i;break
    def selected():
        i=int(it['timeline'].CurrentIndex);return timelines[i] if 0<=i<len(timelines) else None
    def sec_label(ms):
        n=int(ms//1000);return '%d:%02d:%02d'%(n//3600,(n//60)%60,n%60)
    def values():
        try:
            pad=float(it['pad'].Text);gap=float(it['gap'].Text)
            if not isfinite(pad) or not isfinite(gap) or pad<0 or gap<0:raise ValueError()
            return round(pad*1000),round(gap*1000)
        except Exception:raise ValueError('请输入非负的预留时间和合并间隔。')
    def show_folder():
        text=folders[state['folder']][0] if folders else '无可用媒体夹'
        it['folder'].Text=text if len(text)<=22 else '… / '+text.split(' / ')[-2]+' / '+text.split(' / ')[-1] if ' / ' in text else text;it['folder'].ToolTip=text
    def sync_buttons():
        ok=bool(state['data']) and selected() and not state['busy'] and (not state['warning'] or it['confirm'].Checked)
        it['modeMark'].Enabled=bool(ok);it['modeCut'].Enabled=bool(ok)
    def refresh(reset_confirm=False):
        try:
            d=state['data'];t=selected()
            if not isinstance(d,dict) or d.get('schema')!='minutes-roughcut/v1' or d.get('unit')!='ms':raise ValueError('不支持此 JSON 格式。')
            pad,gap=values();duration=d.get('duration_ms')
            duration=duration if isinstance(duration,(int,float)) and not isinstance(duration,bool) and isfinite(duration) and duration>0 else None
            spans=normalize(d.get('ranges'),pad,gap,duration)
            total=sum(b-a for a,b in spans)
            it['raw'].Text='原始时长\n'+(sec_label(duration) if duration else '未知');it['total'].Text='保留时长\n'+sec_label(total);it['count'].Text='保留区间\n%d 段'%len(spans)
            if duration:
                pieces=[];cursor=0
                for a,b in spans:
                    if a>cursor:pieces.append(('<td width="%s%%" bgcolor="#414149"> </td>'%((a-cursor)/duration*100)))
                    pieces.append('<td width="%s%%" bgcolor="#B69AEE"> </td>'%((b-a)/duration*100));cursor=b
                if cursor<duration:pieces.append('<td width="%s%%" bgcolor="#414149"> </td>'%((duration-cursor)/duration*100))
                it['preview'].Text='<table cellspacing="0" cellpadding="0" width="100%"><tr height="10">'+''.join(pieces)+'</tr></table>'
                it['ratio'].Text='保留 %.1f%%'%(100*total/duration)
            else:it['preview'].Text='';it['ratio'].Text='原始时长未知'
            if not t:state['warning']=True;it['durationStatus'].Text='请先选择时间线。';it['confirm'].Visible=False;it['modeMark'].Enabled=False;it['modeCut'].Enabled=False;return
            fps=rate(t.GetSetting('timelineFrameRate'));length=float(Fraction((t.GetEndFrame()-t.GetStartFrame())*1000,1)/fps)
            state['warning']=duration is None or abs(duration-length)>1000
            if duration is None:it['durationStatus'].Text='妙记总时长未知，请确认来源及起点。'
            elif state['warning']:it['durationStatus'].Text='时长相差 %s，请确认来源及起点。'%sec_label(abs(duration-length))
            else:it['durationStatus'].Text='时长匹配 · 请确认时间起点一致';it['durationStatus'].StyleSheet='color:#9bc5a5;'
            if state['warning']:it['durationStatus'].StyleSheet='color:#e0bd79;'
            if reset_confirm:it['confirm'].Checked=False
            it['confirm'].Visible=state['warning'];it['status'].Text='';sync_buttons()
        except Exception as exc:it['status'].Text=str(exc);it['modeMark'].Enabled=False;it['modeCut'].Enabled=False
    def source_changed(ev):
        t=selected();state['folder']=initial_folder(t);show_folder()
        it['name'].Text=(t.GetName()+' - 妙记粗剪') if t else '';refresh(True)
    def choose_folder(ev):
        if state['picker']:state['picker'].Show();state['picker'].Raise();return
        dialog=dispatcher.AddWindow({'ID':'folderPicker','WindowTitle':'选择媒体夹','Geometry':[240,180,520,430],'Margin':16},ui.VGroup({'Spacing':8},[ui.Tree({'ID':'list','ColumnCount':1,'HeaderHidden':True}),ui.Label({'ID':'message','Text':'请选择媒体夹','Weight':0}),ui.HGroup({'Weight':0},[ui.Button({'ID':'cancel','Text':'取消'}),ui.Button({'ID':'accept','Text':'选择'})])]))
        state['picker']=dialog;di=dialog.GetItems();tree=di['list']
        for label,_ in folders:
            item=tree.NewItem();item.Text[0]=label;tree.AddTopLevelItem(item)
        def accept(ev):
            selected_items=tree.SelectedItems() or [];selected_items=list(selected_items.values()) if isinstance(selected_items,dict) else list(selected_items)
            if not selected_items:di['message'].Text='请选择媒体夹';return
            name=selected_items[0].Text[0];match=next((i for i,(x,_) in enumerate(folders) if x==name),None)
            if match is None:di['message'].Text='无法读取媒体夹';return
            state['folder']=match;show_folder();dialog.Hide()
        def cancel(ev):dialog.Hide()
        dialog.On.folderPicker.Close=cancel;dialog.On.cancel.Clicked=cancel;dialog.On.accept.Clicked=accept;dialog.Show();dialog.Raise()
    def run(ev):
        try:
            active=resolve.GetProjectManager().GetCurrentProject();t=selected()
            if not active or active.GetUniqueId()!=project.GetUniqueId():raise ValueError('项目已更改，请重新打开助手。')
            if not t:raise ValueError('请选择时间线。')
            if state['warning'] and not it['confirm'].Checked:raise ValueError('请确认来源与时长。')
            name=it['name'].Text.strip()
            if not name:raise ValueError('请输入新时间线名称。')
            pad,gap=values();state['busy']=True;sync_buttons();it['status'].Text='正在处理…'
            new,count=build(resolve,state['data'],padding=pad,gap=gap,original=t,folder=folders[state['folder']][1],name=name,color_only=state['mode']=='mark')
            it['status'].Text='已生成：%s（%d 段）'%(new.GetName(),count);it['copy'].Visible=False;it['copy'].MaximumSize=[16777215,0]
            try:
                os.makedirs(os.path.dirname(settings_path),exist_ok=True)
                with open(settings_path,'w',encoding='utf-8') as f:json.dump({'mode':state['mode'],'pad':it['pad'].Text,'gap':it['gap'].Text},f)
            except OSError:pass
        except Exception as exc:
            state['diagnostic']=traceback.format_exc();it['status'].Text='无法生成：'+str(exc);it['copy'].MaximumSize=[16777215,34];it['copy'].Visible=True
        finally:state['busy']=False;sync_buttons()
    def change_file(ev):state['reopen']=True;dispatcher.ExitLoop()
    def close(ev):
        if state['picker']:state['picker'].Hide()
        dispatcher.ExitLoop()
    def advanced(ev):
        state['advanced']=not state['advanced'];it['advancedRow'].MaximumSize=[16777215,40 if state['advanced'] else 0];it['advancedRow'].Visible=state['advanced'];it['advanced'].Text='高级设置  ▾' if state['advanced'] else '高级设置  ▸';win.RecalcLayout()
    def copy(ev):
        try:
            fusion.SetClipboard(state['diagnostic']);it['status'].Text='诊断信息已复制。'
        except Exception:
            print(state['diagnostic']);it['status'].Text='诊断信息已输出到控制台。'
    it['file'].Text=(initial_path.replace('\\','/').split('/')[-1] if initial_path else str(initial_data.get('title') or '浏览器划稿'))
    it['name'].Text=(current.GetName()+' - 妙记粗剪') if current else ''
    for key in ('pad','gap'):
        value=settings.get(key,'0')
        if isinstance(value,(str,int,float)):it[key].Text=str(value)
    show_folder();refresh(True)
    win.On.minutesRoughcut.Close=close;win.On.timeline.CurrentIndexChanged=source_changed
    win.On.modeCut.Clicked=lambda e:(state.update(mode='cut'),run(e));win.On.modeMark.Clicked=lambda e:(state.update(mode='mark'),run(e))
    win.On.pick.Clicked=choose_folder;win.On.advanced.Clicked=advanced;win.On.pad.TextChanged=lambda e:refresh();win.On.gap.TextChanged=lambda e:refresh()
    win.On.confirm.Clicked=lambda e:sync_buttons();win.On.changeFile.Clicked=change_file;win.On.copy.Clicked=copy
    # The worker only queues events; all Resolve/UI changes remain on the UI thread.
    mailbox=os.path.expanduser('~/Library/Application Support/Minutes Roughcut')
    stopped=threading.Event()
    def poll_incoming():
        pending=set()
        while not stopped.wait(0.2):
            try:
                names={name for name in os.listdir(mailbox) if name.startswith('incoming-') and name.endswith('.json')}
                pending.intersection_update(names)
                for name in sorted(names-pending):
                    pending.add(name)
                    win.QueueEvent('Incoming',{'filename':name})
            except Exception as exc:print('接收划稿失败：'+str(exc))
    def receive(ev):
        name=ev.get('filename','')
        if not name.startswith('incoming-') or not name.endswith('.json') or '/' in name:return
        path=os.path.join(mailbox,name);request_id=name[len('incoming-'):-len('.json')]
        ack=os.path.join(mailbox,'ack-'+request_id)
        try:
            with open(path,encoding='utf-8') as stream:request=json.load(stream)
            data=request['data']
            if request.get('id')!=request_id or data.get('schema')!='minutes-roughcut/v1' or data.get('unit')!='ms':raise ValueError('划稿格式无效')
            normalize(data.get('ranges'),0,0,None)
            state['data']=data;it['file'].Text=str(data.get('title') or '浏览器划稿')
            refresh(True);win.Show();win.Raise()
            result={'ok':True}
        except Exception as exc:result={'ok':False,'error':str(exc)}
        try:
            with open(ack,'w',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False)
        except OSError as exc:print('接收状态写入失败：'+str(exc))
    win.On.minutesRoughcut.Incoming=receive
    worker=threading.Thread(target=poll_incoming,daemon=True);worker.start()
    try:win.Show();dispatcher.RunLoop()
    finally:stopped.set();worker.join(timeout=1);win.Hide()

    if state['reopen']:_main()

def main(initial_data=None, initial_path=None):
    import fcntl, os
    directory=os.path.expanduser('~/Library/Application Support/Minutes Roughcut')
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory,'window.lock'),'a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Minutes Roughcut 已打开，请先使用或关闭现有窗口。')
            return
        try:
            _main(initial_data,initial_path)
        finally:
            fcntl.flock(lock,fcntl.LOCK_UN)

if __name__=='__main__':
    try:payload=globals().get('minutes_payload');data=json.loads(payload) if payload else None
    except Exception as exc:print('无法读取浏览器数据：'+str(exc));data=None
    main(data)
