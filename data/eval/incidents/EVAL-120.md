# EVAL-120: Stock check failing with database errors

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2027-01-13T18:30:00Z

## Reporter note

January sale evening. Checkout fails at the stock check for about 12% of customers. inventory-api scaled out to many pods.

## Alerts

- CheckoutErrorRateHigh: 12%
- InventoryErrorRateHigh: inventory-api 5xx 13%

## Logs

```
2027-01-13T18:12:03Z inventory-api ERROR asyncpg.exceptions.TooManyConnectionsError: sorry, too many clients already
kubectl get hpa inventory-api: REPLICAS 21, MINPODS 3, MAXPODS 24
```
