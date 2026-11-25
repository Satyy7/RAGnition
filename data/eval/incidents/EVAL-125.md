# EVAL-125: Checkout rejecting payments under load

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2027-01-23T19:00:00Z

## Reporter note

Checkout returns 503 for about a quarter of orders at the payment step. Paygate's status page says "degraded performance". Checkout added promo code validation last week.

## Alerts

- CheckoutErrorRateHigh: 24%

## Logs

```
2027-01-23T18:44:01Z checkout-api ERROR PaymentExecutor - rejected: pool exhausted (active=50, queue=0)
2027-01-23T18:44:02Z payments-api level=info msg="paygate authorize" latency_ms=13870
```
