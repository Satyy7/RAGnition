# EVAL-007: Random timeouts in many services at evening peak

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2026-11-14T18:25:00Z

## Reporter note

Since 18:00 several teams see small error spikes at the same time - checkout, payments, search. No common deploy. It is a sale evening.

## Alerts

- CheckoutErrorRateHigh: 2.1%
- PaymentsErrorRateHigh: 1.8%
- SearchLatencyHigh: p95 1.9s

## Logs

```
2026-11-14T18:07:02Z checkout-api WARN I/O error on GET http://inventory-api.fulfilment.svc.cluster.local/v2/availability: inventory-api.fulfilment.svc.cluster.local: Temporary failure in name resolution
2026-11-14T18:07:05Z payments-api level=error err="dial tcp: lookup payments-pgbouncer.payments.svc.cluster.local: i/o timeout"
kubectl top pods -n kube-system: coredns-6f9b8-2xk4p 1000m, coredns-6f9b8-9qj2c 1000m
```
