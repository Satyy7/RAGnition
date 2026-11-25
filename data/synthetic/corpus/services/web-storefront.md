> SYNTHETIC DOCUMENT. Kestrel Commerce is a fictional company created for RAGnition.

# Service: web-storefront

**Team:** Storefront  
**Runtime:** TypeScript (Next.js, server-side rendering)  
**SLO:** 99.9% of page renders under 1.5 s (p95)

Customer-facing website. Renders product, search, cart and checkout pages.

## Dependencies

Calls:
- catalog-api
- search-api
- checkout-api
- auth-service
- feature-flags

Called by:
- none

## Datastores

- none

## External providers

- CDN (edge cache for static assets and images)

## Configuration notes

- SSR upstream timeout is 3 s per backend call.
- Static images are served from the CDN; origin is an object storage bucket.
- Prices on product pages are fetched client-side from `/api/price/{sku}` (served by catalog-api through the storefront).
- Sign-in, sign-out and the session cookie are handled by auth-service; card payments redirect customers to their bank for 3-D Secure and back to /checkout/return.

## Known failure modes

- Slow upstream APIs make server-side rendering time out and return 504.
- CDN cache rules misconfigured so prices or assets are stale.
