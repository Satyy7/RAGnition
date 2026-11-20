> SYNTHETIC DOCUMENT. Kestrel Commerce is a fictional company created for RAGnition.

# Service: catalog-api

**Team:** Catalog  
**Runtime:** Kotlin (Ktor)  
**SLO:** p95 under 200 ms

Product, variant and price data; source of truth for the search index.

## Dependencies

Calls:
- none

Called by:
- web-storefront
- inventory-api
- search-api

## Datastores

- catalog-db: PostgreSQL 15 (table product_variant ~42M rows)

## External providers

- none

## Configuration notes

- Variant lookups by SKU (`WHERE sku = $1`) are the hottest query (cart and checkout pages).
- Schema migrations are Flyway scripts applied on deploy.

## Known failure modes

- Missing or unusable indexes cause sequential scans on product_variant.
- Long migrations lock tables.
