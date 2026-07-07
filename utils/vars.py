from dotenv import load_dotenv
import os
if os.path.exists(".env"):
    load_dotenv(override=True)
PORT = int(os.getenv("PORT", "8000"))
# MAX_FILE_SIZE dalam MB (akan dikali 1024*1024 di SpeechSTT)
raw_max_size = os.getenv("MAX_FILE_SIZE", "500")
try:
    MAX_FILE_SIZE = int(str(raw_max_size).strip())
except ValueError:
    MAX_FILE_SIZE = 500  # default 500 MB
MODEL_WHISPER = os.getenv("MODEL_WHISPER", "large-v3")
URL_SAVE_DB   = os.getenv("URL_SAVE_DB",   "http://localhost:8010/upload-pdf/")
MAIN_BACKEND_RESULT_URL = os.getenv(
    "MAIN_BACKEND_RESULT_URL",
    "http://10.158.32.26:4000/webhook/register_stt_result",
)


import json
from pathlib import Path
BASE = Path("/tmp/stt_status")
BASE.mkdir(parents=True, exist_ok=True)
def _p(job_id: str) -> Path:
    return BASE / f"{job_id}.json"
def set_status(job_id: str, status: str, detail=None):
    data = {"job_id": job_id, "status": status}
    if detail is not None:
        data["detail"] = detail
    _p(job_id).write_text(json.dumps(data))
def get_status(job_id: str):
    p = _p(job_id)
    if not p.exists():
        return {"job_id": job_id, "status": "unknown"}
    return json.loads(p.read_text())
