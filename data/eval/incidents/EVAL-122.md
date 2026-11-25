# EVAL-122: Checkout delivery options timing out

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2027-01-17T13:00:00Z

## Reporter note

The delivery step in checkout often shows "no delivery options". Checkout released promo code validation yesterday.

## Alerts

- CheckoutLatencyHigh: checkout-api p95 3.4s

## Logs

```
2027-01-17T12:31:09Z checkout-api WARN ShippingClient GET /v1/quotes timed out after 3000ms
2027-01-17T12:31:10Z shipping-api INFO quote carrierhub_ms=2710 cache=disabled
2027-01-17T12:31:11Z shipping-api WARN carrierhub status=429 rate_limited=true
```
