#!/usr/bin/env python3
"""Chrome native messaging adapter for the installed Resolve script."""
import fcntl, json, os, struct, sys, time, uuid

MAX_MESSAGE = 1024 * 1024

def respond(obj):
    data=json.dumps(obj,ensure_ascii=False).encode('utf-8')
    sys.stdout.buffer.write(struct.pack('<I',len(data))+data)
    sys.stdout.buffer.flush()

def run():
    header=sys.stdin.buffer.read(4)
    if len(header)!=4: return
    size=struct.unpack('<I',header)[0]
    if not 0<size<=MAX_MESSAGE:raise ValueError('消息大小无效')
    payload=sys.stdin.buffer.read(size)
    if len(payload)!=size:raise ValueError('消息不完整')
    message=json.loads(payload.decode('utf-8'))
    data=message.get('data') if isinstance(message,dict) else None
    if message.get('kind')!='open' or not isinstance(data,dict) or data.get('schema')!='minutes-roughcut/v1' or data.get('unit')!='ms' or not isinstance(data.get('ranges'),list):raise ValueError('剪辑指令格式无效')
    directory=os.path.expanduser('~/Library/Application Support/Minutes Roughcut')
    os.makedirs(directory,exist_ok=True)
    with open(os.path.join(directory,'window.lock'),'a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            request_id=uuid.uuid4().hex
            request_path=os.path.join(directory,'incoming-'+request_id+'.json')
            ack_path=os.path.join(directory,'ack-'+request_id)
            temporary=request_path+'.tmp'
            try:
                fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'w',encoding='utf-8') as out:json.dump({'id':request_id,'data':data},out,ensure_ascii=False)
                os.replace(temporary,request_path)
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    if os.path.exists(ack_path):
                        with open(ack_path,encoding='utf-8') as reply:result=json.load(reply)
                        if not result.get('ok'):raise ValueError(result.get('error','无法载入划稿'))
                        respond({'ok':True});return
                    time.sleep(0.1)
                raise ValueError('当前窗口尚未启用覆盖功能，请关闭后重新打开一次')
            finally:
                for path in (temporary,request_path,ack_path):
                    try:os.unlink(path)
                    except FileNotFoundError:pass
        fcntl.flock(lock,fcntl.LOCK_UN)
    import DaVinciResolveScript as dvr
    resolve=dvr.scriptapp('Resolve')
    if not resolve or not resolve.GetProjectManager().GetCurrentProject(): raise ValueError('请先打开达芬奇项目')
    script=os.path.expanduser('~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/Minutes Roughcut.py')
    if not os.path.isfile(script):raise ValueError('未找到达芬奇助手')
    resolve.Fusion().RunScript(script,{'minutes_payload':json.dumps(data,ensure_ascii=False)})
    respond({'ok':True})

if __name__=='__main__':
    try:run()
    except Exception as exc:respond({'ok':False,'error':str(exc)})
