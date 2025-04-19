# main.py - FastAPI app for YouTube video summarization with captions fallback
from dotenv import load_dotenv  # Load .env into os.environ
import os, re, uuid, time, requests
from fastapi import FastAPI, Body, Query, HTTPException
from fastapi.responses import JSONResponse
from youtube_transcript_api import YouTubeTranscriptApi  # Fallback caption scraping
import yt_dlp  # Python API for downloading audio

# Load environment variables
load_dotenv()

app = FastAPI()

# Read API keys
YOUTUBE_API_KEY    = os.getenv("YOUTUBE_API_KEY")
ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY")

@app.post("/extract_video_id")
def extract_video_id(payload: dict = Body(..., description="JSON with a 'video_url' key")):
    video_url = payload.get("video_url")
    if not video_url:
        raise HTTPException(status_code=400, detail="Missing 'video_url' in request body")
    m = re.search(r"(?:v=|youtu\.be/)([^&]+)", video_url)
    if not m:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")
    return {"video_id": m.group(1)}

@app.get("/metadata")
def get_video_metadata(video_id: str = Query(..., description="YouTube video ID")):
    if not YOUTUBE_API_KEY:
        raise HTTPException(status_code=500, detail="YOUTUBE_API_KEY not set")
    response = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={"part": "snippet,contentDetails,statistics", "id": video_id, "key": YOUTUBE_API_KEY}
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    items = response.json().get("items", [])
    if not items:
        raise HTTPException(status_code=404, detail="Video not found")
    item = items[0]
    return {
        "title": item["snippet"]["title"],
        "description": item["snippet"]["description"],
        "tags": item["snippet"].get("tags", []),
        "duration": item["contentDetails"]["duration"],
        "stats": item.get("statistics", {})
    }

@app.post("/transcribe")
def download_and_transcribe_audio(
    payload: dict = Body(..., description="JSON with a 'video_id' key")
):
    """
    Attempt to download audio via yt_dlp and send to AssemblyAI.
    If download fails (e.g., bot-check), fallback to YouTube captions.
    Returns transcript text, paragraphs, speaker_labels, and a 'source' key.
    """
    video_id = payload.get("video_id")
    if not video_id:
        raise HTTPException(status_code=400, detail="Missing 'video_id' in request body")
    if not ASSEMBLYAI_API_KEY:
        raise HTTPException(status_code=500, detail="ASSEMBLYAI_API_KEY not set")
    # Prepare output path
    output_path = f"/tmp/{uuid.uuid4()}.m4a"
    ydl_opts = {
        'format': 'bestaudio[ext=m4a]',
        'outtmpl': output_path,
        'quiet': True,
        'nopart': True,
    }
    try:
        # Try audio download via yt_dlp Python API
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([f"https://youtu.be/{video_id}"])
        # Upload to AssemblyAI
        with open(output_path, "rb") as audio_file:
            upload_resp = requests.post(
                "https://api.assemblyai.com/v2/upload",
                headers={"authorization": ASSEMBLYAI_API_KEY},
                data=audio_file
            ).json()
        upload_url = upload_resp.get("upload_url")
        if not upload_url:
            raise Exception("AssemblyAI upload error")
        # Request transcription
        transcript_req = requests.post(
            "https://api.assemblyai.com/v2/transcript",
            headers={"authorization": ASSEMBLYAI_API_KEY},
            json={"audio_url": upload_url, "speaker_labels": True}
        ).json()
        tid = transcript_req.get("id")
        if not tid:
            raise Exception("Transcription request failed")
        # Poll until complete
        while True:
            status_resp = requests.get(
                f"https://api.assemblyai.com/v2/transcript/{tid}",
                headers={"authorization": ASSEMBLYAI_API_KEY}
            ).json()
            if status_resp.get("status") == "completed":
                return {
                    "transcript": status_resp.get("text"),
                    "paragraphs": status_resp.get("paragraphs", []),
                    "speaker_labels": status_resp.get("utterances", []),
                    "source": "assemblyai_audio"
                }
            if status_resp.get("status") == "error":
                raise Exception("Transcription failed")
            time.sleep(5)
    except Exception as e:
        # Fallback to captions
        try:
            segments = YouTubeTranscriptApi.get_transcript(video_id)
            full_text = " ".join(seg['text'] for seg in segments)
            return {
                "transcript": full_text,
                "paragraphs": [],
                "speaker_labels": [],
                "source": "youtube_captions"
            }
        except Exception as ce:
            raise HTTPException(status_code=500, detail=f"All transcription methods failed: {ce}")@app.get("/captions")
def fallback_to_captions(video_id: str = Query(..., description="YouTube video ID")):
    try:
        segments = YouTubeTranscriptApi.get_transcript(video_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Captions error: {e}")
    full_text = " ".join(seg['text'] for seg in segments)
    return {"captions": full_text, "segments": segments}

@app.post("/summarize")
def generate_video_summary(payload: dict = Body(..., description="All gathered data for summarization")):
    return payload
