"""Start the API. Embed the docs only when the Chroma collection is empty."""

from __future__ import annotations

import os
import sys


def main() -> None:
    from app.retrieval.ingestion import get_chroma_collection, ingest_documents

    if get_chroma_collection().count() == 0:
        print("Chroma collection is empty; ingesting technical docs.", flush=True)
        ingest_documents(reset=False)
    else:
        print("Chroma index already present; skipping ingestion.", flush=True)

    os.execvp(
        sys.executable,
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
        ],
    )


if __name__ == "__main__":
    main()
