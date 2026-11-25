# EVAL-121: Customers signed out after card authentication

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2027-01-15T20:00:00Z

## Reporter note

Customers paying by card are sent to the sign-in page when they come back from their bank's verification step, and the order is lost. Storefront changed footer links today.

## Alerts

- CheckoutConversionDrop: card payments completion -37%

## Logs

```
2027-01-15T19:41:20Z web-storefront INFO GET /checkout/return 302 -> /signin reason=no_session_cookie referer=https://acs.examplebank.example/
```
