# EVAL-003: Stock reservations lagging, consumer keeps rebalancing

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2026-11-07T10:45:00Z

## Reporter note

Reservation lag is at 1.4 million messages and growing. The inventory-reserver group rebalances every few minutes. inventory-db looks healthy.

## Alerts

- ReservationLagHigh: inventory-reserver lag 1,412,880
- ConsumerRebalanceRateHigh: inventory-reserver 14 rebalances in 1h

## Logs

```
2026-11-07T10:31:44Z inventory-api WARNING consumer poll timeout has expired. This means the time between subsequent calls to poll() was longer than the configured max_poll_interval_ms
2026-11-07T10:31:45Z inventory-api ERROR CommitFailedError: Commit cannot be completed since the group has already rebalanced
2026-11-07T10:32:02Z inventory-api INFO enrichment GET catalog-api /v1/products/88213/dimensions took 812ms
```
