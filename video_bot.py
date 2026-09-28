import os, re, json, time, html, shutil, subprocess, asyncio
from pathlib import Path
import requests, trafilatura
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import edge_tts
from PIL import Image
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

BASE=Path(__file__).resolve().parent; WORK=BASE/'work'; WORK.mkdir(exist_ok=True)
CRED=BASE/'credentials.json'; TOKEN=BASE/'token.json'; load_dotenv(BASE/'.env')
KEY=os.getenv('OPENROUTER_API_KEY','').strip(); MODEL='openrouter/free'; VOICE='ar-SA-HamedNeural'
SCOPES=['https://www.googleapis.com/auth/spreadsheets','https://www.googleapis.com/auth/drive']
SHEET='News'; SS='title ai payton'; ROOT='AI NEWS VIDEOS'; VIDEOS='VIDEOS'; AUDIO='AUDIO'
s=requests.Session(); s.headers['User-Agent']='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/154 Safari/537.36'

def auth():
    if not CRED.exists(): raise RuntimeError('credentials.json not found beside video_bot.py')
    c=Credentials.from_authorized_user_file(str(TOKEN),SCOPES) if TOKEN.exists() else None
    if not c or not c.valid:
        if c and c.expired and c.refresh_token: c.refresh(Request())
        else: c=InstalledAppFlow.from_client_secrets_file(str(CRED),SCOPES).run_local_server(port=0)
        TOKEN.write_text(c.to_json(),encoding='utf-8')
    return build('drive','v3',credentials=c), build('sheets','v4',credentials=c)

def folder(drive,name,parent=None):
    q=f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent:q+=f" and '{parent}' in parents"
    x=drive.files().list(q=q,spaces='drive',fields='files(id,name)').execute().get('files',[])
    return x[0]['id'] if x else None

def setup_drive(d):
    root=folder(d,ROOT)
    if not root: raise RuntimeError(f'Missing Drive folder: {ROOT}')
    vf=folder(d,VIDEOS,root); af=folder(d,AUDIO,root)
    if not vf or not af: raise RuntimeError('Missing VIDEOS or AUDIO inside AI NEWS VIDEOS')
    return vf,af

def spreadsheet(d):
    q=f"name='{SS}' and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false"
    x=d.files().list(q=q,spaces='drive',fields='files(id,name)').execute().get('files',[])
    if not x: raise RuntimeError(f'Missing spreadsheet: {SS}')
    return x[0]['id']

def rows(g,ssid):
    return g.spreadsheets().values().get(spreadsheetId=ssid,range=f'{SHEET}!A:O').execute().get('values',[])

def cell(g,ssid,r,c,v):
    col=chr(64+c); g.spreadsheets().values().update(spreadsheetId=ssid,range=f'{SHEET}!{col}{r}',valueInputOption='RAW',body={'values':[[v]]}).execute()

def article(url):
    r=s.get(html.unescape(url),timeout=35,allow_redirects=True); r.raise_for_status()
    text=trafilatura.extract(r.text,include_comments=False,include_tables=False,favor_precision=True,deduplicate=True) or ''
    soup=BeautifulSoup(r.text,'html.parser'); imgs=[]
    og=soup.find('meta',property='og:image')
    if og and og.get('content'): imgs.append(og['content'])
    for im in soup.find_all('img'):
        u=im.get('src') or im.get('data-src')
        if u and u.startswith('http'): imgs.append(u)
    return r.url,text,list(dict.fromkeys(imgs))[:10]

def ai(title,text):
    if not KEY: raise RuntimeError('Put OPENROUTER_API_KEY in .env')
    prompt=f'''Create a factual Arabic documentary package using ONLY the source below. Never invent dates, names, numbers, evidence, motives, quotes, police actions or outcomes. If unclear, omit it or say بحسب المصدر. Return JSON only with keys title,hook,script,scenes,description,hashtags. scenes must be a list of objects with scene,visual,narration. Script 500-800 Arabic words. No [اسم القناة].\n\nTITLE:\n{title}\n\nSOURCE:\n{text[:26000]}'''
    r=s.post('https://openrouter.ai/api/v1/chat/completions',headers={'Authorization':f'Bearer {KEY}','Content-Type':'application/json'},json={'model':MODEL,'messages':[{'role':'user','content':prompt}],'temperature':0.25},timeout=150)
    if r.status_code!=200: raise RuntimeError(f'OpenRouter {r.status_code}: {r.text[:1000]}')
    x=r.json()['choices'][0]['message']['content'].strip(); x=re.sub(r'^```json\s*','',x); x=re.sub(r'\s*```$','',x)
    return json.loads(x)

async def tts(text,out): await edge_tts.Communicate(text,VOICE).save(str(out))

def duration(audio):
    p=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',str(audio)],capture_output=True,text=True)
    try:return float(p.stdout.strip())
    except:return 60

def imgs(urls,dir):
    dir.mkdir(exist_ok=True); out=[]
    for i,u in enumerate(urls,1):
        try:
            r=s.get(u,timeout=20); typ=r.headers.get('content-type','')
            if r.status_code!=200 or 'image' not in typ: continue
            ext='.png' if 'png' in typ else '.jpg'; p=dir/f'{i}{ext}'; p.write_bytes(r.content)
            with Image.open(p) as im:
                if im.width>=400 and im.height>=250: out.append(p)
                else:p.unlink(missing_ok=True)
        except:pass
    return out

def video(pics,audio,out):
    if shutil.which('ffmpeg') is None: raise RuntimeError('FFmpeg not found. Install it and test: ffmpeg -version')
    d=duration(audio)
    if pics:
        each=max(3,d/len(pics)); c=out.parent/'concat.txt'
        with c.open('w',encoding='utf8') as f:
            for p in pics:f.write(f"file '{p.as_posix()}'\nduration {each}\n")
            f.write(f"file '{pics[-1].as_posix()}'\n")
        cmd=['ffmpeg','-y','-f','concat','-safe','0','-i',str(c),'-i',str(audio),'-t',str(d+.5),'-vf','scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,format=yuv420p','-c:v','libx264','-preset','veryfast','-c:a','aac','-b:a','192k','-shortest',str(out)]
    else:
        cmd=['ffmpeg','-y','-f','lavfi','-i','color=c=black:s=1920x1080:r=25','-i',str(audio),'-t',str(d+.5),'-vf','format=yuv420p','-c:v','libx264','-c:a','aac','-shortest',str(out)]
    p=subprocess.run(cmd,capture_output=True,text=True)
    if p.returncode: raise RuntimeError(p.stderr[-2000:])

def upload(d,p,parent,mime):
    x=d.files().create(body={'name':p.name,'parents':[parent]},media_body=MediaFileUpload(str(p),mimetype=mime,resumable=True),fields='id,webViewLink').execute()
    try:d.permissions().create(fileId=x['id'],body={'type':'anyone','role':'reader'}).execute()
    except:pass
    return x.get('webViewLink',f"https://drive.google.com/file/d/{x['id']}/view")

def main():
    d,g=auth(); ss=spreadsheet(d); vf,af=setup_drive(d); rr=rows(g,ss)
    target=None
    for n,row in enumerate(rr[1:],2):
        if len(row)>11 and row[11].strip().upper()=='PENDING':target=(n,row);break
    if not target: print('No PENDING rows.'); return
    n,row=target; title=row[1] if len(row)>1 else ''; url=row[2] if len(row)>2 else ''
    print('Processing row',n,title); cell(g,ss,n,12,'PROCESSING')
    try:
        final,text,urls=article(url)
        if len(text)<800: raise RuntimeError(f'Could not extract article text reliably ({len(text)} chars)')
        cell(g,ss,n,12,'SOURCE_OK'); print('Article chars:',len(text))
        a=ai(title,text); job=WORK/f'row_{n}'; job.mkdir(exist_ok=True)
        scenes=json.dumps(a['scenes'],ensure_ascii=False)
        vals=[a['title'],a['hook'],a['script'],scenes,a['description'],' '.join(a['hashtags']),'SCRIPT_DONE','','',time.strftime('%Y-%m-%d %H:%M:%S')]
        g.spreadsheets().values().update(spreadsheetId=ss,range=f'{SHEET}!F{n}:O{n}',valueInputOption='RAW',body={'values':[vals]}).execute()
        audio=job/'voice.mp3'; asyncio.run(tts(a['script'],audio)); cell(g,ss,n,12,'VOICE_DONE')
        pic=imgs(urls,job/'images'); mp4=job/f'video_{n}.mp4'; video(pic,audio,mp4); cell(g,ss,n,12,'VIDEO_DONE')
        link=upload(d,mp4,vf,'video/mp4'); upload(d,audio,af,'audio/mpeg')
        cell(g,ss,n,14,link); cell(g,ss,n,12,'UPLOADED'); cell(g,ss,n,13,''); cell(g,ss,n,15,time.strftime('%Y-%m-%d %H:%M:%S'))
        print('SUCCESS',link)
    except Exception as e:
        cell(g,ss,n,12,'ERROR'); cell(g,ss,n,13,str(e)[:1000]); print('ERROR:',e)

if __name__=='__main__':main()
