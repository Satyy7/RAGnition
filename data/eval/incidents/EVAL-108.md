# EVAL-108: Lookup timeouts across services on sale evening

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2026-12-15T18:40:00Z

## Reporter note

Sale evening. Small error spikes in search, order-worker and payments at the same time. Search and order-worker both deployed today. CoreDNS runs its usual 6 replicas but they look busy.

## Alerts

- SearchErrorRateHigh: 1.6%
- OrderHandoffDelay: oldest order waiting 22m
- PaymentsErrorRateHigh: 1.2%

## Logs

```
2026-12-15T18:22:40Z payments-api level=error err="dial tcp: lookup api.paygate.example: i/o timeout"
2026-12-15T18:22:41Z order-worker ERROR httpx.ConnectError: [Errno -3] Temporary failure in name resolution (shipping-api.fulfilment.svc.cluster.local)
coredns metrics: cache hit ratio 0.34 (last week 0.93); forward requests/s 5.8x last week
```
