from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
import subprocess
import os
import uuid

app = FastAPI()

@app.get("/download_audio")
def download_audio(url: str = Query(..., description="YouTube video URL")):
    audio_filename = f"{uuid.uuid4()}.mp3"
    output_template = os.path.join("/tmp", audio_filename)

    try:
        subprocess.run([
            "yt-dlp",
            "-f", "bestaudio",
            "-x", "--audio-format", "mp3",
            "-o", output_template,
            url
        ], check=True)

        upload_cmd = [
            "curl", "-F", f"file=@{output_template}", "https://file.io"
        ]
        result = subprocess.run(upload_cmd, capture_output=True, text=True)
        return JSONResponse(content=result.stdout)
    except subprocess.CalledProcessError as e:
        return {"error": f"Download failed: {str(e)}"}

