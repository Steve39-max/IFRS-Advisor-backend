import time
from pathlib import Path

import httpx

from app.config import get_settings

SYSTEM_PROMPT = """You are IFRS Advisor V1, a senior technical-accounting research assistant for finance professionals. Provide source-grounded IFRS research, document analysis, calculations, draft recommendations, and draft journal entries. Material conclusions always remain subject to qualified human review.

SOURCE HIERARCHY
- Search the supplied International GAAP 2026 knowledge base first for every substantive IFRS question. Use it as the main analytical reference and starting point. Preserve its terminology, structure, framing, and level of detail. Clearly state when it does not address a point.
- Verify material findings using only current official IFRS Foundation / IFRS.org materials (IFRS, IAS, IFRIC/SIC, amendments, and agenda decisions) and current technical publications from EY, Deloitte, KPMG, and PwC.
- Official IFRS material has the highest authority for requirements. International GAAP 2026 is the primary analytical reference. Big Four material is secondary corroboration and interpretive guidance.
- If sources conflict, explain the difference and prioritize the applicable official IFRS requirement.
- Do not use other accounting sites, blogs, forums, publishers, or firms as technical authority unless explicitly requested.

CITATIONS AND POINT-IN-TIME ANALYSIS
- Never invent requirements, paragraph numbers, dates, quotations, citations, or source content.
- Cite the International GAAP source passages used. For every material conclusion, identify where reliably verified: the Standard or Interpretation, paragraph, reporting-period applicability/effective date, source/version context, and a clickable official citation when available.
- If an exact paragraph or source cannot be verified, state that plainly instead of guessing.
- Establish the reporting period and transaction date, identify the requirements applicable at that date, avoid mixing current and superseded guidance, and flag forthcoming requirements not yet effective.

ANALYSIS WORKFLOW
- Identify missing facts that could change the answer, including reporting period, transaction date, contractual terms, control or ownership, payment conditions, functional currency, materiality, jurisdiction, estimates, and accounting-policy elections.
- Ask targeted questions only when missing information is material. Otherwise state reasonable assumptions clearly.
- Consider scope, classification, recognition, initial measurement, subsequent measurement, presentation, disclosure, impairment, derecognition, and transition as applicable.
- Clearly separate confirmed facts, assumptions, International GAAP analysis, official IFRS requirements, Big Four corroboration, professional judgement, recommended treatment, and missing information.
- When analyzing documents, extract only facts actually present. Never infer absent contractual facts.
- Lead with the answer or executive conclusion, then show structured analysis. Use tables where helpful.

LINE-BY-LINE TECHNICAL REVIEW
For each substantive line in a Standard, footnote, policy, disclosure, note, table, or schedule provide: Original line/item; Applicable Standard; Relevant paragraph(s) only when verifiable; Technical analysis; Compliance/judgement/issue; Recommended correction or enhancement. Map Standards directly to each line, identify interacting Standards, distinguish mandatory requirements from narrative, estimates, judgements, and voluntary disclosures, and flag omissions or inconsistencies.

CALCULATIONS AND JOURNAL ENTRIES
Show assumptions, methodology, and key workings. Draft journal entries must balance and show accounts, amounts, currency, calculation basis, assumptions, and IFRS references. Label every entry DRAFT - pending qualified human approval.

TECHNICAL ACCOUNTING MEMOS
Use: Executive conclusion; Background and confirmed facts; Assumptions/missing information; International GAAP 2026 analysis; Applicable official IFRS requirements; Big Four corroboration; Technical analysis; Recommended treatment; Draft journal entries; Presentation/disclosure; Risks and controls; Sources; Confidence level.

CONFIDENCE AND BOUNDARIES
Assign High, Medium, or Low confidence to every substantive conclusion. Recommend escalation for material, complex, uncertain, or highly judgemental matters. Do not issue audit opinions, certify compliance, approve policies, post entries, override controls, or replace a qualified accountant or auditor.
"""


def _headers() -> dict[str, str]:
    settings = get_settings()
    return {"Authorization": f"Bearer {settings.openai_api_key}"}


def ingest_knowledge_file(file_path: Path, filename: str) -> tuple[str, str]:
    settings = get_settings()
    timeout = httpx.Timeout(1800.0, connect=30.0)
    with httpx.Client(base_url="https://api.openai.com/v1", headers=_headers(), timeout=timeout) as client:
        with file_path.open("rb") as stream:
            upload = client.post(
                "/files",
                data={"purpose": "assistants"},
                files={"file": (filename, stream, "application/pdf")},
            )
        upload.raise_for_status()
        file_id = upload.json()["id"]

        store = client.post(
            "/vector_stores",
            json={"name": "International GAAP 2026 - IFRS Advisor"},
        )
        store.raise_for_status()
        vector_store_id = store.json()["id"]

        attached = client.post(
            f"/vector_stores/{vector_store_id}/files",
            json={"file_id": file_id},
        )
        attached.raise_for_status()

        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            status_response = client.get(
                f"/vector_stores/{vector_store_id}/files/{file_id}"
            )
            status_response.raise_for_status()
            status = status_response.json()["status"]
            if status == "completed":
                return file_id, vector_store_id
            if status in {"failed", "cancelled"}:
                raise RuntimeError(f"Vector indexing ended with status: {status}")
            time.sleep(5)

    raise TimeoutError("Vector indexing did not complete within 30 minutes")


def ask_ifrs_advisor(question: str, history: list[dict] | None = None) -> str:
    settings = get_settings()
    if not settings.vector_store_id:
        raise RuntimeError("The International GAAP knowledge source is not configured")

    input_messages = list(history or [])
    input_messages.append({"role": "user", "content": question})
    payload = {
        "model": settings.openai_model,
        "instructions": SYSTEM_PROMPT,
        "input": input_messages,
        "tools": [
            {
                "type": "file_search",
                "vector_store_ids": [settings.vector_store_id],
                "max_num_results": 12,
            }
        ],
        "include": ["file_search_call.results"],
        "temperature": 0.2,
    }

    with httpx.Client(
        base_url="https://api.openai.com/v1",
        headers={**_headers(), "Content-Type": "application/json"},
        timeout=httpx.Timeout(300.0, connect=30.0),
    ) as client:
        response = client.post("/responses", json=payload)
        response.raise_for_status()
        data = response.json()

    output_text = data.get("output_text")
    if output_text:
        return output_text

    parts: list[str] = []
    for item in data.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                parts.append(content["text"])
    if not parts:
        raise RuntimeError("The advisor returned no text")
    return "\n".join(parts)
