"""Private document storage behind short-lived signed URLs (FR-8).

Uses boto3 against any S3-compatible endpoint - real AWS S3 in production
(leave S3_ENDPOINT_URL unset), MinIO/LocalStack/moto_server locally (set
S3_ENDPOINT_URL to point at them). The application code never sees a
provider-specific API either way.
"""
import os
import uuid
from datetime import timedelta

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv()

S3_BUCKET = os.getenv("S3_BUCKET", "business-navigators-documents")
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL") or None  # None = real AWS
S3_REGION = os.getenv("S3_REGION", "me-central-1")
# Signed URLs default to 15 minutes - short-lived per the SRS requirement
# ("accessed only via short-lived signed URLs").
SIGNED_URL_TTL_SECONDS = int(os.getenv("SIGNED_URL_TTL_SECONDS", "900"))

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=S3_ENDPOINT_URL,
            region_name=S3_REGION,
            # path-style addressing is what MinIO/LocalStack/moto_server
            # expect; real AWS S3 also accepts it, so this is safe either way.
            config=BotoConfig(s3={"addressing_style": "path"}),
        )
    return _client


def ensure_bucket() -> None:
    """Creates the bucket if it doesn't exist yet. Safe to call on every
    startup - idempotent, and cheap enough not to need a separate
    migration step for local/dev use. A real AWS deployment would
    typically provision the bucket via infra-as-code instead, but calling
    this is harmless there too (falls through on BucketAlreadyOwnedByYou)."""
    client = _get_client()
    try:
        client.head_bucket(Bucket=S3_BUCKET)
    except ClientError:
        kwargs = {"Bucket": S3_BUCKET}
        if S3_REGION and S3_REGION != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": S3_REGION}
        try:
            client.create_bucket(**kwargs)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                raise


def _object_key(tenant_id: str, lead_phone_number: str, filename: str) -> str:
    """Tenant-scoped key path so a bucket can safely be shared across
    tenants if it ever needs to be - the tenant boundary is enforced in
    the key itself, on top of the database-level RLS boundary on the
    `documents` table that maps to these keys."""
    safe_name = os.path.basename(filename or "document")
    return f"{tenant_id}/{lead_phone_number}/{uuid.uuid4().hex}_{safe_name}"


def upload_document(
    tenant_id: str, lead_phone_number: str, filename: str, content: bytes, content_type: str = None
) -> str:
    """Stores the raw file privately (no public ACL) and returns its
    storage key - callers persist this key via database.create_document()
    rather than the bytes themselves."""
    ensure_bucket()
    key = _object_key(tenant_id, lead_phone_number, filename)
    _get_client().put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=content,
        ContentType=content_type or "application/octet-stream",
    )
    return key


def get_signed_url(storage_key: str, expires_in: int = None) -> str:
    """A short-lived, single-object GET URL - the only way this app ever
    hands out document access, per the SRS's private-storage requirement.
    Callers are responsible for logging who requested it (see
    database.log_document_access) before/after calling this."""
    return _get_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": S3_BUCKET, "Key": storage_key},
        ExpiresIn=expires_in or SIGNED_URL_TTL_SECONDS,
    )


def delete_document(storage_key: str) -> None:
    _get_client().delete_object(Bucket=S3_BUCKET, Key=storage_key)
