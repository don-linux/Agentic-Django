# 0003. The API exposes a UUIDv7 public_id, never the integer key

- Status: Accepted
- Date: 2026-11-18

## Context

Integer primary keys leak row counts and growth, and invite enumeration. Random UUIDs as primary keys fragment indexes.

## Decision

Every exposed model has a `public_id` (UUIDv7, `core.models.PublicIdModel`; `User` declares its own). The API, including allauth's user payload, shows it as the string `id`, and detail URLs look it up. The integer `pk` stays the primary key and never leaves the server. See [ARCHITECTURE.md#layers](../ARCHITECTURE.md#layers) and [api.md#representation](../api.md#representation).

## Consequences

- Foreign keys and joins stay on compact integers; UUIDv7 is time-ordered, so its unique index stays compact too.
- An integer in an `{id}` position is a plain 404.

## Alternatives

- UUID primary keys: rejected, larger foreign keys and indexes everywhere.
- Integer ids in the API: rejected, see the context.
