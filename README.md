# Agentic Technical Support (RAG)

Python agent that classifies IT support tickets, searches technical documentation in ChromaDB via Gemini function calling, and returns evidence-grounded troubleshooting recommendations — or a fixed safe fallback when evidence is insufficient.

## Stack

- Python
- Gemini API (`google-genai`) for LLM, embeddings, and function calling
- ChromaDB (local persistent client, cosine distance)
- FastAPI / Pydantic (API schemas ready; agent logic in `app/`)
- pytest

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Set `GOOGLE_API_KEY` in `.env`.

## Ingest documentation

```bash
python -m app.rag.ingestion
```

Source docs: `data/technical_docs/`.  
Persistent index: `data/chroma/`.

## Run the agent

```bash
python -m app.agent
```

Programmatic entry point: `handle_support_ticket()` in `app/agent.py`.

## Tests

```bash
pytest tests -q
```

Live Gemini / embedding tests are skipped unless `GOOGLE_API_KEY` is set.

## Notes

- Retrieval uses cosine distance (lower is closer).
- Evidence must clear `relevance_max_distance` before grounded recommendations are generated.
- If retrieval is empty or irrelevant, the application returns a fixed fallback message without asking Gemini to invent procedures.
