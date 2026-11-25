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
- auth-service
- shipping-api
- feature-flags

Called by:
- web-storefront

## Datastores

- cart-cache: Redis 7, maxmemory 4 GB, maxmemory-policy volatile-lru

## External providers

- none

## Configuration notes

- Carts are Redis keys `cart:{session}`; the platform rule is that every key carries a TTL (carts 7 days).
- Calls inventory-api `GET /v2/availability` before reserving stock and maps the JSON response to a strict DTO (unknown or missing required fields fail deserialization).
- Customer requests carry a JWT issued by auth-service; checkout-api verifies it against the auth-service JWKS, which it caches for 24 h.
- Delivery options and prices come from shipping-api `GET /v1/quotes` (timeout 3 s).
- Payment calls to payments-api run on a bounded pool of 50 worker threads with a 20 s timeout.

## Known failure modes

- Redis memory pressure; behaviour depends on the eviction policy and on keys having TTLs.
- Downstream schema changes break response parsing.
- Payment timeouts cause abandoned checkouts.
