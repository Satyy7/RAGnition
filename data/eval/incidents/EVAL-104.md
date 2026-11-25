# EVAL-104: Old checkout page and old search ranking back

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2026-12-07T15:20:00Z

## Reporter note

Since this afternoon some customers see the old checkout page and search ranking looks like last summer. Storefront deployed the winter theme today, but checkout and search did not deploy anything. It seems to affect pods that restarted today.

## Alerts

- FlagFetchErrors: 4 services reporting flag fetch failures

## Logs

```
2026-12-07T13:41:09Z checkout-api WARN FlagClient - fetch http://feature-flags.platform.svc.cluster.local/api/client/features failed: 404 Not Found; using compiled defaults
2026-12-07T13:58:30Z search-api WARN flag-client fetch failed status=404 fallback=defaults
```
