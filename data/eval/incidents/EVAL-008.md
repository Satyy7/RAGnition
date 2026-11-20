# EVAL-008: Cart and product pages slow

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2026-11-05T16:40:00Z

## Reporter note

Product and cart pages got slow this afternoon; catalog-api p95 is around 4 s.

## Alerts

- StorefrontLatencyHigh: p95 4.6s
- CatalogLatencyHigh: catalog-api p95 4.1s

## Logs

```
2026-11-05T16:31:10Z catalog-db LOG duration: 4212.551 ms statement: SELECT * FROM product_variant WHERE sku = $1
EXPLAIN SELECT * FROM product_variant WHERE sku = 'KS-55102-GRY': Seq Scan on product_variant (rows=42110388)
```
