# EVAL-126: Reservations and availability slowing down

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2027-01-25T16:00:00Z

## Reporter note

Reservation lag has been creeping up for days and availability calls got slower each week. inventory-api changed its log format yesterday and the consumer membership today.

## Alerts

- ReservationLagHigh: inventory-reserver lag 22m
- InventoryLatencyHigh: /v2/availability p95 640ms

## Logs

```
inventory-db: SELECT relname, n_live_tup, n_dead_tup, last_autovacuum FROM pg_stat_user_tables -> reservations 2,104,331 | 412,880,120 | 2026-12-11 23:10
inventory-db: reservations total size 61 GB
```
