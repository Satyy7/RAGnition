# EVAL-119: All payments rejected by Paygate

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2027-01-11T08:00:00Z

## Reporter note

Every payment has failed since about 22:05 last night. The Paygate CA bundle was updated for G3 in November and the client certificate is valid for another 80 days.

## Alerts

- PaymentsErrorRateHigh: payments-api 5xx ratio 100%

## Logs

```
2027-01-10T22:05:31Z payments-api level=error msg="paygate authorize" err="remote error: tls: unknown certificate authority"
openssl x509 -in paygate-client.crt -noout -issuer -enddate: issuer=CN=Kestrel Internal CA v2, notAfter=Apr 10 22:00:00 2027 GMT
```
