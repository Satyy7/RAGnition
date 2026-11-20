> SYNTHETIC DOCUMENT. Kestrel Commerce is a fictional company created for RAGnition.

# Service: order-worker

**Team:** Fulfilment  
**Runtime:** Python 3.12  
**SLO:** orders handed to warehouse within 10 minutes

Consumes the orders topic, builds fulfilment documents and pushes them to the warehouse system.

## Dependencies

Calls:
- inventory-api
- notification-service

Called by:
- none

## Datastores

- Kafka topic orders

## External providers

- none

## Configuration notes

- Kubernetes resources in Helm values `order-worker.resources` (requests and limits).
- Peak resident memory measured at about 700 MB during sale traffic (PM-2026-005).

## Known failure modes

- OOMKilled (exit code 137) when the memory limit is below peak usage, leading to CrashLoopBackOff.
