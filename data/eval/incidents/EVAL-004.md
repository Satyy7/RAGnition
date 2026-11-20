# EVAL-004: Product changes not appearing in search

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2026-11-15T08:20:00Z

## Reporter note

Merchandisers say price and stock updates since last night are not visible in search. Search queries themselves work.

## Alerts

- SearchIndexLag: catalog changes not searchable for 9h
- OpenSearchDiskHigh: search-prod data nodes at 95.4%, 95.8%, 94.9%

## Logs

```
2026-11-15T08:02:13Z search-indexer ERROR bulk failed: cluster_block_exception index [products-v12] blocked by: [TOO_MANY_REQUESTS/12/disk usage exceeded flood-stage watermark, index has read-only-allow-delete block]
2026-11-15T08:05:40Z opensearch WARN flood stage disk watermark [95%] exceeded on [search-prod-data-1]
```
