# EVAL-010: Checkout failing at stock check

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2026-11-10T13:15:00Z

## Reporter note

About a third of checkouts fail when we check stock. Nothing deployed to checkout today.

## Alerts

- CheckoutErrorRateHigh: checkout-api 5xx ratio 31%

## Logs

```
2026-11-10T13:02:51Z checkout-api ERROR AvailabilityClient - failed to read response: com.fasterxml.jackson.databind.exc.MismatchedInputException: Missing required creator property 'available_qty' (index 1)
2026-11-10T13:02:51Z checkout-api INFO inventory-api responded 200 in 41ms
```
