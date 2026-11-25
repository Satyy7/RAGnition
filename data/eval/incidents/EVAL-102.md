# EVAL-102: Orders waiting for shipping labels

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2026-12-04T10:30:00Z

## Reporter note

Warehouse says many orders have no label since early morning. Delivery quotes in checkout are also slow. No sale running today.

## Alerts

- LabelFailureRate: 38% of label requests failing
- OrderHandoffDelay: oldest order waiting 74m

## Logs

```
2026-12-04T09:50:03Z order-worker ERROR label request failed: POST http://shipping-api.fulfilment.svc.cluster.local/v1/labels -> 503 {"error":"upstream","upstream_status":429}
2026-12-04T09:50:10Z shipping-api WARN carrierhub POST /labels status=429 rate_limited=true current_rps=71
```
