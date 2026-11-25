> SYNTHETIC DOCUMENT. Kestrel Commerce is a fictional company created for RAGnition.

# Service: search-api

**Team:** Search  
**Runtime:** Java 21  
**SLO:** search p95 under 300 ms; catalog changes searchable within 5 minutes

Product search and the search-indexer job that writes catalog updates into OpenSearch.

## Dependencies

Calls:
- catalog-api
- feature-flags

Called by:
- web-storefront

## Datastores

- OpenSearch cluster search-prod: 3 data nodes, 500 GB disk each; indices products-*, search-querylog-*

## External providers

- none

## Configuration notes

- OpenSearch disk watermarks are defaults (low 85%, high 90%, flood_stage 95%).
- At flood_stage OpenSearch puts index.blocks.read_only_allow_delete on indices with a shard on that node.
- The query log indices search-querylog-* are managed by an index lifecycle policy (retention setting).
- The search-indexer reads catalog change events from the Kafka topic `catalog.product-updated`.
- Text queries match the title field with fuzziness AUTO and other fields exactly; search-prod is sized for this at about 35% CPU at peak.

## Known failure modes

- Disk watermarks reached, so shards stay unassigned (high) or indices are write-blocked (flood stage).
- Indexer backlog makes search results stale.
