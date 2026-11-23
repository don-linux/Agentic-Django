# 0020. OPTIONS, Allow and 406 follow Django and RFC 9110

- Status: Accepted
- Date: 2026-11-23

## Context

Ninja has no answer of its own for HEAD, for OPTIONS, for a 405 in the API's error body, or for a 406: it ignores `Accept`. `core.api.negotiation.ApiContractMiddleware` answers them for every route under `/api/v1/`. So the template has to define three things: what OPTIONS returns, where `Allow` goes, and in which order the 405, OPTIONS and the 406 are checked.

## Decision

- **OPTIONS** answers as Django's `View.options()` does: 200 with `Allow` and `Content-Length: 0`, no body and no authentication. It only lists the methods (RFC 9110, section 9.3.7).
- **`Allow`** goes on the 405 and on OPTIONS only, as in Django and Ninja; RFC 9110, section 10.2.1, requires it on a 405. The order of the methods is Django's `View.http_method_names`.
- **Order of checks.** A path that is not a route is 404. Then a method the route lacks is 405, and OPTIONS answers without negotiating. Only then is an `Accept` that takes none of the operation's media types a 406, still before authentication.
- **`Accept`** is read whole, by RFC 9110's rules: `q=0` refuses, `*/json` is no media range, and the most specific range decides. The server bounds the header's size: gunicorn joins repeated lines, and allows `limit_request_fields` lines of `limit_request_field_size` bytes. An operation produces the media types its 2xx responses declare in the OpenAPI document: JSON for every operation the template ships, HTML for the docs page, the file's type for a download that declares one ([files.md](../files.md#endpoint)). The response has that type, so there is nothing to choose between. An operation that declares no content on success, such as a 204, is not negotiated.

See [api.md#head-options-and-405](../api.md#head-options-and-405) and [api.md#representation](../api.md#representation).

## Consequences

- OPTIONS has an empty body: a client reads the methods from `Allow`.
- A wrong method with an unacceptable `Accept` is a 405, not a 406.
- The negotiation follows the RFC rules only: one media type per response, no choice between renderers.

## Alternatives

- **An OPTIONS body with metadata** (name, description, accepted and rendered types). Rejected: no standard defines it, and Django's `View.options()` answers with `Allow` only.
- **`Allow` on every response.** Rejected: RFC 9110 requires it on a 405, and Django and Ninja send it there.
- **Drop the 406, as Ninja does by ignoring `Accept`.** Rejected: the contract declares it, and honoring `Accept` follows RFC 9110.
- **OPTIONS as a 405**, since Ninja has no answer for it. Rejected: Django answers OPTIONS on every view.
