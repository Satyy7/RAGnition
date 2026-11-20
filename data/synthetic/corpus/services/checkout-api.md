> SYNTHETIC DOCUMENT. Kestrel Commerce is a fictional company created for RAGnition.

# Service: checkout-api

**Team:** Checkout  
**Runtime:** Java 21 (Spring Boot)  
**SLO:** 99.95% successful checkout requests; p95 latency under 800 ms

Cart and checkout orchestration. Holds carts in Redis, reserves stock, takes payment, emits orders.

## Dependencies

Calls:
- inventory-api
- payments-api

Called by:
- web-storefront

## Datastores

- cart-cache: Redis 7, maxmemory 4 GB, maxmemory-policy volatile-lru

## External providers

- none

## Configuration notes

- Carts are Redis keys `cart:{session}`; the platform rule is that every key carries a TTL (carts 7 days).
- Calls inventory-api `GET /v2/availability` before reserving stock and maps the JSON response to a strict DTO (unknown or missing required fields fail deserialization).

## Known failure modes

- Redis memory pressure; behaviour depends on the eviction policy and on keys having TTLs.
- Downstream schema changes break response parsing.
- Payment timeouts cause abandoned checkouts.
