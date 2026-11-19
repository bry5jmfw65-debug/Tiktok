import os, uuid, json, secrets, subprocess, shutil, time
from pathlib import Path
from urllib.parse import urlencode
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
import httpx

ROOT=Path(os.getenv("STORAGE_ROOT","./data")).resolve()
UPLOADS=ROOT/"uploads"; CLIPS=ROOT/"clips"; META=ROOT/"jobs.json"
for p in (UPLOADS,CLIPS): p.mkdir(parents=True,exist_ok=True)
app=FastAPI(title="ViralClip AI", version="1.0.0")
security=HTTPBasic()
def auth(c: HTTPBasicCredentials=Depends(security)):
    u=os.getenv("APP_USERNAME","")
    p=os.getenv("APP_PASSWORD","")
    if not u or not p or not (secrets.compare_digest(c.username,u) and secrets.compare_digest(c.password,p)):
        from fastapi import status
        raise HTTPException(status_code=401,detail="Set APP_USERNAME and APP_PASSWORD in the hosting environment.",headers={"WWW-Authenticate":"Basic"})
    return c.username
def jobs():
    try: return json.loads(META.read_text())
    except: return []
def save_jobs(items): META.write_text(json.dumps(items,indent=2))
def run(cmd):
    r=subprocess.run(cmd,capture_output=True,text=True)
    if r.returncode: raise RuntimeError((r.stderr or r.stdout)[-1800:])
    return r.stdout.strip()
def duration(path):
    return float(run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(path)]))
@app.get("/api/health")
def health(): return {"ok":True,"app":"ViralClip AI"}
@app.get("/",response_class=HTMLResponse)
def home(user:str=Depends(auth)):
    return HTMLResponse(Path("static/index.html").read_text())
@app.get("/api/jobs")
def list_jobs(user:str=Depends(auth)): return jobs()
@app.post("/api/upload")
async def upload(file:UploadFile=File(...),user:str=Depends(auth)):
    ext=Path(file.filename or "video.mp4").suffix.lower()
    if ext not in (".mp4",".mov",".m4v",".webm",".mkv"): raise HTTPException(400,"Upload an MP4, MOV, WEBM or MKV video.")
    jid=uuid.uuid4().hex[:10]; dest=UPLOADS/f"{jid}{ext}"
    size=0
    with dest.open("wb") as out:
        while True:
            chunk=await file.read(1024*1024)
            if not chunk: break
            size+=len(chunk)
            if size>2*1024*1024*1024: dest.unlink(missing_ok=True); raise HTTPException(413,"Maximum upload size is 2 GB.")
            out.write(chunk)
    item={"id":jid,"source":file.filename,"path":str(dest),"status":"uploaded","created":int(time.time()),"clips":[]}
    data=jobs(); data.insert(0,item); save_jobs(data)
    return item
@app.post("/api/youtube")
def youtube(url:str=Form(...),user:str=Depends(auth)):
    if not any(x in url.lower() for x in ("youtube.com/","youtu.be/")): raise HTTPException(400,"Enter a YouTube URL.")
    jid=uuid.uuid4().hex[:10]; dest=UPLOADS/f"{jid}.mp4"
    try:
        run(["yt-dlp","--no-playlist","-f","bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b","--merge-output-format","mp4","-o",str(dest),url])
    except Exception as e: dest.unlink(missing_ok=True); raise HTTPException(400,"Could not fetch this video. Use only content you own or have permission to reuse. "+str(e))
    item={"id":jid,"source":url,"path":str(dest),"status":"uploaded","created":int(time.time()),"clips":[]}
    data=jobs(); data.insert(0,item); save_jobs(data); return item
@app.post("/api/jobs/{jid}/clips")
def make_clips(jid:str,clip_length:int=Form(35),user:str=Depends(auth)):
    if clip_length<15 or clip_length>60: raise HTTPException(400,"Clip length must be 15–60 seconds.")
    data=jobs(); item=next((x for x in data if x["id"]==jid),None)
    if not item: raise HTTPException(404,"Video not found.")
    src=Path(item["path"])
    if not src.exists(): raise HTTPException(410,"Source video is no longer stored.")
    try: total=duration(src)
    except Exception as e: raise HTTPException(400,"Could not read video: "+str(e))
    if total<clip_length: raise HTTPException(400,"Video is shorter than the selected clip length.")
    # Spread up to five candidate windows across the source. These are candidate excerpts, not a guarantee of virality.
    count=min(5,max(1,int(total//clip_length)))
    starts=sorted(set(int((total-clip_length)*i/max(1,count-1)) for i in range(count)))
    clips=[]
    for n,start in enumerate(starts,1):
        out=CLIPS/f"{jid}-{n}.mp4"
        try:
            run(["ffmpeg","-y","-ss",str(start),"-i",str(src),"-t",str(clip_length),"-vf","scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920","-c:v","libx264","-preset","veryfast","-crf","23","-c:a","aac","-b:a","128k","-movflags","+faststart",str(out)])
        except Exception as e: raise HTTPException(500,"Clip rendering failed: "+str(e))
        clips.append({"name":out.name,"url":f"/clips/{out.name}","start_seconds":start,"duration":clip_length})
    item["clips"]=clips; item["status"]="clips_ready"; save_jobs(data)
    return {"id":jid,"clips":clips,"note":"Candidate windows are evenly sampled. Review them before posting; no tool can guarantee virality."}
app.mount("/clips",StaticFiles(directory=str(CLIPS)),name="clips")
@app.get("/api/tiktok/connect")
def tiktok_connect(user:str=Depends(auth)):
    key=os.getenv("TIKTOK_CLIENT_KEY"); redirect=os.getenv("TIKTOK_REDIRECT_URI")
    if not key or not redirect: raise HTTPException(400,"Add TIKTOK_CLIENT_KEY and TIKTOK_REDIRECT_URI in Render first.")
    state=secrets.token_urlsafe(24); (ROOT/"oauth_state").write_text(state)
    q=urlencode({"client_key":key,"response_type":"code","scope":"user.info.basic,video.publish","redirect_uri":redirect,"state":state})
    return RedirectResponse("https://www.tiktok.com/v2/auth/authorize/?"+q)
@app.get("/oauth/tiktok/callback")
async def tiktok_callback(code:str="",state:str="",error:str=""):
    expected=(ROOT/"oauth_state").read_text() if (ROOT/"oauth_state").exists() else ""
    if error: return HTMLResponse("TikTok authorization was cancelled: "+error,400)
    if not expected or not secrets.compare_digest(state,expected): raise HTTPException(400,"Invalid OAuth state.")
    async with httpx.AsyncClient(timeout=30) as client:
        r=await client.post("https://open.tiktokapis.com/v2/oauth/token/",data={"client_key":os.getenv("TIKTOK_CLIENT_KEY"),"client_secret":os.getenv("TIKTOK_CLIENT_SECRET"),"code":code,"grant_type":"authorization_code","redirect_uri":os.getenv("TIKTOK_REDIRECT_URI")},headers={"Content-Type":"application/x-www-form-urlencoded"})
    if r.status_code>=300: raise HTTPException(400,"TikTok token exchange failed: "+r.text[:500])
    (ROOT/"tiktok_token.json").write_text(json.dumps(r.json()))
    return HTMLResponse("<h2>TikTok connected</h2><p>Return to ViralClip AI and select a clip to publish.</p><a href='/'>Open app</a>")
@app.post("/api/tiktok/publish")
async def tiktok_publish(filename:str=Form(...),caption:str=Form(""),privacy:str=Form("SELF_ONLY"),user:str=Depends(auth)):
    tokenfile=ROOT/"tiktok_token.json"
    if not tokenfile.exists(): raise HTTPException(400,"Connect TikTok first.")
    if privacy not in ("PUBLIC_TO_EVERYONE","MUTUAL_FOLLOW_FRIENDS","SELF_ONLY"): raise HTTPException(400,"Invalid privacy setting.")
    path=CLIPS/Path(filename).name
    if not path.exists(): raise HTTPException(404,"Clip not found.")
    token=json.loads(tokenfile.read_text()).get("access_token")
    size=path.stat().st_size
    headers={"Authorization":"Bearer "+token,"Content-Type":"application/json; charset=UTF-8"}
    async with httpx.AsyncClient(timeout=120) as client:
        init=await client.post("https://open.tiktokapis.com/v2/post/publish/video/init/",headers=headers,json={"post_info":{"title":caption[:150],"privacy_level":privacy,"disable_duet":False,"disable_comment":False,"disable_stitch":False},"source_info":{"source":"FILE_UPLOAD","video_size":size,"chunk_size":size,"total_chunk_count":1}})
        if init.status_code>=300: raise HTTPException(400,"TikTok rejected the publish request: "+init.text[:700])
        payload=init.json().get("data",{}); upload_url=payload.get("upload_url")
        if not upload_url: raise HTTPException(502,"TikTok did not return an upload URL.")
        with path.open("rb") as f: content=f.read()
        up=await client.put(upload_url,content=content,headers={"Content-Type":"video/mp4","Content-Length":str(size),"Content-Range":f"bytes 0-{size-1}/{size}"})
        if up.status_code>=300: raise HTTPException(400,"TikTok upload failed: "+up.text[:500])
        return {"ok":True,"publish_id":payload.get("publish_id"),"message":"TikTok accepted the upload. Check TikTok's posting status and app inbox; processing/review may still be pending."}
