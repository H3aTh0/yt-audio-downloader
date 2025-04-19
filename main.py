from dotenv import load_dotenv
import os, re, uuid, time, subprocess, requests
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse
from youtube_transcript_api import YouTubeTranscriptApi

load_dotenv()

app = FastAPI()

YOUTUBE_API_KEY   = os.getenv("YOUTUBE_API_KEY")
ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY")

@app.post("/extract_video_id")
def extract_video_id(video_url: str = Query(..., description="YouTube video URL")):
    m = re.search(r"(?:v=|youtu\.be/)([^&]+)", video_url)
    if not m:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")
    return {"video_id": m.group(1)}


@app.get("/metadata")
def get_video_metadata(video_id: str = Query(..., description="YouTube video ID")):
    if not YOUTUBE_API_KEY:
        raise HTTPException(500, "YOUTUBE_API_KEY not set")
    r = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={
          "part": "snippet,contentDetails,statistics",
          "id": video_id,
          "key": YOUTUBE_API_KEY
        }
    )
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    items = r.json().get("items", [])
    if not items:
        raise HTTPException(404, "Video not found")
    i = items[0]
    return {
        "title": i["snippet"]["title"],
        "description": i["snippet"]["description"],
        "tags": i["snippet"].get("tags", []),
        "duration": i["contentDetails"]["duration"],
        "stats": i["statistics"]
    }


@app.post("/transcribe")
def download_and_transcribe_audio(video_id: str = Query(..., description="YouTube video ID")):
    if not ASSEMBLYAI_API_KEY:
        raise HTTPException(500, "ASSEMBLYAI_API_KEY not set")
    # 1) download audio
    out = f"/tmp/{uuid.uuid4()}.mp3"
    subprocess.run(
      ["yt-dlp","-f","bestaudio","-x","--audio-format","mp3","-o", out, f"https://youtu.be/{video_id}"],
      check=True
    )
    # 2) upload to AssemblyAI
    with open(out,"rb") as f:
        up = requests.post(
          "https://api.assemblyai.com/v2/upload",
          headers={"authorization": ASSEMBLYAI_API_KEY}, data=f
        ).json()
    url = up.get("upload_url")
    # 3) start transcription
    tx = requests.post(
      "https://api.assemblyai.com/v2/transcript",
      headers={"authorization": ASSEMBLYAI_API_KEY},
      json={"audio_url": url, "speaker_labels": True}
    ).json()
    tid = tx.get("id")
    if not tid:
        raise HTTPException(500, "Transcription request failed")
    # 4) poll
    while True:
        status = requests.get(
          f"https://api.assemblyai.com/v2/transcript/{tid}",
          headers={"authorization": ASSEMBLYAI_API_KEY}
        ).json()
        if status["status"] == "completed":
            return {
                "transcript": status["text"],
                "paragraphs": status.get("paragraphs", []),
                "speaker_labels": status.get("utterances", [])
            }
        if status["status"] == "error":
            raise HTTPException(500, "Transcription failed")
        time.sleep(5)


@app.get("/captions")
def fallback_to_captions(video_id: str = Query(..., description="YouTube video ID")):
    try:
        segs = YouTubeTranscriptApi.get_transcript(video_id)
    except Exception as e:
        raise HTTPException(500, f"Captions error: {e}")
    text = " ".join(s["text"] for s in segs)
    return {"captions": text, "segments": segs}


@app.post("/summarize")
def generate_video_summary(payload: dict):
    # Echo back everything so ChatGPT can consume it
    return payload
