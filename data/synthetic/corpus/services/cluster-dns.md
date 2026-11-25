> SYNTHETIC DOCUMENT. Kestrel Commerce is a fictional company created for RAGnition.

# Service: cluster-dns

**Team:** Platform  
**Runtime:** CoreDNS 1.11 (Kubernetes add-on)  
**SLO:** lookup p99 under 50 ms

In-cluster DNS for service discovery in prod-eu1.

## Dependencies

Calls:
- none

Called by:
- every service (shared infrastructure)

## Datastores

- none

## External providers

- none

## Configuration notes

- Pods use the default ndots:5, so one external or partially-qualified lookup can become up to 5 DNS queries.
- Replica count is managed by the cluster-proportional-autoscaler (min 6).
- The CoreDNS cache plugin keeps answers for 30 s; most service lookups are answered from cache.

## Known failure modes

- CoreDNS CPU saturation causes lookup timeouts in every service at once.
