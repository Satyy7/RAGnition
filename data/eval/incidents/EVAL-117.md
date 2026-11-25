# EVAL-117: Search slow, OpenSearch CPU high

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2027-01-07T12:00:00Z

## Reporter note

Search p95 is 1.6 s since yesterday and search-prod data nodes sit at 90% CPU. Traffic is normal. Catalog shipped dependency updates yesterday.

## Alerts

- SearchLatencyHigh: p95 1.62s
- OpenSearchCPUHigh: search-prod data nodes 91%

## Logs

```
search-prod slowlog: took[1.4s] query={"multi_match":{"query":"cusion cover","fields":["title","description","brand","attributes.*"],"fuzziness":"AUTO"}}
```
