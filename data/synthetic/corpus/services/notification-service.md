> SYNTHETIC DOCUMENT. Kestrel Commerce is a fictional company created for RAGnition.

# Service: notification-service

**Team:** Customer Comms  
**Runtime:** Node.js 22  
**SLO:** confirmation email sent within 5 minutes of order

Sends order confirmation and shipping emails through the PostRelay email API.

## Dependencies

Calls:
- none

Called by:
- order-worker

## Datastores

- none

## External providers

- PostRelay email API: account limit 100 messages/s; returns HTTP 429 with Retry-After when exceeded

## Configuration notes

- Marketing campaigns share the same PostRelay account and limit.
- Retry policy must honour Retry-After and use exponential backoff with jitter (PM-2026-004).
- PostRelay rejects mail from a sender domain without published SPF and DKIM records (permanent error 550 5.7.26).

## Known failure modes

- Provider throttling (429) and retry amplification build a send backlog.
