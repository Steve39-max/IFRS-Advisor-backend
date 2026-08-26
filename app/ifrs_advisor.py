import time
from pathlib import Path

import httpx

from app.config import get_settings

SYSTEM_PROMPT = """You are IFRS Advisor V1, a senior technical-accounting research assistant for finance professionals. Provide source-grounded IFRS research, document analysis, calculations, draft recommendations, and draft journal entries. Material conclusions always remain subject to qualified human review.

SOURCE HIERARCHY
1. Search International GAAP 2026 first for every substantive IFRS question and use it as the main analytical reference.
2. Verify material findings using current official IFRS Foundation / IFRS.org materials.
3. Corroborate with current EY, Deloitte, KPMG, and PwC technical publications when available.
Official IFRS material controls requirements if sources conflict. Do not use other external accounting sources unless explicitly requested.

Never invent requirements, paragraph numbers, dates, quotations, or citations. Cite retrieved International GAAP passages and authoritative web sources. Establish the reporting period and transaction date, distinguish current from superseded guidance, and flag forthcoming requirements.

Identify only missing facts that could change the accounting. Clearly separate confirmed facts, assumptions, International GAAP analysis, official requirements, Big Four corroboration, professional judgement, recommended treatment, and missing information. Consider scope, classification, recognition, measurement, presentation, disclosure, impairment, derecognition, and transition as applicable.

For line-by-line reviews, map each substantive line to the applicable Standard and reliably verified paragraph, then state the technical analysis, compliance/judgement issue, and recommended correction. Never infer contractual facts absent from uploaded documents.

For calculations, use the calculation tool, show assumptions, methodology, and key workings. Draft journal entries must balance and show accounts, amounts, currency, basis, assumptions, and IFRS references. Label every entry DRAFT - pending qualified human approval.

For technical memos use: Executive conclusion; Background and confirmed facts; Assumptions/missing information; International GAAP 2026 analysis; Applicable official IFRS requirements; Big Four corroboration; Technical analysis; Recommended treatment; Draft journal entries; Presentation/disclosure; Risks and controls; Sources; Confidence level.

Assign High, Medium, or Low confidence to substantive conclusions. Recommend escalation for material, complex, uncertain, or highly judgemental matters. Never issue audit opinions, certify compliance, approve policies, post entries, override controls, or replace a qualified accountant or auditor.
"""

ALLOWED_DOMAINS = [
    "ifrs.org",
    "ey.com",
    "deloitte.com",
    "kpmg.com",
    "pwc.com",
]


def _headers() -> dict[str, str]:
    settings = get_settings()
    return {"Authorization": f"Bearer {settings.openai_api_key}"}


def ingest_knowledge_file(file_path: Path, filename: str) -> tuple[str, str]:
    timeout = httpx.Timeout(1800.0, connect=30.0)
    with httpx.Client(base_url="https://api.openai.com/v1", headers=_headers(), timeout=timeout) as client:
        with file_path.open("rb") as stream:
            upload = client.post("/files", data={"purpose": "assistants"}, files={"file": (filename, stream, "application/pdf")})
        upload.raise_for_status()
        file_id = upload.json()["id"]
        store = client.post("/vector_stores", json={"name": "International GAAP 2026 - IFRS Advisor"})
        store.raise_for_status()
        vector_store_id = store.json()["id"]
        attached = client.post(f"/vector_stores/{vector_store_id}/files", json={"file_id": file_id})
        attached.raise_for_status()
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            status_response = client.get(f"/vector_stores/{vector_store_id}/files/{file_id}")
            status_response.raise_for_status()
            status = status_response.json()["status"]
            if status == "completed":
                return file_id, vector_store_id
            if status in {"failed", "cancelled"}:
                raise RuntimeError(f"Vector indexing ended with status: {status}")
            time.sleep(5)
    raise TimeoutError("Vector indexing did not complete within 30 minutes")


def upload_case_file(file_path: Path, filename: str, content_type: str) -> str:
    with httpx.Client(base_url="https://api.openai.com/v1", headers=_headers(), timeout=300.0) as client:
        with file_path.open("rb") as stream:
            response = client.post(
                "/files",
                data={"purpose": "user_data"},
                files={"file": (filename, stream, content_type)},
            )
        response.raise_for_status()
        return response.json()["id"]


def _collect_response(data: dict) -> dict:
    text_parts: list[str] = []
    citations: list[dict] = []
    sources: list[dict] = []
    seen_files: set[tuple[str, int]] = set()
    seen_urls: set[str] = set()

    for item in data.get("output", []):
        if item.get("type") == "web_search_call":
            for source in (item.get("action") or {}).get("sources", []):
                url = source.get("url")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    sources.append({"title": source.get("title") or url, "url": url})
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") != "output_text":
                continue
            text = content.get("text") or ""
            text_parts.append(text)
            for annotation in content.get("annotations", []):
                if annotation.get("type") == "file_citation":
                    key = (annotation.get("file_id", ""), annotation.get("index", 0))
                    if key not in seen_files:
                        seen_files.add(key)
                        citations.append({
                            "filename": annotation.get("filename") or "Knowledge source",
                            "index": annotation.get("index"),
                        })
                elif annotation.get("type") == "url_citation":
                    url = annotation.get("url")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        sources.append({"title": annotation.get("title") or url, "url": url})

    if not text_parts:
        raise RuntimeError("The advisor returned no text")
    return {"answer": "\n".join(text_parts), "citations": citations, "sources": sources}


def ask_ifrs_advisor(question: str, history: list[dict] | None = None, file_ids: list[str] | None = None) -> dict:
    settings = get_settings()
    if not settings.vector_store_id:
        raise RuntimeError("The International GAAP knowledge source is not configured")

    input_messages = list(history or [])
    current_content: list[dict] = [{"type": "input_text", "text": question}]
    for file_id in file_ids or []:
        current_content.append({"type": "input_file", "file_id": file_id})
    input_messages.append({"role": "user", "content": current_content})

    payload = {
        "model": settings.openai_model,
        "instructions": SYSTEM_PROMPT,
        "input": input_messages,
        "tools": [
            {"type": "file_search", "vector_store_ids": [settings.vector_store_id], "max_num_results": 12},
            {"type": "web_search", "filters": {"allowed_domains": ALLOWED_DOMAINS}, "search_context_size": "medium"},
            {"type": "code_interpreter", "container": {"type": "auto"}},
        ],
        "include": [
            "file_search_call.results",
            "web_search_call.action.sources",
            "code_interpreter_call.outputs",
        ],
        "store": False,
    }

    with httpx.Client(
        base_url="https://api.openai.com/v1",
        headers={**_headers(), "Content-Type": "application/json"},
        timeout=httpx.Timeout(600.0, connect=30.0),
    ) as client:
        response = client.post("/responses", json=payload)
        response.raise_for_status()
        return _collect_response(response.json())
