import asyncio
import hmac
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import get_settings
from app.ifrs_advisor import ask_ifrs_advisor, ingest_knowledge_file

app = FastAPI(title="IFRS Advisor API")

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatMessage(BaseModel):
    role: str
    content: str


class AskRequest(BaseModel):
    question: str
    history: list[ChatMessage] | None = None


class AskResponse(BaseModel):
    answer: str


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "knowledge_configured": bool(settings.vector_store_id),
    }


@app.post("/api/ask", response_model=AskResponse)
def ask(request: AskRequest) -> AskResponse:
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="question must not be empty")

    history = [m.model_dump() for m in request.history] if request.history else None
    try:
        answer = ask_ifrs_advisor(request.question, history)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="The advisor request failed") from exc

    return AskResponse(answer=answer)


@app.post("/api/admin/knowledge/ingest")
async def ingest_knowledge(request: Request) -> dict:
    configured = settings.ingestion_token or ""
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
    if not configured or not hmac.compare_digest(configured, supplied):
        raise HTTPException(status_code=401, detail="Unauthorized")

    filename = request.headers.get("x-filename", "International-GAAP-2026.pdf")
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="Only PDF files are accepted")

    total = 0
    temp_path: Path | None = None
    try:
        fd, raw_path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        temp_path = Path(raw_path)
        with temp_path.open("wb") as output:
            async for chunk in request.stream():
                total += len(chunk)
                if total > 512 * 1024 * 1024:
                    raise HTTPException(status_code=413, detail="PDF exceeds 512 MB")
                output.write(chunk)

        if total == 0:
            raise HTTPException(status_code=400, detail="PDF body is empty")

        file_id, vector_store_id = await asyncio.to_thread(
            ingest_knowledge_file, temp_path, filename
        )
        return {
            "status": "completed",
            "file_id": file_id,
            "vector_store_id": vector_store_id,
            "bytes": total,
        }
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()
