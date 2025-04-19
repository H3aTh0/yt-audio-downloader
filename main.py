# main.py - FastAPI app for YouTube video summarization
# ------------------------------------------------------
# Imports and environment loading
from dotenv import load_dotenv  # Load variables from .env file into os.environ
import os, re, uuid, time, subprocess, requests  # Standard libs and HTTP client
from fastapi import FastAPI, Body, Query, HTTPException  # FastAPI framework components
from fastapi.responses import JSONResponse  # Standard JSON response class (unused but imported per spec)
from youtube_transcript_api import YouTubeTranscriptApi  # Fallback caption scraping

# Load environment variables at startup
load_dotenv()  # Reads .env file and sets environment variables

# Instantiate FastAPI application
app = FastAPI()

# Read API keys from environment variables
# These should be defined in your .env (dev) or via Render dashboard (prod)
YOUTUBE_API_KEY    = os.getenv("YOUTUBE_API_KEY")
ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY")

# Route: Extract YouTube video ID from a URL
@app.post("/extract_video_id")
def extract_video_id(
    payload: dict = Body(..., description="JSON with a 'video_url' key")
):
    """
    Takes a JSON body {'video_url': '<YouTube URL>'}
    and returns {'video_id': '<extracted ID>'}.
    """
    video_url = payload.get("video_url")
    if not video_url:
        # Missing parameter in request body
        raise HTTPException(status_code=400, detail="Missing 'video_url' in request body")
    # Regex to match standard YouTube URL formats
    m = re.search(r"(?:v=|youtu\.be/)([^&]+)", video_url)
    if not m:
        # URL did not match expected pattern
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")
    return {"video_id": m.group(1)}

# Route: Fetch video metadata via YouTube Data API
@app.get("/metadata")
def get_video_metadata(
    video_id: str = Query(..., description="YouTube video ID")
):
    """
    Calls YouTube Data API v3 to retrieve snippet, duration, and statistics
    for the given video_id. Returns a JSON object with title, description,
    tags, duration (ISO 8601), and raw stats.
    """
    if not YOUTUBE_API_KEY:
        # Ensure API key is configured
        raise HTTPException(status_code=500, detail="YOUTUBE_API_KEY not set")
    # Make HTTP GET request to YouTube API
    response = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={
            "part": "snippet,contentDetails,statistics",
            "id": video_id,
            "key": YOUTUBE_API_KEY
        }
    )
    # Handle potential API errors
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    items = response.json().get("items", [])
    if not items:
        # Video ID not found or invalid
        raise HTTPException(status_code=404, detail="Video not found")
    item = items[0]
    # Extract and return structured metadata
    return {
        "title": item["snippet"]["title"],
        "description": item["snippet"]["description"],
        "tags": item["snippet"].get("tags", []),
        "duration": item["contentDetails"]["duration"],
        "stats": item.get("statistics", {})
    }

# Route: Download audio and transcribe via AssemblyAI
@app.post("/transcribe")
def download_and_transcribe_audio(
    payload: dict = Body(..., description="JSON with a 'video_id' key")
):
    """
    Downloads audio from YouTube using yt-dlp, uploads to AssemblyAI,
    polls for transcript completion, and returns full transcript,
    paragraph segments, and speaker labels.
    """
    video_id = payload.get("video_id")
    if not video_id:
        raise HTTPException(status_code=400, detail="Missing 'video_id' in request body")
    if not ASSEMBLYAI_API_KEY:
        raise HTTPException(status_code=500, detail="ASSEMBLYAI_API_KEY not set")

    # Step 1: Download audio to temporary file
    output_path = f"/tmp/{uuid.uuid4()}.mp3"
    try:
        subprocess.run(
            [
                "yt-dlp", "-f", "bestaudio", "-x", "--audio-format", "mp3",
                "-o", output_path, f"https://youtu.be/{video_id}"
            ],
            check=True
        )
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"yt-dlp failed: {e}")

    # Step 2: Upload audio to AssemblyAI
    with open(output_path, "rb") as audio_file:
        upload_resp = requests.post(
            "https://api.assemblyai.com/v2/upload",
            headers={"authorization": ASSEMBLYAI_API_KEY},
            data=audio_file
        ).json()
    upload_url = upload_resp.get("upload_url")
    if not upload_url:
        raise HTTPException(status_code=500, detail="AssemblyAI upload error")

    # Step 3: Request transcription with speaker labels
    transcript_req = requests.post(
        "https://api.assemblyai.com/v2/transcript",
        headers={"authorization": ASSEMBLYAI_API_KEY},
        json={"audio_url": upload_url, "speaker_labels": True}
    ).json()
    transcript_id = transcript_req.get("id")
    if not transcript_id:
        raise HTTPException(status_code=500, detail="Transcription request failed")

    # Step 4: Poll AssemblyAI until transcription is complete
    while True:
        status_resp = requests.get(
            f"https://api.assemblyai.com/v2/transcript/{transcript_id}",
            headers={"authorization": ASSEMBLYAI_API_KEY}
        ).json()
        status = status_resp.get("status")
        if status == "completed":
            # Return structured transcription data
            return {
                "transcript": status_resp.get("text"),
                "paragraphs": status_resp.get("paragraphs", []),
                "speaker_labels": status_resp.get("utterances", [])
            }
        if status == "error":
            raise HTTPException(status_code=500, detail="Transcription failed")
        time.sleep(5)  # Wait before polling again

# Route: Fallback caption scraping via youtube-transcript-api
@app.get("/captions")
def fallback_to_captions(
    video_id: str = Query(..., description="YouTube video ID")
):
    """
    Fetches captions directly if audio transcription fails or
    transcripts are unavailable. Returns combined text and
    segment-level timestamps.
    """
    try:
        segments = YouTubeTranscriptApi.get_transcript(video_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Captions error: {e}")
    full_text = " ".join(seg["text"] for seg in segments)
    return {"captions": full_text, "segments": segments}

# Route: Summarize collected data
@app.post("/summarize")
def generate_video_summary(
    payload: dict = Body(..., description="All gathered data for summarization")
):
    """
    Simply echoes back all received data so that ChatGPT can
    generate a structured summary based on transcript > captions > metadata.
    """
    return payload
