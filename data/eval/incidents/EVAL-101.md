# EVAL-101: Signed-in customers rejected at checkout

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2026-12-02T08:10:00Z

## Reporter note

Since about 06:00 signed-in customers get "please sign in again" when they place an order. Guest checkout works. Checkout shipped v5.9.1 this morning.

## Alerts

- CheckoutAuthFailures: checkout-api 401 ratio 28%

## Logs

```
2026-12-02T06:04:12Z checkout-api WARN JwtVerifier - token rejected: kid "k-20261202" not found in JWKS
2026-12-02T06:04:12Z checkout-api DEBUG JwksCache - keys=[k-20261201] fetched_at=2026-12-01T09:12:40Z max_age=86400
```
