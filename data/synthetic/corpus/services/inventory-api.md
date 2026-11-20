> SYNTHETIC DOCUMENT. Kestrel Commerce is a fictional company created for RAGnition.

# Service: inventory-api

**Team:** Fulfilment  
**Runtime:** Python 3.12 (FastAPI)  
**SLO:** availability p95 under 150 ms; reservation lag under 60 s

Stock levels and reservations. Serves availability queries and runs the reservation consumer.

## Dependencies

Calls:
- catalog-api

Called by:
- checkout-api
- order-worker

## Datastores

- inventory-db: PostgreSQL 15
- Kafka topic inventory-reservations (consumer group inventory-reserver, 12 partitions)

## External providers

- none

## Configuration notes

- Reservation consumer settings max.poll.records=500, max.poll.interval.ms=300000 (5 minutes).
- If one poll batch takes longer than max.poll.interval.ms, the consumer is removed from the group and partitions are rebalanced.
- Public API `/v2/availability` is consumed by checkout-api.

## Known failure modes

- Slow per-message processing causes rebalance loops and growing consumer lag.
- Breaking API changes affect checkout-api.
