# EVAL-012: One Kafka broker with high disk latency

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2026-11-17T02:15:00Z

## Reporter note

Kafka broker 3 shows disk write latency above 200 ms since 01:30; producers see occasional slow acks. Other brokers are normal.

## Alerts

- KafkaBrokerDiskLatencyHigh: kafka-3 p99 write latency 236ms

## Logs

```
2026-11-17T02:10:00Z kafka-3 WARN [ReplicaManager broker=3] Slow log append took 412 ms
```
