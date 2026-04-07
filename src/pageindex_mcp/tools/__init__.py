"""Tool function exports — imported by server.py for registration."""

from .documents import (
    find_relevant_documents,
    get_document,
    get_document_structure,
    get_page_content,
    recent_documents,
)

__all__ = [
    "find_relevant_documents",
    "get_document",
    "get_document_structure",
    "get_page_content",
    "recent_documents",
]
