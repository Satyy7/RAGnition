> SYNTHETIC DOCUMENT. Kestrel Commerce is a fictional company created for RAGnition.

# Service: payments-api

**Team:** Payments  
**Runtime:** Go 1.23  
**SLO:** 99.95% successful authorizations; p99 under 2 s

Authorizes and captures card payments through the Paygate payment service provider.

## Dependencies

Calls:
- none

Called by:
- checkout-api

## Datastores

- payments-db: PostgreSQL 15 via PgBouncer (transaction pooling)

## External providers

- Paygate PSP over mutual TLS (client certificate + Paygate server chain verified against a pinned trust bundle)

## Configuration notes

- 6 replicas, each with a database pool of max 20 connections (120 client connections at full load); steady-state peak uses about 40 server connections.
- PgBouncer `default_pool_size` is the number of real server connections per database/user pair.
- The Paygate trust bundle is a ConfigMap `paygate-ca-bundle` updated by hand.
- The Paygate client certificate is issued and renewed by cert-manager (since PM-2025-015).
- Paygate only accepts client certificates issued by 'Kestrel Partner CA', the CA registered with Paygate during onboarding.
- Paygate HTTP client - request timeout 2 s; keep-alive connections are reused and TLS sessions are resumed, so most requests skip the full mutual-TLS handshake (about 300 ms).

## Known failure modes

- Connection waits at PgBouncer when client demand exceeds the server pool.
- TLS failures towards Paygate (expired client certificate, untrusted server chain).
- Paygate latency spikes.
