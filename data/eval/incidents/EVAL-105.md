# EVAL-105: Payment latency high while Paygate is healthy

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2026-12-10T20:05:00Z

## Reporter note

payments-api p99 went from about 900 ms to 2.4 s since yesterday and some authorizations time out. Paygate's status page is green and their own latency graph is flat. payments-api CPU is three times higher than last week.

## Alerts

- PaymentsLatencyHigh: payments-api p99 2.41s

## Logs

```
2026-12-10T19:58:02Z payments-api level=info msg="paygate authorize" latency_ms=1870 new_conn=true tls_full_handshake=true handshake_ms=318
2026-12-10T19:58:02Z payments-api level=info msg="paygate pool" idle=0 opened_last_min=1144
```
