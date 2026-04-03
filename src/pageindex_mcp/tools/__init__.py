"""Tool function exports — imported by server.py for registration."""

from .documents import (
    find_relevant_documents,
    get_document,
    get_document_image,
    get_document_structure,
    get_page_content,
    recent_documents,
    remove_document,
    sync_preloaded_documents,
)
from .processing import (
    process_document,
    upload_and_process_document,
)

__all__ = [
    "find_relevant_documents",
    "get_document",
    "get_document_image",
    "get_document_structure",
    "get_page_content",
    "process_document",
    "recent_documents",
    "remove_document",
    "sync_preloaded_documents",
    "upload_and_process_document",
]
