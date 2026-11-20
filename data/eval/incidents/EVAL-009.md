# EVAL-009: Order confirmation emails delayed

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2026-11-18T16:10:00Z

## Reporter note

Customers are not getting order confirmations; the queue is 2 hours behind. Marketing started the winter campaign at 15:00.

## Alerts

- EmailQueueBacklog: 184,000 messages, oldest 118m

## Logs

```
2026-11-18T15:22:04Z notification-service warn http-client retry 1/5 POST https://api.postrelay.example/v1/send status=429
2026-11-18T15:22:04Z notification-service warn http-client retry 2/5 POST https://api.postrelay.example/v1/send status=429
2026-11-18T15:22:04Z notification-service warn http-client retry 3/5 POST https://api.postrelay.example/v1/send status=429 retry-after=30
```
