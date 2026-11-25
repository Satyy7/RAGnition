# EVAL-128: Kafka under-replicated partitions after broker replacement

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2027-01-27T07:30:00Z

## Reporter note

After broker 2 was replaced last night, 37 partitions are still under-replicated.

## Alerts

- KafkaUnderReplicatedPartitions: 37

## Logs

```
2027-01-27T07:10:00Z kafka-2 INFO [ReplicaFetcher replicaId=2] fetching 37 partitions, throttled=true
```
