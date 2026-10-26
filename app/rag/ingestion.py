"""Compatibility wrapper. Ingestion lives in app.retrieval.ingestion."""

from app.retrieval.ingestion import *  # noqa: F403
from app.retrieval.ingestion import main

if __name__ == "__main__":
    main()
