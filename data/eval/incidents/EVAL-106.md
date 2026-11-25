# EVAL-106: Stock reservations lagging

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2026-12-11T14:00:00Z

## Reporter note

Reservation lag keeps growing since this morning, now 50 minutes. The consumer pods are up and busy. inventory-api deployed a readiness endpoint yesterday.

## Alerts

- ReservationLagHigh: inventory-reserver lag 50m
- ConsumerRebalanceRate: inventory-reserver 14 rebalances/h

## Logs

```
2026-12-11T13:41:20Z inventory-api ERROR CommitFailedError: Commit cannot be completed since the group has already rebalanced. The time between subsequent calls to poll() was longer than the configured max.poll.interval.ms
2026-12-11T13:41:21Z inventory-api INFO poll returned records=2000 batch_ms=402113
```
