# EVAL-103: New products missing from search

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2026-12-06T09:30:00Z

## Reporter note

Merchandising added 200 products on Friday and none of them can be found in search. Price edits from Friday are not in search either. Search itself is fast and healthy. Search changed its synonyms yesterday.

## Alerts

- SearchFreshness: newest indexed product update is 41h old

## Logs

```
2026-12-06T09:12:00Z search-indexer INFO consumer group search-indexer topic=catalog.product-updated lag=0 records_last_hour=0
2026-12-06T09:12:00Z search-indexer INFO last record received at 2026-12-04T14:02:51Z
```
