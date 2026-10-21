import os
import uuid
import shutil
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import yt_dlp

app = FastAPI(title="Frizy Media Backend")

# Izinkan akses dari domain Lovable kamu
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Bisa diganti dengan domain Lovable kamu nantinya
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TEMP_DIR = "/tmp/frizy_downloads"
os.makedirs(TEMP_DIR, exist_ok=True)

# Simpan status pekerjaan di memory
jobs_db = {}

class InspectRequest(BaseModel):
    url: str

class JobRequest(BaseModel):
    url: str
    format_id: str
    type: str  # "video" atau "audio"

@app.get("/")
def health_check():
    return {"status": "ok", "service": "Frizy Backend Engine"}

@app.post("/api/media/inspect")
def inspect_media(req: InspectRequest):
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(req.url, download=False)
            
            formats = []
            for f in info.get("formats", []):
                # Filter format video umum & audio
                if f.get("ext") in ["mp4", "m4a", "webm"] and (f.get("vcodec") != "none" or f.get("acodec") != "none"):
                    formats.append({
                        "id": f.get("format_id"),
                        "ext": f.get("ext"),
                        "resolution": f.get("resolution") or f"{f.get('height', 'audio')}p",
                        "filesize_approx": f.get("filesize") or f.get("filesize_approx") or 0,
                        "has_audio": f.get("acodec") != "none"
                    })
            
            return {
                "title": info.get("title"),
                "duration": info.get("duration"),
                "author": info.get("uploader"),
                "thumbnail": info.get("thumbnail"),
                "formats": formats
            }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

def process_download(job_id: str, url: str, format_id: str, media_type: str):
    jobs_db[job_id]["status"] = "processing"
    output_path = os.path.join(TEMP_DIR, f"{job_id}.%(ext)s")
    
    if media_type == "audio":
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': output_path,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        }
    else:
        ydl_opts = {
            'format': f"{format_id}+bestaudio/best",
            'outtmpl': output_path,
            'merge_output_format': 'mp4',
        }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            
        ext = "mp3" if media_type == "audio" else "mp4"
        final_file = os.path.join(TEMP_DIR, f"{job_id}.{ext}")
        
        if os.path.exists(final_file):
            jobs_db[job_id]["status"] = "ready"
            jobs_db[job_id]["file_path"] = final_file
        else:
            jobs_db[job_id]["status"] = "failed"
            jobs_db[job_id]["error"] = "File output not found"
    except Exception as e:
        jobs_db[job_id]["status"] = "failed"
        jobs_db[job_id]["error"] = str(e)

@app.post("/api/media/jobs")
def create_job(req: JobRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())[:8]
    jobs_db[job_id] = {"status": "queued", "file_path": None}
    
    background_tasks.add_task(process_download, job_id, req.url, req.format_id, req.type)
    return {"job_id": job_id, "status": "pending"}

@app.get("/api/media/jobs/{job_id}")
def get_job_status(job_id: str):
    job = jobs_db.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"status": job["status"], "error": job.get("error")}

@app.get("/api/media/jobs/{job_id}/download")
def download_file(job_id: str, background_tasks: BackgroundTasks):
    job = jobs_db.get(job_id)
    if not job or job.get("status") != "ready":
        raise HTTPException(status_code=400, detail="File is not ready")
    
    file_path = job.get("file_path")
    filename = os.path.basename(file_path)
    
    # Hapus file otomatis setelah terkirim
    def cleanup():
        if os.path.exists(file_path):
            os.remove(file_path)
            
    background_tasks.add_task(cleanup)
    return FileResponse(file_path, filename=filename, media_type="application/octet-stream")
