#!/usr/bin/env python3
"""AEC document error finder.

The participant contract is intentionally small: output.json contains an
``errors`` array, and each error names the document containing the incorrect
information. The deterministic pass handles common schedule/spec tables; one
bounded OpenRouter review covers less regular document layouts.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


MAX_SECONDS = 9 * 60 + 30
MAX_CALLS = 300
MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")


class Budget:
    def __init__(self) -> None:
        self.started = time.monotonic()
        self.calls = 0

    def reserve(self) -> bool:
        if self.calls >= MAX_CALLS or time.monotonic() - self.started >= MAX_SECONDS:
            return False
        self.calls += 1
        return True


def clean(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def read_document(path: Path) -> list[str]:
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        return [clean(page.extract_text() or "") for page in PdfReader(str(path)).pages]
    return [clean(path.read_text(encoding="utf-8", errors="replace"))]


def read_documents(dataset_dir: Path) -> dict[str, list[str]]:
    allowed = {".pdf", ".txt", ".md", ".csv", ".json", ".html"}
    control_files = {"files.json", "manifest.json"}
    result: dict[str, list[str]] = {}
    for path in sorted(dataset_dir.iterdir()):
        if not path.is_file() or path.name.lower() in control_files or path.suffix.lower() not in allowed:
            continue
        try:
            pages = read_document(path)
            if any(page.strip() for page in pages):
                result[path.name] = pages
        except Exception as exc:  # noqa: BLE001
            print(f"could not read {path.name}: {exc}", file=sys.stderr)
    return result


def page_texts(documents: dict[str, list[str]]) -> list[tuple[str, int, str]]:
    return [(name, page_no, text) for name, pages in documents.items() for page_no, text in enumerate(pages, 1)]


def error(document: str, category: str, location: str, description: str) -> dict[str, str]:
    return {
        "document": document,
        "category": category,
        "location": location,
        "description": description,
    }


def number(value: str) -> float:
    return float(value.replace(",", ""))


def first_number(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, re.I)
    return match.group(1) if match else None


def spec_sentences(documents: dict[str, list[str]]) -> list[tuple[str, int, str]]:
    sentences: list[tuple[str, int, str]] = []
    for name, page, text in page_texts(documents):
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
            sentence = sentence.strip(" |-")
            if sentence:
                sentences.append((name, page, sentence))
    return sentences


def pipe_rows(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not (line.startswith("|") and line.endswith("|")):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells and not all(set(cell) <= set("-:") for cell in cells):
            rows.append(cells)
    return rows


def pipe_tables(text: str) -> list[list[list[str]]]:
    tables: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in text.splitlines() + [""]:
        line = line.strip()
        if line.startswith("|") and line.endswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if cells and all(set(cell) <= set("-:") for cell in cells):
                continue
            current.append(cells)
        elif current:
            if len(current) >= 2:
                tables.append(current)
            current = []
    return tables


def row_value(row: dict[str, str], names: tuple[str, ...], pattern: str) -> str | None:
    for name in names:
        value = first_number(pattern, row.get(name, ""))
        if value is not None:
            return value
    return None


def deterministic_errors(documents: dict[str, list[str]]) -> list[dict[str, str]]:
    """Find obvious table-to-normative-text mismatches without guessing."""
    specs = spec_sentences(documents)
    findings: list[dict[str, str]] = []
    for name, pages in documents.items():
        for page_no, text in enumerate(pages, 1):
            for rows in pipe_tables(text):
                headers = [re.sub(r"\s+", " ", cell.lower()).strip() for cell in rows[0]]
                if not any(key in " ".join(headers) for key in ("fire", "flow", "rating", "fixture", "mark")):
                    continue
                for values in rows[1:]:
                    if len(values) != len(headers):
                        continue
                    row = dict(zip(headers, values))
                    mark = next((value for key, value in row.items() if "mark" in key), "")
                    if not re.search(r"\b[A-Z]{1,5}-\d{1,4}\b", mark):
                        continue
                    row_text = " ".join(values)
                    location = next((value for key, value in row.items() if "location" in key), "")
                    subject = f"{mark} {location} {row_text}".strip()

                    scheduled_minutes = row_value(row, ("fire rating", "rating", "fire"), r"(\d+(?:\.\d+)?)\s*(?:min|minute|minutes)")
                    if scheduled_minutes is not None:
                        room_terms = re.findall(r"[a-z]+", f"{location} {row_text}".lower())
                        candidates = [
                            (spec_name, spec_page, sentence)
                            for spec_name, spec_page, sentence in specs
                            if spec_name != name and "minute" in sentence.lower()
                            and any(term in sentence.lower() for term in room_terms if len(term) >= 5)
                        ]
                        required = next((first_number(r"(\d+(?:\.\d+)?)\s*(?:-?minute|minutes?)", sentence) for _, _, sentence in candidates), None)
                        if required is not None and number(required) != number(scheduled_minutes):
                            findings.append(error(
                                name,
                                "cross-document-conflict",
                                f"page {page_no}, {mark} {location}".strip(),
                                f"Schedule lists {mark} at {scheduled_minutes} min; the specification requires {required}-minute doors for {location or 'this location'}.",
                            ))

                    scheduled_gpm = row_value(row, ("flow", "gpm", "fixture"), r"(\d+(?:\.\d+)?)\s*gpm")
                    if scheduled_gpm is not None:
                        terms = ["lavatory", "faucet", "service sink", "water closet"]
                        matching_terms = [term for term in terms if term in subject.lower()]
                        candidates = [
                            (spec_name, spec_page, sentence)
                            for spec_name, spec_page, sentence in specs
                            if spec_name != name and "gpm" in sentence.lower()
                            and any(term in sentence.lower() for term in matching_terms)
                        ]
                        required = next((first_number(r"(\d+(?:\.\d+)?)\s*gpm", sentence) for _, _, sentence in candidates), None)
                        if required is not None and number(required) != number(scheduled_gpm):
                            findings.append(error(
                                name,
                                "unit-error",
                                f"page {page_no}, {mark}",
                                f"Fixture schedule lists {mark} at {scheduled_gpm} gpm; the specification requires {required} gpm.",
                            ))
    return findings


def corpus(documents: dict[str, list[str]]) -> str:
    parts: list[str] = []
    for name, pages in documents.items():
        body = "\n".join(f"[page {i}]\n{page}" for i, page in enumerate(pages, 1))
        parts.append(f"===== {name} =====\n{body[:30000]}")
    return "\n\n".join(parts)[:90000]


def parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}


def llm_errors(documents: dict[str, list[str]], budget: Budget) -> list[dict[str, str]]:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key or not budget.reserve() or len(documents) < 2:
        return []
    prompt = """You review an architectural/construction document set with deliberately injected errors.
Find only material errors in these categories: cross-document-conflict, code-violation,
unit-error, or missing-item. Be conservative: omit anything uncertain.

Return ONLY this JSON shape:
{"errors":[{"document":"exact file name containing the INCORRECT information",
"category":"cross-document-conflict|code-violation|unit-error|missing-item",
"location":"page, section, table, mark, or item",
"description":"one sentence quoting the wrong value and the correct value"}]}

Rules:
- The document field must be an exact file name from the supplied set.
- Report the document containing the incorrect value, not the document containing the requirement.
- For conflicts, identify both the wrong and correct values in description.
- Do not duplicate the same error.

DOCUMENT SET:
""" + corpus(documents)
    payload = json.dumps({
        "model": MODEL,
        "temperature": 0,
        "max_tokens": 6000,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    request = Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=75) as response:
            data = json.loads(response.read().decode())
        parsed = parse_json(data["choices"][0]["message"]["content"])
    except (HTTPError, URLError, TimeoutError, KeyError, IndexError, json.JSONDecodeError) as exc:
        print(f"LLM review unavailable: {exc}", file=sys.stderr)
        return []
    valid_categories = {"cross-document-conflict", "code-violation", "unit-error", "missing-item"}
    valid_documents = set(documents)
    results: list[dict[str, str]] = []
    for item in parsed.get("errors", []) if isinstance(parsed, dict) else []:
        if not isinstance(item, dict):
            continue
        document = str(item.get("document", ""))
        category = str(item.get("category", ""))
        location = str(item.get("location", ""))
        description = str(item.get("description", ""))
        if document not in valid_documents or category not in valid_categories or not description:
            continue
        results.append(error(document, category, location[:300], description[:1000]))
    return results


def deduplicate(errors: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str, str]] = set()
    result: list[dict[str, str]] = []
    for item in errors:
        key = (item["document"], item["category"], re.sub(r"\W+", " ", item["description"].lower()).strip())
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result[:1000]


def main() -> int:
    dataset_dir = Path(os.environ.get("DATASET_DIR", "./dataset"))
    output_path = Path(os.environ.get("OUTPUT_PATH", "./output.json"))
    if not dataset_dir.is_dir():
        print(f"DATASET_DIR is not a directory: {dataset_dir}", file=sys.stderr)
        return 2
    budget = Budget()
    documents = read_documents(dataset_dir)
    if not documents:
        print(f"No readable documents found under {dataset_dir}", file=sys.stderr)
        return 2
    errors = deduplicate(deterministic_errors(documents) + llm_errors(documents, budget))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({"errors": errors}, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(errors)} errors to {output_path}; LLM calls: {budget.calls}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
