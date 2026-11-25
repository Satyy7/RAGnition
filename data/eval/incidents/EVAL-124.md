# EVAL-124: Product API slow

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2027-01-21T14:00:00Z

## Reporter note

catalog-api p95 is 2.8 s since yesterday's release; product pages are slow.

## Alerts

- CatalogLatencyHigh: catalog-api p95 2.8s

## Logs

```
pg_stat_statements: SELECT * FROM variant_attribute WHERE variant_id = $1 calls=48210331 mean_ms=1.9
EXPLAIN SELECT * FROM variant_attribute WHERE variant_id = 9921: Seq Scan on variant_attribute (rows=3120440)
```
