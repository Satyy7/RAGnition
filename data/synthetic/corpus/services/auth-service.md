> SYNTHETIC DOCUMENT. Kestrel Commerce is a fictional company created for RAGnition.

# Service: auth-service

**Team:** Identity  
**Runtime:** Go 1.23  
**SLO:** 99.95% successful sign-ins; token verification keys always available

Customer sign-in, sessions and token issuing for the storefront and checkout.

## Dependencies

Calls:
- none

Called by:
- web-storefront
- checkout-api

## Datastores

- session-store: Redis 7, maxmemory 2 GB, maxmemory-policy noeviction

## External providers

- none

## Configuration notes

- Issues JWTs signed with a rotating signing key; public keys are published at `/.well-known/jwks.json`.
- Consumers (checkout-api, web-storefront) cache the JWKS for 24 h, so a retired key must stay published for at least 24 h after rotation.
- Sessions are Redis keys `sess:{id}` with a 1-day TTL; about 40 MB of new session data is written per day.
- The session cookie is SameSite=Lax so that it is sent when customers return from their bank's 3-D Secure page.

## Known failure modes

- Signing key rotated without overlap, so tokens fail verification with 401 in consumers.
- session-store full; with noeviction Redis rejects writes and sign-ins fail.
