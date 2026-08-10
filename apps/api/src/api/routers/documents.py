"""Open a generated PDF.

`documents.storage_url` is an object KEY, not a URL, and the bucket is private. So the
browser cannot be handed the stored value and the API should not stream megabytes through
itself either — a redirect to a short-lived signed URL is the shape that costs neither.

The Drive mirror is the fallback rather than the default. `gdrive_url` works today and
survives this process dying, but it depends on Drive being configured and it is a
link-shared URL with no expiry; the signed one is the honest answer where both exist.
"""

from uuid import UUID

import storage
from db.models import Document
from fastapi import APIRouter, HTTPException
from fastapi import status as http
from fastapi.responses import RedirectResponse

from api.deps import SessionDep

router = APIRouter(prefix="/documents", tags=["documents"])

# Long enough to open a PDF and read it, short enough that a URL copied out of a browser
# history is not a lasting hole in a private bucket.
_EXPIRES_SECONDS = 900


@router.get("/{document_id}/download")
async def download(document_id: UUID, session: SessionDep) -> RedirectResponse:
    row = await session.get(Document, document_id)
    if row is None:
        raise HTTPException(status_code=http.HTTP_404_NOT_FOUND, detail="not found")

    if storage.is_configured():
        try:
            url = storage.presigned_get(row.storage_url, expires_in=_EXPIRES_SECONDS)
        except storage.StorageError as error:
            raise HTTPException(
                status_code=http.HTTP_502_BAD_GATEWAY, detail="could not sign a document URL"
            ) from error
    elif row.gdrive_url:
        url = row.gdrive_url
    else:
        # Unconfigured storage is a supported state everywhere else here; it should read
        # as "this deployment cannot do that yet" rather than as a crash.
        raise HTTPException(
            status_code=http.HTTP_503_SERVICE_UNAVAILABLE,
            detail="object storage is not configured and this document has no Drive mirror",
        )

    # 307 rather than 302: the method is preserved, and nothing downstream may cache a
    # redirect whose target expires in fifteen minutes.
    return RedirectResponse(url, status_code=http.HTTP_307_TEMPORARY_REDIRECT)
