"""Object-storage configuration.

Its own `Settings` rather than a block on the API's or the worker's, because both of
them import this package and neither owns it. Same flat env namespace as everything
else — `extra="ignore"` is what lets three Settings classes read one `.env`.

Every field is optional. Unconfigured is a supported state (see `client.is_configured`),
so there is nothing here whose absence should stop a process booting.
"""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    # e.g. https://<account>.r2.cloudflarestorage.com
    storage_endpoint_url: str | None = None
    # SecretStr: `repr(get_settings())` reaches a log line or a Sentry breadcrumb
    # eventually, and these are credentials (Part 13 rule 9).
    storage_access_key_id: SecretStr | None = None
    storage_secret_access_key: SecretStr | None = None
    storage_bucket: str | None = None
    # R2 has no regions, but boto3's SigV4 signer requires one to compute a signature.
    # "auto" is the value Cloudflare's own documentation uses.
    storage_region: str = "auto"

    # A résumé is a handful of pages. The cap exists so a 400 MB upload cannot exhaust
    # the API process's memory before anything has had a chance to reject it.
    max_resume_bytes: int = 10 * 1024 * 1024

    # --- M5: the Google Drive mirror -------------------------------------------------
    # All four optional and interlocked together, like the S3 settings above:
    # unconfigured, `documents.gdrive_url` stays NULL and the document still ships.
    #
    # **The mirror uploads as the user, not as a service account.** A service account has
    # had a 0 GB Drive quota since 2023 and cannot own a file — it must write into a
    # Shared Drive, which is a Google Workspace feature that a personal account does not
    # have. So these are OAuth user credentials, and the documents are owned by the user.
    #
    # `scripts/gdrive_token.py` produces all four in one run.
    gdrive_folder_id: str | None = None
    gdrive_client_id: str | None = None
    gdrive_client_secret: SecretStr | None = None
    # Long-lived. The access token is minted from it per session and never stored.
    gdrive_refresh_token: SecretStr | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
