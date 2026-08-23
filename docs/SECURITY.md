# Security design — OWASP Top 10 (2021)

How each category is addressed, and where in the code. Every control listed here is
covered by the end-to-end test suite unless marked otherwise.

---

## A01 — Broken Access Control

The most important category for this system, because "everyone sees everything, only the
author edits" is exactly the kind of rule that gets enforced in the UI and forgotten on
the server.

- Authorisation is **server-side only**. `can_edit` in an API response is a UI hint; the
  server independently re-checks on every write (`deps.require_edit_permission`).
- Deny by default: a route with no auth dependency receives no user context at all.
  Admin routes take `Depends(admin_user)`; there is no role flag in a request body.
- The session is re-resolved against the database on **every** request
  (`deps.optional_user`), so deactivating or deleting a user revokes access immediately
  rather than at token expiry. Role downgrades and deactivations also call
  `revoke_all_sessions`.
- **IDOR**: survey and file ids are opaque `ObjectId`s validated before use. Files not yet
  attached to a survey are readable only by their uploader; attaching a file to a survey
  is refused if it already belongs to a different survey (`_claim_files`).
- Mass assignment is impossible: `SurveyPayload` is `extra="forbid"` and lists only the
  fields a surveyor may write. `owner_email`, timestamps and `deleted` are set by the
  server; sending `owner_email` in the body returns 422.
- The owner account cannot be deleted, deactivated or demoted, so lockout is impossible.
- Path traversal: the SPA fallback resolves with `os.path.realpath` and requires the
  result to stay under the frontend root.

## A02 — Cryptographic Failures

- Every password is hashed with **Argon2id** (`time_cost=3, memory_cost=64 MiB`). No
  password is ever stored, logged or returned in clear text; a one-time password exists
  in plaintext only inside the single HTTP response that mints it.
- Password reset tokens are 256 bits of `secrets.token_urlsafe` entropy, stored as a
  SHA-256 digest, single use, and time limited.
- Sessions are HS256 JWTs signed with `SESSION_SECRET`; the app refuses to boot if that
  secret is missing, a known placeholder, or shorter than 32 characters.
- Session cookies are `HttpOnly`, `SameSite=Strict`, and `Secure` automatically whenever
  `PUBLIC_ORIGIN` is `https://`. HSTS is emitted on the same condition.
- The JWT carries an `iss` bound to `PUBLIC_ORIGIN` and is verified against it, and a
  `jti` that must still exist server-side — so tokens are genuinely revocable.
- No secret is ever logged. `GET /api/users` explicitly projects away `password_hash`
  and `reset_token_hash`, and the one-time password is returned only by the create and
  reset endpoints themselves, never by any listing.
- TLS itself is terminated by Cloudflare; nothing listens on a public interface.

## A03 — Injection

- MongoDB is only ever queried through the driver with typed values — no string-built
  queries, no `$where`, no `eval`.
- Every request body is a strict Pydantic model. Enum-like fields (`sector`, `wet`,
  `cardboard`, container types, volumes, frequencies) are checked against an allowlist,
  so `{"$ne": null}` in `biz_name` fails validation with 422.
- `reject_operator_keys` walks the parsed body and refuses any key starting with `$` or
  containing `.`, as defence in depth against NoSQL operator injection.
- Search terms go through `escape_regex`, so `.*` is matched literally instead of
  becoming a catastrophic regex.
- `clean_text` NFC-normalises and strips control characters from free text.
- **XSS**: the frontend never assigns user data to `innerHTML`; all dynamic content is
  built with `document.createElement` + text nodes (`UI.el`). The CSP has no
  `unsafe-inline` for scripts and no `unsafe-eval`, so even a missed sink cannot execute.

## A04 — Insecure Design

- Sign-in is invitation-only by design: no self-registration path exists in the code, so a
  misconfiguration cannot accidentally open one.
- Credentials are issued, never chosen by the recipient: a new account starts with a
  server-generated one-time password that grants no session until it is replaced, so a
  user can never pick a weak password for the account's whole lifetime.
  A one-time password that is never used is therefore not a standing weakness.
- Recovery has two independent paths (emailed single-use link, admin reissue) so losing
  a password never requires touching the database, and neither path ever reveals whether
  an address exists.
- Signatures are immutable once captured — a deliberate constraint for a document that
  carries legal weight. Only an admin can reset one, and the reset is audited.
- Cities with surveys attached are deactivated rather than deleted, so survey data can
  never be orphaned.
- Deletes are soft, keeping records recoverable and out of the unique index.
- Request bodies are capped before being read; uploads are streamed with a hard ceiling.
- Rate limits on login (split per address and per account), password reset, first
  login, uploads, exports and general API traffic.

## A05 — Security Misconfiguration

- Containers run as an unprivileged user (uid 10001) with `no-new-privileges`.
- MongoDB runs with `--auth` and **publishes no port**. The application connects as a
  least-privilege user with `readWrite` on one database; the root account is used only by
  the backup job.
- The API binds to `127.0.0.1` on the host; the tunnel reaches it over the internal
  Docker network.
- Interactive API docs and the OpenAPI schema are disabled when `ENVIRONMENT=production`.
- `TrustedHostMiddleware` rejects Host headers that do not match `PUBLIC_ORIGIN`.
- The `Server` response header is removed; uvicorn runs with `--no-server-header`.
- Unhandled exceptions return a generic Hebrew message — stack traces go to the log only.
- Response headers on every request:

```
Content-Security-Policy  default-src 'self'; object-src 'none'; frame-ancestors 'none';
                         script-src 'self'; frame-src 'none'; connect-src 'self'; …
X-Content-Type-Options   nosniff
X-Frame-Options          DENY
Referrer-Policy          strict-origin-when-cross-origin
Permissions-Policy       camera=(self), microphone=(), geolocation=(), payment=(), usb=()
Cross-Origin-Opener-Policy / Resource-Policy
Strict-Transport-Security  (https only)
```

`camera=(self)` is present deliberately — the form takes photos directly.

## A06 — Vulnerable and Outdated Components

- Every Python dependency is pinned to an exact version in `requirements.txt`; base
  images are pinned to a minor version.
- The frontend has **no build step, no npm dependency tree and no third-party scripts
  at all** — plain HTML, CSS and JavaScript served from our own origin. The CSP is
  correspondingly strict: `script-src 'self'`, `frame-src 'none'`, `connect-src 'self'`.
- Update path: bump the pins, `docker compose up -d --build`. Volumes are untouched.

## A07 — Identification and Authentication Failures

- **No self-registration exists in the codebase.** An account can only be created by an
  authenticated admin; the login endpoint refuses any address without an active record.
- New accounts get a one-time password from a 56-character unambiguous alphabet
  (no `0/O`, `1/l/I`), flagged `must_change_password`. Until it is replaced the account
  receives **no session cookie at all** — only a 15-minute, single-purpose change token.
  It cannot read or write a single record in that state.
- The chosen password must differ from the one-time password, must be at least
  `PASSWORD_MIN_LENGTH` characters with a letter and a digit, must not appear in a
  common-password list, and must not contain the user's own address.
- A wrong password and an unknown address return **byte-identical** responses, and an
  unknown address still pays the cost of a dummy Argon2 verification, so neither the
  message nor the timing reveals which addresses exist.
- Rate limiting is split so that neither control undermines the other: per source address
  (`RATE_LOGIN`, default 40/5min) is generous enough for a whole crew behind one office
  NAT, while per account (`RATE_LOGIN_ACCOUNT`, default 12/5min) is what actually stops
  guessing. First-login and password-change have their own buckets so ordinary sign-in
  traffic cannot starve them.
- `MAX_LOGIN_ATTEMPTS` (default 6) failures lock an account for `LOCKOUT_MINUTES`
  (default 15). Every failure, lockout and reset is audited.
- Reset links are single use, expire in `RESET_TOKEN_TTL_MINUTES`, and **revoke every
  existing session** for that account on use. An admin reissuing a one-time password does
  the same.
- Sessions expire after `SESSION_TTL_HOURS` (default 12) and are revocable server-side.

## A08 — Software and Data Integrity Failures

- Uploads are validated three ways: extension allowlist, declared content type allowlist,
  and **magic-byte inspection**. A Windows executable renamed to `.pdf` is rejected.
- Images are decoded and **re-encoded** server side with Pillow. That strips EXIF and
  destroys anything hidden in a polyglot file — the stored bytes are produced by our own
  encoder, not the uploader's.
- SVG is not an accepted image type (it is a scriptable format).
- Documents are served with `Content-Disposition: attachment` and a per-file
  `Content-Security-Policy: default-src 'none'; sandbox`, so nothing renders in our origin.
  Only images and PDFs are served inline.
- No CDN for application code, so there is no third-party script to be tampered with.

## A09 — Security Logging and Monitoring Failures

- An append-only `audit` collection records logins (success, denial, lockout), admin
  password set/change/reset, user and city changes, survey create/update/delete,
  signature resets, file uploads, and every export — each with actor, target, client IP,
  user agent and timestamp. Entries expire after two years via a TTL index.
- The admin can read the recent log from the **הגדרות** screen.
- Container logs are size-capped and rotated (10 MB × 5).
- `GET /api/health` reports database connectivity for external monitoring.

## A10 — Server-Side Request Forgery

- The application never fetches a URL supplied by a user. The only outbound connections
  are to MongoDB and the configured SMTP host, both fixed by configuration. Removing the
  external identity provider removed the last third-party call from the request path.

---

## Cloudflare Tunnel specifics

- Nothing is published to the internet by the compose file. Only the tunnel egresses.
- The real client address is read from `CF-Connecting-IP`, which Cloudflare sets and a
  client cannot forge through the tunnel. A raw `X-Forwarded-For` chain is deliberately
  **not** trusted, and uvicorn runs without `--proxy-headers` so a spoofed header cannot
  become `request.client` and bypass rate limiting. Set `TRUST_CF_HEADERS=false` if the
  API is ever exposed without Cloudflare.
- `SameSite=Strict` cookies plus a double-submit CSRF token mean a cross-site request
  cannot act on a user's session even if it reaches the origin.
- Recommended extra layer: put **Cloudflare Access** in front of the hostname (it can
  authenticate against Google, Microsoft, a one-time PIN, and others). That turns the
  application password into a genuine second factor. Application auth stays
  authoritative either way.

---

## Residual risks and the honest limits

- **A password is the whole perimeter.** There is no second factor. The controls that
  make that defensible are the lockout, the split rate limits, the Argon2 cost and the
  forced replacement of issued credentials — but a shared or guessed password is a full
  compromise of that account. If you want more, put **Cloudflare Access** in front of the
  hostname; the application password then becomes a second factor behind it.
- **One-time passwords travel out of band.** The admin reads the password off the screen
  and relays it, or it is emailed in clear text if SMTP is on. Prefer handing it over in
  person or by a channel you trust, and rely on the forced change at first login.
- **Rate limiting is per client IP.** A distributed attacker with many addresses gets
  proportionally more attempts. Cloudflare's own WAF/rate rules are the answer at scale.
- **No antivirus scanning** of uploaded documents. Type and magic-byte checks stop the
  obvious cases and nothing renders in our origin, but a malicious PDF downloaded and
  opened locally is outside the app's control. Add ClamAV if that matters.
- **Backups are unencrypted** inside a Docker volume. Anyone with host access can read
  them. Encrypt the volume or copy dumps to encrypted storage if the host is shared.
- **Changing your own password does not drop your other sessions.** A password *reset*
  (emailed link, or admin reissue) does drop them all. If an account is believed
  compromised, use the reset path rather than a self-service change.
