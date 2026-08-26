import json
import os
import sys

import httpx

BACKEND_URL = os.environ.get("ADVISOR_BACKEND_URL", "https://ifrs-advisor-backend-prod-production.up.railway.app")
SECRET = os.environ.get("BACKEND_SHARED_SECRET")


def main() -> int:
    if not SECRET:
        print("Set BACKEND_SHARED_SECRET before running evals.", file=sys.stderr)
        return 2
    with open("evals/cases.json", encoding="utf-8") as stream:
        cases = json.load(stream)
    failures = []
    with httpx.Client(timeout=600.0) as client:
        for case in cases:
            response = client.post(
                f"{BACKEND_URL}/api/ask",
                headers={"Authorization": f"Bearer {SECRET}"},
                json={"question": case["question"], "history": [], "file_ids": []},
            )
            response.raise_for_status()
            payload = response.json()
            answer = payload["answer"]
            missing = [term for term in case.get("must_include", []) if term.lower() not in answer.lower()]
            if case.get("must_have_citations") and not payload.get("citations"):
                missing.append("structured citation")
            if missing:
                failures.append({"name": case["name"], "missing": missing})
            print(f"{'PASS' if not missing else 'FAIL'} {case['name']}")
    if failures:
        print(json.dumps(failures, indent=2))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
