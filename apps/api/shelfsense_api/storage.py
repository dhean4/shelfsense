"""Photo object storage on any S3-compatible service (RustFS locally, ADR-0010)."""

from typing import Any

from aiobotocore.session import AioSession, get_session

from shelfsense_api.config import Settings


class PhotoStore:
    """Put, get and presign objects in the photos bucket."""

    def __init__(self, settings: Settings, session: AioSession | None = None) -> None:
        """Bind to the bucket and endpoint in ``settings``."""
        self._settings = settings
        self._session = session or get_session()
        self._bucket = settings.s3_bucket_photos

    def _client(self) -> Any:
        return self._session.create_client(
            "s3",
            region_name=self._settings.s3_region,
            endpoint_url=self._settings.s3_endpoint_url,
            aws_access_key_id=self._settings.s3_access_key,
            aws_secret_access_key=self._settings.s3_secret_key,
        )

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        """Upload bytes under ``key``."""
        async with self._client() as s3:
            await s3.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)

    async def get(self, key: str) -> bytes:
        """Download the object body."""
        async with self._client() as s3:
            response = await s3.get_object(Bucket=self._bucket, Key=key)
            async with response["Body"] as stream:
                body: bytes = await stream.read()
                return body

    async def presigned_get_url(self, key: str) -> str:
        """A time-limited URL a browser can fetch directly."""
        async with self._client() as s3:
            url: str = await s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=self._settings.s3_presign_seconds,
            )
            return url

    async def ensure_bucket(self) -> None:
        """Create the bucket if missing (tests and first boot)."""
        async with self._client() as s3:
            try:
                await s3.head_bucket(Bucket=self._bucket)
            except s3.exceptions.ClientError:
                await s3.create_bucket(Bucket=self._bucket)
