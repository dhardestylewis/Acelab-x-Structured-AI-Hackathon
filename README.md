# Acelab contradiction finder

This repository contains a submission-ready `run.sh` entrypoint. It reads PDF, text, Markdown, CSV, JSON, and HTML documents from `DATASET_DIR`, finds conservative construction-document errors, and writes the participant contract to `OUTPUT_PATH`.

## Run

```bash
export DATASET_DIR=/path/to/document-set
export OUTPUT_PATH=/path/to/findings.json
export OPENROUTER_API_KEY=your-key
./run.sh
```

`OPENROUTER_API_KEY` is optional. Without it, the deterministic extractor reports common schedule/spec mismatches. With it, one bounded reviewer call adds conservative comparisons for less regular layouts.

Optional settings:

```bash
export OPENROUTER_MODEL=openai/gpt-4o-mini
```

The program enforces fewer than 300 LLM calls and a 9 minute 30 second internal deadline, leaving a small margin under the event's 10-minute limit. It never reads or writes an answer key.

## Output schema

```json
{
  "errors": [
    {
      "id": "F-0001",
      "document": "schedule.pdf",
      "category": "cross-document-conflict",
      "location": "page 1, D-202 Mechanical 101",
      "description": "Schedule lists D-202 at 45 min; the specification requires 90-minute doors for Mechanical 101."
    }
  ],
  "metadata": {}
}
```

The original event schema and starter repository were not present in the supplied workspace, so this is a documented local schema that should be adapted once the organizers provide the starter repository.
