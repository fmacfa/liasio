# Reproducible, bounded load checks

These reports measure specific disposable fixtures, not maximum supported users or a production performance guarantee. The local 0.5 run uses four concurrent users, 64 MiB each, SQLite on an Apple Silicon Mac, loopback networking and antivirus disabled. It uploads and downloads 256 MiB in total in each direction, interrupts/resumes each upload, verifies downloaded SHA-256 values, and checks one-use authorization. See [raw 0.5 results](local-v0.5.json). The report's elapsed time excludes fixture/server preparation; HTTP latency excludes streamed ZIP downloads. Compression of generated text and local networking make throughput unsuitable for internet/VPS sizing.

Reproduce with:

```sh
uv run python scripts/load_test.py --database sqlite --workers 4 --mib 64 --output .local/load-report.json
```

The script creates its own temporary application/data and never targets an existing server. PostgreSQL mode creates and later drops a randomly named test database using the supplied database administrator connection.

Container CI separately runs four users at 256 MiB each, PostgreSQL and live ClamAV. Its exact report is attached to the GitHub Actions run as `load-report-postgres-clamav`. Run this profile on representative VPS hardware before setting production limits. Record CPU/RAM, disk and network characteristics, concurrent scanner/preview/download activity, queue delay and peak memory in a deployment-specific capacity study; the current probe does not measure these peaks. Reports for earlier versions are historical evidence only.

Before 1.0, repeat on multiple VPS sizes and complete a human recovery drill and [independent security review](../INDEPENDENT_AUDIT.md). Hardware guidance in the README remains a starting allowance rather than a capacity certification.
