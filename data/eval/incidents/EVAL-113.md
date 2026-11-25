# EVAL-113: Payments failing at holiday peak

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2026-12-28T19:20:00Z

## Reporter note

About 9% of payments fail with 503 since 19:00. The PgBouncer pool was put back to 60 earlier this month. payments-api deployed 3-D Secure text changes yesterday.

## Alerts

- PaymentsErrorRateHigh: payments-api 5xx ratio 9.1%
- PgBouncerClientsWaiting: pool payments-db/payments cl_waiting=84 for 5m

## Logs

```
2026-12-28T19:06:14Z payments-api level=error msg="authorize failed" err="timeout acquiring connection from pool after 5s"
2026-12-28T19:07:00Z pgbouncer LOG stats: payments-db/payments cl_active=60 cl_waiting=84 sv_active=60 sv_idle=0
```
