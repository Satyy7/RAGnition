# EVAL-111: Product pages show old prices

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2026-12-20T09:40:00Z

## Reporter note

Prices were updated at 09:00 for the sale but product pages still show yesterday's prices. The checkout total is correct. Catalog shipped a price rounding fix yesterday.

## Alerts

- none

## Logs

```
curl -sI https://www.kestrel.example/api/price/KS-10213 -> 200, age: 2410, x-cache: HIT
2026-12-20T09:30:00Z catalog-api INFO GET /v1/prices/KS-10213 price=39.99 updated_at=2026-12-20T09:00:04Z
```
