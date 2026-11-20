# EVAL-001: Payment authorizations timing out

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2026-11-04T19:12:00Z

## Reporter note

Since about 19:00 roughly 15% of payments fail with 503. Traffic is normal for a weekday evening, nothing deployed to payments-api this week.

## Alerts

- PaymentsErrorRateHigh: payments-api 5xx ratio 14.8% (threshold 1%)
- PgBouncerClientsWaiting: pool payments-db/payments cl_waiting=58 for 6m

## Logs

```
2026-11-04T19:03:11Z payments-api level=error msg="authorize failed" err="timeout acquiring connection from pool after 5s"
2026-11-04T19:03:12Z payments-api level=error msg="authorize failed" err="timeout acquiring connection from pool after 5s"
2026-11-04T19:04:40Z pgbouncer LOG stats: payments-db/payments cl_active=25 cl_waiting=61 sv_active=25 sv_idle=0
```
