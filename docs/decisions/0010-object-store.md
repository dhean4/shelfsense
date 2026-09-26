# ADR-0010: RustFS replaces MinIO for local and test object storage

- **Status**: accepted
- **Date**: 2026-09-26
- **Phase**: P9 (found by the first CI run on GitHub)

## Context

ADR-0001 chose MinIO as the local S3-compatible store, and the integration suite started
a MinIO testcontainer. The first CI run on a clean runner failed: `minio/minio` (and the
`minio/mc` client) could no longer be pulled from Docker Hub or Quay without
authentication. The images had only worked locally because they were already cached.

## Decision

Use **RustFS** (`rustfs/rustfs`, Apache-2.0, S3-compatible, ~250 MB) for the Compose
stack and the integration tests, and `amazon/aws-cli` for the one-shot bucket creation.
The API talks to it through the same `aiobotocore` client as before; `put`, `get`,
`head_bucket`/`create_bucket` and presigned GET URLs were verified before switching.
Environment variables were renamed from `MINIO_*` to `S3_*` since nothing about them is
MinIO-specific.

_Rejected_: LocalStack (a gigabyte image for one service), pinning an old MinIO tag
(none are pullable either), a filesystem-backed fake (would not exercise presigned URLs).

## Consequences

- Fresh checkouts and CI runners pull public images only. The Compose service is now
  named `s3`; Langfuse's S3 endpoints point at it.
- Local data in the old `minio-data` volume is not migrated; `make demo` regenerates it.
- Production is unaffected: Fly's Tigris or any S3 works through the same settings.
