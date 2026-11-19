import os, uuid, json, secrets, subprocess, time, tempfile, re
from pathlib import Path
from urllib.parse import urlencode, urlparse
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from cryptography.fernet import Fernet, InvalidToken
import httpx

ROOT = Path(os.getenv("STORAGE_ROOT", "./data")).resolve()
UPLOADS, CLIPS = ROOT / "uploads", ROOT / "clips"
META = ROOT / "jobs.json"
for folder in (UPLOADS, CLIPS):
    folder.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="ViralClip AI", version="2.0.0")
security = HTTPBasic()

def auth(c: HTTPBasicCredentials = Depends(security)):
    username, password = os.getenv("APP_USERNAME", ""), os.getenv("APP_PASSWORD", "")
    if not username or not password or not (
        secrets.compare_digest(c.username, username) and secrets.compare_digest(c.password, password)
    ):
        raise HTTPException(401, "App login is not configured. Set APP_USERNAME and APP_PASSWORD in Render.",
                            headers={"WWW-Authenticate": "Basic"})
    return c.username

def jobs():
    try:
        return json.loads(META.read_text())
    except (OSError, ValueError):
        return []

def save_jobs(items):
    temp = META.with_suffix(".tmp")
    temp.write_text(json.dumps(items, indent=2))
    temp.replace(META)

def run(cmd, timeout=1800):
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout)[-1600:])
    return result.stdout.strip()

def video_duration(path):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "default=noprint_wrappers=1:nokey=1", str(path)]))

def get_item(jid):
    items = jobs()
    item = next((x for x in items if x["id"] == jid), None)
    if not item:
        raise HTTPException(404, "Video not found.")
    return items, item

def token_box():
    key = os.getenv("TOKEN_ENCRYPTION_KEY", "")
    if not key:
        raise HTTPException(503, "TikTok token encryption is not configured. Add TOKEN_ENCRYPTION_KEY in Render.")
    try:
        return Fernet(key.encode())
    except Exception:
        raise HTTPException(503, "TOKEN_ENCRYPTION_KEY is invalid. Use a Fernet-compatible key.")

def analyze_with_ai(source: Path, total: float, clip_length: int):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return [], "AI analysis is not configured; generated balanced sample clips instead."
    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / "audio.mp3"
        run(["ffmpeg", "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000",
             "-b:a", "48k", "-t", str(min(total, 7200)), str(audio)])
        if audio.stat().st_size > 24 * 1024 * 1024:
            return [], "Audio is too large for one-pass transcription; generated balanced sample clips instead."
        headers = {"Authorization": f"Bearer {api_key}"}
        with audio.open("rb") as f:
            tr = httpx.post("https://api.openai.com/v1/audio/transcriptions",
                            headers=headers, data={"model": "whisper-1", "response_format": "verbose_json"},
                            files={"file": ("audio.mp3", f, "audio/mpeg")}, timeout=180)
        tr.raise_for_status()
        segments = tr.json().get("segments", [])
        if not segments:
            return [], "No speech transcript was detected; generated balanced sample clips instead."
        compact = [{"start": round(s.get("start", 0), 1), "end": round(s.get("end", 0), 1),
                    "text": s.get("text", "")[:300]} for s in segments[:500] if s.get("text")]
        prompt = (
            "You are an expert short-form video editor. Select up to five distinct, compelling moments "
            "from this transcript. Prefer strong hooks, surprising claims, emotional turns, useful tips, "
            "clear punchlines, and self-contained stories. Avoid introductions, dead air, repeated moments, "
            "and contextless fragments. Each moment must fit the requested duration and stay within video bounds. "
            "Return ONLY JSON: {\"clips\":[{\"start\":number,\"reason\":string,\"title\":string,"
            "\"caption\":string,\"hashtags\":[string]}]}. Do not claim guaranteed virality.\n"
            f"Video duration: {total:.1f}s. Target clip duration: {clip_length}s.\nTranscript segments: "
            + json.dumps(compact, ensure_ascii=False)
        )
        response = httpx.post("https://api.openai.com/v1/chat/completions", headers=headers,
            json={"model": "gpt-4o-mini", "temperature": 0.2,
                  "response_format": {"type": "json_object"},
                  "messages": [{"role": "user", "content": prompt}]}, timeout=120)
        response.raise_for_status()
        data = json.loads(response.json()["choices"][0]["message"]["content"])
        chosen = []
        for c in data.get("clips", [])[:5]:
            start = max(0, min(float(c.get("start", 0)), max(0, total - clip_length)))
            if any(abs(start - old["start"]) < clip_length * 0.65 for old in chosen):
                continue
            chosen.append({"start": start, "reason": str(c.get("reason", ""))[:240],
                           "title": str(c.get("title", "Highlight"))[:100],
                           "caption": str(c.get("caption", ""))[:150],
                           "hashtags": [str(x)[:40] for x in c.get("hashtags", [])[:8]]})
        return chosen, "AI-ranked moments from speech transcription. Review every clip before publishing."

@app.get("/api/health")
def health():
    return {"ok": True, "app": "ViralClip AI", "version": "2.0.0"}

@app.get("/", response_class=HTMLResponse)
def home(user: str = Depends(auth)):
    return HTMLResponse(Path("static/index.html").read_text())

@app.get("/api/jobs")
def list_jobs(user: str = Depends(auth)):
    return jobs()

def remove_job_files(item):
    candidates = [Path(item.get("path", ""))]
    candidates.extend(CLIPS / Path(c.get("name", "")).name for c in item.get("clips", []))
    for path in candidates:
        try:
            resolved = path.resolve()
            if resolved.parent in (UPLOADS.resolve(), CLIPS.resolve()) and resolved.is_file(): resolved.unlink()
        except (OSError, ValueError): pass

@app.get("/api/storage")
def storage_status(user: str = Depends(auth)):
    def folder_bytes(folder):
        total = 0
        for p in folder.iterdir():
            if p.is_file():
                try: total += p.stat().st_size
                except OSError: pass
        return total
    return {"uploads_bytes": folder_bytes(UPLOADS), "clips_bytes": folder_bytes(CLIPS), "jobs": len(jobs())}

@app.delete("/api/jobs/{jid}")
def delete_job(jid: str, user: str = Depends(auth)):
    data, item = get_item(jid)
    remove_job_files(item)
    save_jobs([x for x in data if x.get("id") != jid])
    return {"ok": True, "message": "Video and its generated clips deleted."}

@app.delete("/api/library")
def clear_library(user: str = Depends(auth)):
    for item in jobs(): remove_job_files(item)
    save_jobs([])
    for folder in (UPLOADS, CLIPS):
        for path in folder.iterdir():
            if path.is_file():
                try: path.unlink()
                except OSError: pass
    return {"ok": True, "message": "All videos and generated clips were cleared. TikTok connection was preserved."}

@app.post("/api/upload")
async def upload(file: UploadFile = File(...), user: str = Depends(auth)):
    ext = Path(file.filename or "video.mp4").suffix.lower()
    if ext not in (".mp4", ".mov", ".m4v", ".webm", ".mkv"):
        raise HTTPException(400, "Upload an MP4, MOV, WEBM or MKV video.")
    jid = uuid.uuid4().hex[:10]
    dest = UPLOADS / f"{jid}{ext}"
    size = 0
    try:
        with dest.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > 1024 * 1024 * 1024:
                    raise HTTPException(413, "Maximum upload size is 1 GB.")
                out.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    item = {"id": jid, "source": file.filename, "path": str(dest), "status": "uploaded",
            "created": int(time.time()), "clips": []}
    data = jobs()
    data.insert(0, item)
    save_jobs(data)
    return item

@app.post("/api/youtube")
def youtube(url: str = Form(...), user: str = Depends(auth)):
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not (host == "youtu.be" or host.endswith(".youtube.com") or host == "youtube.com"):
        raise HTTPException(400, "Enter a valid YouTube video URL.")
    jid = uuid.uuid4().hex[:10]
    dest = UPLOADS / f"{jid}.mp4"
    try:
        # Current yt-dlp plus its supported EJS component handles YouTube's
        # changing player challenges more reliably. It cannot authenticate
        # private/age-gated videos or override YouTube account restrictions.
        run(["yt-dlp", "--no-playlist", "--max-filesize", "1G",
             "--retries", "3", "--extractor-retries", "3", "--fragment-retries", "3",
             "--socket-timeout", "30", "--remote-components", "ejs:github", "-f",
             "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b", "--merge-output-format", "mp4",
             "-o", str(dest), url], timeout=1800)
    except Exception as e:
        dest.unlink(missing_ok=True)
        detail = str(e)
        lowered = detail.lower()
        if "sign in to confirm" in lowered or "http error 403" in lowered or "not a bot" in lowered:
            message = (
                "YouTube refused this server download (403 / sign-in verification). "
                "This is a YouTube access restriction, not an AI-key problem. "
                "For this video, download the original from your own YouTube Studio "
                "or get a copy from its creator with permission, then use Upload video. "
                "ViralClip AI cannot access private or verification-gated videos. "
            )
        else:
            message = (
                "YouTube import failed. If you have permission to reuse this video, "
                "try again later or upload the original video file. Details: "
            )
        raise HTTPException(400, message + detail[-700:])
    item = {"id": jid, "source": url, "path": str(dest), "status": "uploaded",
            "created": int(time.time()), "clips": []}
    data = jobs()
    data.insert(0, item)
    save_jobs(data)
    return item

@app.post("/api/jobs/{jid}/clips")
def make_clips(jid: str, clip_length: int = Form(35), user: str = Depends(auth)):
    if clip_length < 15 or clip_length > 60:
        raise HTTPException(400, "Clip length must be 15–60 seconds.")
    data, item = get_item(jid)
    src = Path(item["path"])
    if not src.exists():
        raise HTTPException(410, "Source video is no longer stored.")
    try:
        total = video_duration(src)
    except Exception as e:
        raise HTTPException(400, "Could not read video: " + str(e))
    if total < clip_length:
        raise HTTPException(400, "Video is shorter than the selected clip length.")
    try:
        picks, note = analyze_with_ai(src, total, clip_length)
    except Exception as e:
        picks, note = [], "AI analysis unavailable (" + str(e)[:180] + "); generated balanced sample clips instead."
    if not picks:
        count = min(5, max(1, int(total // clip_length)))
        starts = sorted(set(int((total - clip_length) * i / max(1, count - 1)) for i in range(count)))
        picks = [{"start": float(s), "reason": "Balanced sample excerpt; not AI-ranked.",
                  "title": f"Clip {i}", "caption": "", "hashtags": []} for i, s in enumerate(starts, 1)]
    clips = []
    for n, pick in enumerate(picks, 1):
        start = max(0, min(float(pick["start"]), total - clip_length))
        out = CLIPS / f"{jid}-{n}.mp4"
        try:
            run(["ffmpeg", "-y", "-ss", str(start), "-i", str(src), "-t", str(clip_length),
                 "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac",
                 "-b:a", "128k", "-movflags", "+faststart", str(out)], timeout=1800)
        except Exception as e:
            raise HTTPException(500, "Clip rendering failed: " + str(e))
        clips.append({"name": out.name, "url": f"/clips/{out.name}", "start_seconds": round(start, 1),
                      "duration": clip_length, "reason": pick.get("reason", ""),
                      "title": pick.get("title", ""), "caption": pick.get("caption", ""),
                      "hashtags": pick.get("hashtags", [])})
    item["clips"], item["status"] = clips, "clips_ready"
    item["analysis_note"] = note
    save_jobs(data)
    return {"id": jid, "clips": clips, "note": note}

app.mount("/clips", StaticFiles(directory=str(CLIPS)), name="clips")

@app.get("/api/tiktok/connect")
def tiktok_connect(user: str = Depends(auth)):
    key, redirect = os.getenv("TIKTOK_CLIENT_KEY"), os.getenv("TIKTOK_REDIRECT_URI")
    if not key or not redirect:
        raise HTTPException(400, "Add TIKTOK_CLIENT_KEY and TIKTOK_REDIRECT_URI in Render first.")
    state = secrets.token_urlsafe(32)
    (ROOT / "oauth_state").write_text(state)
    q = urlencode({"client_key": key, "response_type": "code",
                   "scope": "user.info.basic,video.publish", "redirect_uri": redirect, "state": state})
    return RedirectResponse("https://www.tiktok.com/v2/auth/authorize/?" + q)

@app.get("/oauth/tiktok/callback")
async def tiktok_callback(code: str = "", state: str = "", error: str = ""):
    state_file = ROOT / "oauth_state"
    expected = state_file.read_text() if state_file.exists() else ""
    if error:
        return HTMLResponse("TikTok authorization was cancelled. You can return to ViralClip AI.", 400)
    if not expected or not secrets.compare_digest(state, expected):
        raise HTTPException(400, "Invalid OAuth state.")
    state_file.unlink(missing_ok=True)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post("https://open.tiktokapis.com/v2/oauth/token/",
            data={"client_key": os.getenv("TIKTOK_CLIENT_KEY"),
                  "client_secret": os.getenv("TIKTOK_CLIENT_SECRET"), "code": code,
                  "grant_type": "authorization_code", "redirect_uri": os.getenv("TIKTOK_REDIRECT_URI")},
            headers={"Content-Type": "application/x-www-form-urlencoded"})
    if r.status_code >= 300:
        raise HTTPException(400, "TikTok token exchange failed. Check the TikTok app settings and redirect URL.")
    box = token_box()
    (ROOT / "tiktok_token.enc").write_bytes(box.encrypt(json.dumps(r.json()).encode()))
    return HTMLResponse("<h2>TikTok connected</h2><p>Return to ViralClip AI and choose a clip to publish.</p><a href='/'>Open app</a>")

@app.post("/api/tiktok/publish")
async def tiktok_publish(filename: str = Form(...), caption: str = Form(""),
                         privacy: str = Form("SELF_ONLY"), user: str = Depends(auth)):
    tokenfile = ROOT / "tiktok_token.enc"
    if not tokenfile.exists():
        raise HTTPException(400, "Connect TikTok first.")
    if privacy not in ("PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"):
        raise HTTPException(400, "Invalid privacy setting.")
    path = CLIPS / Path(filename).name
    if not path.exists():
        raise HTTPException(404, "Clip not found.")
    try:
        token_data = json.loads(token_box().decrypt(tokenfile.read_bytes()))
    except (InvalidToken, ValueError):
        raise HTTPException(500, "Saved TikTok authorization could not be read. Reconnect TikTok.")
    token = token_data.get("access_token")
    if not token:
        raise HTTPException(400, "TikTok access token is missing. Reconnect TikTok.")
    size = path.stat().st_size
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json; charset=UTF-8"}
    async with httpx.AsyncClient(timeout=180) as client:
        init = await client.post("https://open.tiktokapis.com/v2/post/publish/video/init/",
            headers=headers, json={"post_info": {"title": caption[:150],
            "privacy_level": privacy, "disable_duet": False, "disable_comment": False,
            "disable_stitch": False}, "source_info": {"source": "FILE_UPLOAD",
            "video_size": size, "chunk_size": size, "total_chunk_count": 1}})
        if init.status_code >= 300:
            raise HTTPException(400, "TikTok rejected the publish request: " + init.text[:500])
        payload = init.json().get("data", {})
        upload_url = payload.get("upload_url")
        if not upload_url:
            raise HTTPException(502, "TikTok did not return an upload URL.")
        with path.open("rb") as f:
            content = f.read()
        up = await client.put(upload_url, content=content, headers={"Content-Type": "video/mp4",
            "Content-Length": str(size), "Content-Range": f"bytes 0-{size-1}/{size}"})
        if up.status_code >= 300:
            raise HTTPException(400, "TikTok upload failed: " + up.text[:400])
        return {"ok": True, "publish_id": payload.get("publish_id"),
                "message": "TikTok accepted the upload. Check TikTok posting status; review may still be pending."}
