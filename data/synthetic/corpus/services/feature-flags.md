> SYNTHETIC DOCUMENT. Kestrel Commerce is a fictional company created for RAGnition.

# Service: feature-flags

**Team:** Platform  
**Runtime:** Go 1.23  
**SLO:** flag fetch p99 under 100 ms

In-house feature flag service. Services read flags through the flag client SDK.

## Dependencies

Calls:
- none

Called by:
- web-storefront
- checkout-api
- search-api
- order-worker

## Datastores

- flags-db: PostgreSQL 15 (small)

## External providers

- none

## Configuration notes

- SDK clients poll `/api/client/features` every 30 s and keep the last good values in memory.
- If a client cannot fetch flags at startup it falls back to the defaults compiled into each service; several defaults are outdated.
- Sized for about 400 polling clients at a 30 s interval (2 replicas).
- Clients in web-storefront, checkout-api, search-api and order-worker.

## Known failure modes

- Flag service unreachable, so services start with compiled defaults and features change unexpectedly.
- Polling load overloads the service and flag fetches time out.
