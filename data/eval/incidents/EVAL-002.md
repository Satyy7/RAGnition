# EVAL-002: Checkout failing with Redis errors

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2026-11-12T14:30:00Z

## Reporter note

Checkout error rate climbing since this morning, adding to cart fails for some users. Redis memory graph has been rising steadily for two days.

## Alerts

- CheckoutErrorRateHigh: checkout-api 5xx ratio 7.2%
- RedisMemoryHigh: cart-cache used_memory 100% of maxmemory

## Logs

```
2026-11-12T14:21:05Z checkout-api ERROR CartStore - save failed: OOM command not allowed when used memory > 'maxmemory'.
2026-11-12T14:21:09Z cart-cache INFO keyspace: db0 keys=9184412 expires=1120 avg_ttl=511000000
2026-11-12T14:22:30Z cart-cache INFO stats: evicted_keys=0
```
