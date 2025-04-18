import os, re, uuid, time, subprocess, requests
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse
from youtube_transcript_api import YouTubeTranscriptApi

app = FastAPI()

YOUTUBE_API_KEY   = os.getenv("YOUTUBE_API_KEY")
ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY")


@app.get("/extract_video_id")
def extract_video_id(url: str = Query(..., description="YouTube video URL")):
    pattern = r"(?:v=|youtu\.be/)([^&]+)"
    m = re.search(pattern, url)
    if not m:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")
    return {"video_id": m.group(1)}


@app.get("/metadata")
def get_video_metadata(video_id: str = Query(..., description="YouTube video ID")):
    if not YOUTUBE_API_KEY:
        raise HTTPException(500, "YOUTUBE_API_KEY not set")
    api_url = (
        "https://www.googleapis.com/youtube/v3/videos"
        f"?part=snippet,contentDetails,statistics"
        f"&id={video_id}&key={YOUTUBE_API_KEY}"
    )
    r = requests.get(api_url)
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail="YouTube API error")
    items = r.json().get("items") or []
    if not items:
        raise HTTPException(404, "Video not found")
    itm = items[0]
    return {
        "title":       itm["snippet"]["title"],
        "description": itm["snippet"]["description"],
        "tags":        itm["snippet"].get("tags", []),
        "duration":    itm["contentDetails"]["duration"],
        "stats":       itm["statistics"],
    }


@app.post("/transcribe")
def download_and_transcribe_audio(video_id: str = Query(..., description="YouTube video ID")):
    if not ASSEMBLYAI_API_KEY:
        raise HTTPException(500, "ASSEMBLYAI_API_KEY not set")
    # 1) Download audio via yt-dlp
    fname = f"{uuid.uuid4()}.mp3"
    outp  = os.path.join("/tmp", fname)
    url   = f"https://www.youtube.com/watch?v={video_id}"
    try:
        subprocess.run(
            ["yt-dlp","-f","bestaudio","-x","--audio-format","mp3","-o",outp,url],
            check=True
        )
    except subprocess.CalledProcessError as e:
        raise HTTPException(500, f"yt-dlp failed: {e}")

    # 2) Upload to AssemblyAI
    with open(outp, "rb") as f:
        up = requests.post(
            "https://api.assemblyai.com/v2/upload",
            headers={"authorization": ASSEMBLYAI_API_KEY},
            data=f
        )
    upload_url = up.json().get("upload_url")
    if not upload_url:
        raise HTTPException(500, "AssemblyAI upload error")

    # 3) Kick off transcription
    tx = requests.post(
        "https://api.assemblyai.com/v2/transcript",
        json={"audio_url": upload_url, "speaker_labels": True},
        headers={"authorization": ASSEMBLYAI_API_KEY}
    ).json()
    tx_id = tx.get("id")
    if not tx_id:
        raise HTTPException(500, "Transcription request failed")

    # 4) Poll until done
    while True:
        st = requests.get(
            f"https://api.assemblyai.com/v2/transcript/{tx_id}",
            headers={"authorization": ASSEMBLYAI_API_KEY}
        ).json()
        status = st.get("status")
        if status == "completed":
            return {
                "transcript":     st.get("text"),
                "paragraphs":     st.get("paragraphs", []),
                "speaker_labels": st.get("utterances", [])
            }
        if status == "error":
            raise HTTPException(500, "Transcription failed")
        time.sleep(5)


@app.get("/captions")
def fallback_to_captions(video_id: str = Query(..., description="YouTube video ID")):
    try:
        segs = YouTubeTranscriptApi.get_transcript(video_id)
    except Exception as e:
        raise HTTPException(500, f"Captions error: {e}")
    full = " ".join(s["text"] for s in segs)
    return {"captions": full, "segments": segs}


@app.post("/summarize")
def generate_video_summary(payload: dict):
    # If you prefer ChatGPT to do the actual summary, simply
    # hand this payload back to your GPT function call.
    return payload
