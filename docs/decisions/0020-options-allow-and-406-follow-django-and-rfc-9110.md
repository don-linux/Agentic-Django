# 0020. OPTIONS, Allow and 406 follow Django and RFC 9110

- Status: Accepted
- Date: 2026-11-23

## Context

`core.api.negotiation.ApiContractMiddleware` answers HEAD, OPTIONS, 405 and 406 for every route under `/api/v1/`, because Ninja has no answer of its own for them. The DRF migration made it reproduce DRF:

- **OPTIONS.** It answered SimpleMetadata (`{name, description, renders, parses}`) and was a 401 without credentials.
- **`Allow`.** It went on every response of a route, as `finalize_response` set it.
- **406.** It came before the 405 and OPTIONS, as content negotiation ran first in `APIView.initial`.
- **`Accept`.** The header was cut at DRF 3.18.3's limits: 64 members, 256 characters per member and 8192 in all.

DRF parity stopped being a requirement on 2026-11-22.

## Decision

- **OPTIONS** answers as Django's `View.options()` does: 200 with `Allow` and `Content-Length: 0`, no body and no authentication. It only lists the methods (RFC 9110, section 9.3.7).
- **`Allow`** goes on the 405 and on OPTIONS only, as in Django and Ninja; RFC 9110, section 10.2.1, requires it on a 405. The order of the methods is Django's `View.http_method_names`.
- **Order of checks.** A path that is not a route is 404. Then a method the route lacks is 405, and OPTIONS answers without negotiating. Only then is an `Accept` that takes none of the operation's media types a 406, still before authentication.
- **`Accept`** is read whole, by RFC 9110's rules: `q=0` refuses, `*/json` is no media range, and the most specific range decides. The server bounds the header's size: gunicorn joins repeated lines, and allows `limit_request_fields` lines of `limit_request_field_size` bytes. An operation produces the media types its 2xx responses declare in the OpenAPI document: JSON for every operation the template ships, HTML for the docs page, the file's type for a download that declares one ([files.md](../files.md#endpoint)). The response has that type, so there is nothing to choose between. An operation that declares no content on success, such as a 204, is not negotiated.

See [api.md#head-options-and-405](../api.md#head-options-and-405) and [api.md#representation](../api.md#representation).

## Consequences

- A client that read the OPTIONS metadata gets an empty body. A client that read `Allow` from a 200 or a 401 has to send OPTIONS.
- A wrong method with an unacceptable `Accept` is a 405, not a 406.
- The negotiation keeps only the RFC rules. The DRF limits and the choice between several renderers are gone, together with their tests.

## Alternatives

- **Keep DRF's answers.** Rejected: they reproduce another framework with no reason of their own.
- **Drop the 406, as Ninja does by ignoring `Accept`.** Rejected: the contract declares it, and honoring `Accept` is the template's own choice under RFC 9110.
- **OPTIONS as a 405**, since Ninja has no answer for it. Rejected: Django answers OPTIONS on every view.
