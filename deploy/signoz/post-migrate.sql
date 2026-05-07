-- Post-migration schema fix for SigNoz v0.144 collector + v0.76 query-service.
--
-- Background: at this version pairing the schema-migrator (v0.144.4) creates
-- four span-attributes tables with `tagType Enum8('tag' = 1, 'resource' = 2)`,
-- but the collector binary writes a third enum value `'scope'` for
-- scope-level OTel attributes. ClickHouse rejects writes with
--     unknown element "scope"
-- until the enum is widened. This script extends the enum on every affected
-- table to (`'tag' = 1, 'resource' = 2, 'scope' = 3`), unblocking ingest.
--
-- Run once, after `docker compose --profile observability up` settles, via:
--   docker exec neighbourhood-library-signoz-clickhouse-1 \
--       clickhouse-client --multiquery \
--       --queries-file=/dev/stdin < deploy/signoz/post-migrate.sql
--
-- Idempotent: re-running on an already-extended schema is a no-op.

ALTER TABLE signoz_traces.span_attributes_keys ON CLUSTER cluster
    MODIFY COLUMN tagType Enum8('tag' = 1, 'resource' = 2, 'scope' = 3);

ALTER TABLE signoz_traces.span_attributes ON CLUSTER cluster
    MODIFY COLUMN tagType Enum8('tag' = 1, 'resource' = 2, 'scope' = 3);

ALTER TABLE signoz_traces.distributed_span_attributes_keys ON CLUSTER cluster
    MODIFY COLUMN tagType Enum8('tag' = 1, 'resource' = 2, 'scope' = 3);

ALTER TABLE signoz_traces.distributed_span_attributes ON CLUSTER cluster
    MODIFY COLUMN tagType Enum8('tag' = 1, 'resource' = 2, 'scope' = 3);
