# EVAL-114: Customers losing their carts

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2026-12-30T15:00:00Z

## Reporter note

Support has many tickets about empty carts today. Checkout released an express checkout button this morning.

## Alerts

- CartRecreateRate: 5.4% of sessions rebuilt their cart

## Logs

```
2026-12-30T14:40:02Z cart-cache INFO stats: evicted_keys=1820331 used_memory=4.00G maxmemory_policy=allkeys-lru
2026-12-30T14:40:05Z checkout-api WARN CartStore - cart not found for active session s-77a1
```
