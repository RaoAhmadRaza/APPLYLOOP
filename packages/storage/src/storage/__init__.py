"""The one blob store: raw résumé uploads, and later M5's generated PDFs.

A shared package rather than a module inside either app, because both ends need it and
the dependency direction forbids the shortcut: the API writes the upload, the worker
reads it back to extract text, and `api -> workers` is not allowed (see the root
pyproject). One implementation, two importers.

S3-compatible, not S3-specific. §7.1 picks Cloudflare R2, which speaks the S3 API, and
so do MinIO and S3 itself — `STORAGE_ENDPOINT_URL` is the only thing that changes.

**Unconfigured is a supported state**, the same interlock the aggregator uses without a
proxy: `is_configured()` is false, callers refuse cleanly, and nothing else notices. A
local checkout with no bucket still boots, still runs the suite, and still ingests jobs.
"""

from storage.client import (
    StorageError,
    build_document_key,
    build_key,
    delete,
    get,
    is_configured,
    presigned_get,
    put,
)
from storage.settings import Settings, get_settings

__all__ = [
    "Settings",
    "StorageError",
    "build_document_key",
    "build_key",
    "delete",
    "get",
    "get_settings",
    "is_configured",
    "presigned_get",
    "put",
]
