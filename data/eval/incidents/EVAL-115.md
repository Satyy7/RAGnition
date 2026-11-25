# EVAL-115: Product pages returning 504

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2027-01-03T10:25:00Z

## Reporter note

Product and cart pages time out since about 10:00. Storefront deployed new search suggestions this morning.

## Alerts

- StorefrontErrorRateHigh: 504 ratio 22%
- CatalogLatencyHigh: catalog-api p95 9.8s

## Logs

```
2027-01-03T10:08:51Z catalog-api ERROR SQL error: canceling statement due to lock timeout (SELECT ... FROM product_variant WHERE sku = $1)
catalog-db pg_locks: 1 granted RowExclusiveLock on product_variant held 00:24:10 by pid 41212 (flyway); 380 waiting
```
