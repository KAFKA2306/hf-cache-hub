# Artifact storage acceptance

This document records the evidence boundary for issue #17. A test is PASS only when the named bytes and hashes were actually observed. Missing remote transfer evidence is never inferred from local tests.

## CI synthetic contract

`tests/test_artifact_acceptance_e2e.py` creates a deterministic 100 MiB artifact and three temporary Git repositories: one producer and two consumers.

The test verifies all of the following in one process:

1. the producer commits only `.gitignore`, README, and the thin artifact manifest;
2. the 100 MiB file is absent from `git ls-files` and the committed producer is clean;
3. a fake Storage Bucket copy has the same SHA-256 as the producer file;
4. consumer A resolves into the shared content-addressed cache and reports exactly 100 MiB transferred;
5. consumer B resolves the same SHA object through the same cache and reports `cache_hit=true` and `transferred_bytes=0`;
6. both materialized consumer files have the producer SHA-256;
7. both consumer repositories remain clean because materialized bytes are outside tracked source paths;
8. the test emits one `ARTIFACT_ACCEPTANCE_METRICS=<json>` line containing observed `.git` sizes, transfer counts, cache path, artifact size, and SHA-256.

Run:

```bash
python -m unittest tests.test_artifact_acceptance_e2e -v
```

Observed in GitHub Actions CI run #19 on 2026-08-20:

- status: `PASS_LOCAL_SYNTHETIC`
- artifact size: `104857600` bytes
- SHA-256: `4cbf988462cc3ba2e10e3aae9f5268546aa79016359fb45be7dd199c073125c0`
- producer `.git`: `28676` bytes
- consumer A `.git`: `28378` bytes
- consumer B `.git`: `28378` bytes
- fake remote download calls: `1`
- first resolve transferred bytes: `104857600`
- second resolve transferred bytes: `0`
- second resolve cache hit: `true`
- all 41 repository tests passed

The shared cache path emitted by CI is intentionally ephemeral runner state and is not a stable machine-independent path.

This is intentionally a local/CI contract test. Its fake downloader does **not** prove Hugging Face Storage Bucket upload or readback.

## Real Storage Bucket acceptance

The real acceptance remains separate and must use the same publish/resolve code paths as production.

Required observations:

- a deterministic artifact of at least 100 MiB;
- local producer size and SHA-256;
- `artifact_manager.py publish` returning `PUBLISHED` only after remote readback verification;
- first `artifact_cache.py resolve` from an empty shared cache;
- second resolve from another clean repository using the same cache;
- producer, remote readback, cache, and both consumer SHA-256 values all equal;
- observed transfer bytes where the API exposes them; unavailable transfer metrics must be recorded as unavailable, not zero;
- no credential/token in Git, manifest, or logs;
- cleanup of temporary acceptance objects when the test is synthetic.

The repository contains a dedicated 100 MiB GitHub Actions acceptance workflow on branch `issue-13-real-bucket-acceptance`. It must not be cited as PASS until a successful run is observed.

## Real Gaussian Splat acceptance

The production-path test is:

```text
AutoPhotogrammetry successful run
  -> publish-splat
  -> hf-cache-hub artifact publish/readback
  -> shared artifact cache resolve
  -> vrmine artifact-backed materializer
  -> Library/VRMine/GaussianSources/<id>.ply
```

Record the exact AutoPhotogrammetry commit, run identifier, PLY size/SHA-256, remote URI, first/second resolve evidence, vrmine commit, and final materialized SHA-256.

The producer integration and consumer capability are implemented, but the canonical vrmine registry must not be switched away from its current source URLs until those exact PLY objects have been successfully published and read back from the Storage Bucket.

## Current evidence boundary — 2026-08-20

| Boundary | State | Evidence |
| --- | --- | --- |
| Declarative artifact manifest | PASS | hf-cache-hub #12 / PR #18 |
| Publish/readback implementation | PASS_CODE | hf-cache-hub PR #19, CI run #15 |
| Real >=100 MiB Storage Bucket round trip | NOT_OBSERVED | issue #13 remains open |
| Shared content-addressed local cache | PASS | hf-cache-hub #14 / PR #20, CI run #17 |
| AutoPhotogrammetry producer integration | PASS_CODE | AutoPhotogrammetry PR #61, Test run #98 |
| vrmine artifact consumer capability | PASS_CODE | vrmine PR #129, 3DGS contracts run #65 |
| Canonical vrmine registry migration | BLOCKED_REMOTE_BYTES | keep current source authority until publish/readback exists |
| Synthetic 100 MiB multi-repository acceptance | PASS_LOCAL_SYNTHETIC | hf-cache-hub PR #21, CI run #19; 104857600 bytes first transfer, 0 bytes second transfer, cache hit true |
| Real Gaussian Splat end-to-end | NOT_OBSERVED | requires real Bucket publication/readback first |

## Pass rule

Issue #17 is complete only after both the real Storage Bucket acceptance and the real Gaussian Splat path have observed matching hashes. Code coverage or a fake downloader alone is insufficient.
