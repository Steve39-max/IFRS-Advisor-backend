import asyncio
import hmac
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import get_settings
from app.ifrs_advisor import ask_ifrs_advisor_structured, ingest_knowledge_file, upload_case_file

app = FastAPI(title="IFRS Advisor API")
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type", "Authorization", "X-Filename"],
)


class ChatMessage(BaseModel):
    role: str
    content: str


class AskRequest(BaseModel):
    question: str
    history: list[ChatMessage] | None = None
    file_ids: list[str] | None = None


class AskResponse(BaseModel):
    answer: str
    citations: list[dict]
    sources: list[dict]


def _authorized(request: Request, expected: str | None) -> bool:
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "knowledge_configured": bool(settings.vector_store_id)}


@app.post("/api/ask", response_model=AskResponse)
def ask(payload: AskRequest, request: Request) -> AskResponse:
    if not _authorized(request, settings.backend_shared_secret):
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not payload.question.strip():
        raise HTTPException(status_code=400, detail="question must not be empty")
    history = [m.model_dump() for m in payload.history] if payload.history else None
    try:
        result = ask_ifrs_advisor_structured(payload.question, history, payload.file_ids)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="The advisor request failed") from exc
    return AskResponse(**result)


@app.post("/api/files")
async def upload_file(request: Request) -> dict:
    if not _authorized(request, settings.backend_shared_secret):
        raise HTTPException(status_code=401, detail="Unauthorized")
    filename = request.headers.get("x-filename", "case-document")
    content_type = request.headers.get("content-type", "application/octet-stream")
    total = 0
    temp_path: Path | None = None
    try:
        fd, raw_path = tempfile.mkstemp()
        os.close(fd)
        temp_path = Path(raw_path)
        with temp_path.open("wb") as output:
            async for chunk in request.stream():
                total += len(chunk)
                if total > 25 * 1024 * 1024:
                    raise HTTPException(status_code=413, detail="Files must be 25 MB or smaller")
                output.write(chunk)
        if total == 0:
            raise HTTPException(status_code=400, detail="File is empty")
        file_id = await asyncio.to_thread(upload_case_file, temp_path, filename, content_type)
        return {"file_id": file_id, "name": filename, "size": total, "type": content_type}
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


@app.post("/api/admin/knowledge/ingest")
async def ingest_knowledge(request: Request) -> dict:
    if not _authorized(request, settings.ingestion_token):
        raise HTTPException(status_code=401, detail="Unauthorized")
    filename = request.headers.get("x-filename", "International-GAAP-2026.pdf")
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
        file_id, vector_store_id = await asyncio.to_thread(ingest_knowledge_file, temp_path, filename)
        return {"status": "completed", "file_id": file_id, "vector_store_id": vector_store_id, "bytes": total}
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()
