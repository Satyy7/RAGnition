# EVAL-006: All Paygate payments failing with certificate error

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2026-11-14T09:06:00Z

## Reporter note

Every payment fails since 09:00 UTC. Our client certificate is valid until March.

## Alerts

- PaymentsErrorRateHigh: payments-api 5xx ratio 99.6%

## Logs

```
2026-11-14T09:00:41Z payments-api level=error msg="paygate authorize" err="Post https://api.paygate.example/v3/authorize: tls: failed to verify certificate: x509: certificate signed by unknown authority"
2026-11-14T09:00:41Z payments-api level=debug msg="peer chain" leaf="api.paygate.example" issuer="Paygate Issuing CA G3"
```
