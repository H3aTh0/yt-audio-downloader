# main.py - FastAPI app for YouTube video summarization
# ------------------------------------------------------
# Imports and environment loading
from dotenv import load_dotenv  # Load .env into os.environ
import os, re, uuid, time, subprocess, requests
from fastapi import FastAPI, Body, Query, HTTPException
from fastapi.responses import JSONResponse
from youtube_transcript_api import YouTubeTranscriptApi

# Load environment variables at startup
load_dotenv()

# Create FastAPI app
app = FastAPI()

# Read API keys from environment
YOUTUBE_API_KEY    = os.getenv("YOUTUBE_API_KEY")
ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY")

@app.post("/extract_video_id")
def extract_video_id(
    payload: dict = Body(..., description="JSON with a 'video_url' key")
):
    """
    Extracts the video_id from a YouTube URL.
    Expects JSON body: { "video_url": "<URL>" }
    Returns: { "video_id": "<ID>" }
    """
    video_url = payload.get("video_url")
    if not video_url:
        raise HTTPException(status_code=400, detail="Missing 'video_url' in request body")
    m = re.search(r"(?:v=|youtu\.be/)([^&]+)", video_url)
    if not m:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")
    return {"video_id": m.group(1)}

@app.get("/metadata")
def get_video_metadata(
    video_id: str = Query(..., description="YouTube video ID")
):
    """
    Fetches metadata (title, description, tags, duration, stats) from YouTube Data API v3.
    """
    if not YOUTUBE_API_KEY:
        raise HTTPException(status_code=500, detail="YOUTUBE_API_KEY not set")
    response = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={
            "part": "snippet,contentDetails,statistics",
            "id": video_id,
            "key": YOUTUBE_API_KEY
        }
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
    Downloads the audio-only stream directly (no conversion) and uploads to AssemblyAI.
    Expects JSON body: { "video_id": "<ID>" }
    Returns transcript text, paragraphs, and speaker_labels.
    """
    video_id = payload.get("video_id")
    if not video_id:
        raise HTTPException(status_code=400, detail="Missing 'video_id' in request body")
    if not ASSEMBLYAI_API_KEY:
        raise HTTPException(status_code=500, detail="ASSEMBLYAI_API_KEY not set")

    # Download best audio stream in m4a container (no ffmpeg needed)
    output_path = f"/tmp/{uuid.uuid4()}.m4a"
    try:
        subprocess.run(
            [
                "yt-dlp", "-f", "bestaudio[ext=m4a]",  # select m4a stream
                "-o", output_path,
                f"https://youtu.be/{video_id}"
            ],
            check=True
        )
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"yt-dlp failed: {e}")

    # Upload audio file to AssemblyAI
    with open(output_path, "rb") as audio_file:
        upload_resp = requests.post(
            "https://api.assemblyai.com/v2/upload",
            headers={"authorization": ASSEMBLYAI_API_KEY},
            data=audio_file
        ).json()
    upload_url = upload_resp.get("upload_url")
    if not upload_url:
        raise HTTPException(status_code=500, detail="AssemblyAI upload error")

    # Request transcription with speaker labels
    transcript_req = requests.post(
        "https://api.assemblyai.com/v2/transcript",
        headers={"authorization": ASSEMBLYAI_API_KEY},
        json={"audio_url": upload_url, "speaker_labels": True}
    ).json()
    transcript_id = transcript_req.get("id")
    if not transcript_id:
        raise HTTPException(status_code=500, detail="Transcription request failed")

    # Poll until transcription is complete
    while True:
        status_resp = requests.get(
            f"https://api.assemblyai.com/v2/transcript/{transcript_id}",
            headers={"authorization": ASSEMBLYAI_API_KEY}
        ).json()
        if status_resp.get("status") == "completed":
            return {
                "transcript": status_resp.get("text"),
                "paragraphs": status_resp.get("paragraphs", []),
                "speaker_labels": status_resp.get("utterances", [])
            }
        if status_resp.get("status") == "error":
            raise HTTPException(status_code=500, detail="Transcription failed")
        time.sleep(5)

@app.get("/captions")
def fallback_to_captions(
    video_id: str = Query(..., description="YouTube video ID")
):
    """
    Fallback to captions via youtube-transcript-api if transcription fails.
    Returns combined text and segment timestamps.
    """
    try:
        segments = YouTubeTranscriptApi.get_transcript(video_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Captions error: {e}")
    full_text = " ".join(seg["text"] for seg in segments)
    return {"captions": full_text, "segments": segments}

@app.post("/summarize")
def generate_video_summary(
    payload: dict = Body(..., description="All gathered data for summarization")
):
    """
    Echoes back all data so ChatGPT can generate the final summary.
    """
    return payload
