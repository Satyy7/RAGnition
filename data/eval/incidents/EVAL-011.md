# EVAL-011: Product images slow in Singapore

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2026-11-16T03:40:00Z

## Reporter note

Users in Singapore report product images taking 6-8 seconds to load. EU and US are fine.

## Alerts

- RUM: image load p95 7.2s (country=SG)

## Logs

```
RUM sample: GET https://cdn.kestrel.example/img/p/88213-1.webp 7104ms country=SG pop=unknown
```
