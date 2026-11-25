# EVAL-107: Checkout slow but no errors

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2026-12-14T13:00:00Z

## Reporter note

Checkout p95 is around 2 s since yesterday, no errors. Checkout released gift wrap this morning. The slow part seems to be the stock check, mostly for baskets with bundles.

## Alerts

- CheckoutLatencyHigh: checkout-api p95 2.1s

## Logs

```
2026-12-14T12:41:08Z checkout-api INFO AvailabilityClient GET /v2/availability took 1730ms items=4 bundles=1
2026-12-14T12:41:08Z inventory-api INFO GET /v2/availability 200 total_ms=1702 bundle_resolve_ms=1655
```
