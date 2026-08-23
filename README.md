# מערכת סקרים עירוניים — Municipal Survey Management System

A production deployment of the surveyor form: FastAPI + MongoDB + Docker, email and
password sign-in with admin-issued credentials, per-city survey management,
server-generated Excel exports and a scheduled daily report by email.

---

## 1. What is in the box

| Service | Image | Purpose |
|---|---|---|
| `mongo` | `mongo:7.0` | Database. Survey documents **and** every uploaded photo/document/signature (GridFS). Never exposed outside the Docker network. |
| `api` | built from `backend/` | FastAPI application + the static frontend. Listens on `127.0.0.1:8080` only. |
| `worker` | same image | Scheduler: daily report email, orphan-upload cleanup, session purge. |
| `backup` | built from `backup/` | Nightly `mongodump` into a dedicated volume, with retention. |
| `cloudflared` | `cloudflare/cloudflared` | Optional Cloudflare Tunnel (profile `tunnel`). |

**All state lives in named volumes**, so rebuilding or updating containers never loses data:

| Volume | Contents |
|---|---|
| `shalev_mongo_data` | The database: surveys, users, cities, and all files |
| `shalev_mongo_config` | mongod internal config |
| `shalev_mongo_backups` | Nightly dump archives |
| `shalev_app_logs` | Application logs |

---

## 2. First-time setup

### 2.1 Accounts

There is nothing to register with an external provider. Sign-in is email + password,
and **there is no self-registration anywhere in the system**:

* the owner account (`ADMIN_EMAIL`, default `mosseriy1@gmail.com`) is created on first
  boot and its password is printed **once** to the container log;
* every other account is opened by an admin from the **משתמשים** screen, which mints a
  one-time password shown on screen (and emailed too, if SMTP is configured);
* the user must replace that one-time password at first sign-in before the account can
  do anything at all.

Optionally set `ADMIN_INITIAL_PASSWORD` in `.env` to choose the owner's first password
yourself instead of reading it from the log. It still has to be changed at first login.

### 2.2 Gmail app password (daily report + password reset links)

Outgoing mail is sent **from** `mosseriy1@gmail.com` and covers both the daily report
and the “forgot my password” reset links, so Gmail needs an app password:

1. The account must have **2-Step Verification** turned on.
2. Go to <https://myaccount.google.com/apppasswords>, create a password named e.g. "Surveys".
3. Paste the 16-character value into `SMTP_PASSWORD` (spaces can stay or be removed).

> Without SMTP the system still works: the daily report is simply disabled, and a
> forgotten password is recovered by an admin reissuing a one-time password from the
> **משתמשים** screen. The login page says so instead of promising an email.

### 2.3 Configuration file

Secrets are never committed, so a fresh clone has no `.env` and the stack will
refuse to start until you create one. The quickest way is the bundled script,
which copies `.env.example` and fills in strong random values for
`MONGO_ROOT_PASSWORD`, `MONGO_APP_PASSWORD` and `SESSION_SECRET`:

```bash
bash scripts/setup-env.sh
```

On Windows PowerShell:

```bash
powershell -ExecutionPolicy Bypass -File scripts\setup-env.ps1
```

Neither script ever overwrites an existing `.env`.

Then open `.env` and set the two values only you can supply:

- `PUBLIC_ORIGIN` — the exact origin the browser uses
  (`http://localhost:8080` for a local run)
- `SMTP_PASSWORD` — the Gmail app password from §2.2, if you want mail

To do it by hand instead, copy the template and replace every `CHANGE-ME`:

```bash
cp .env.example .env
```

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

The API **refuses to start** with placeholder or short secrets — that is deliberate.

### 2.4 Start

```bash
docker compose up -d --build
```

Check it came up, and grab the owner's first password:

```bash
docker compose ps && curl -s http://127.0.0.1:8080/api/health
```

```bash
docker compose logs api | grep -A4 "OWNER ACCOUNT CREATED"
```

That prints something like:

```
  OWNER ACCOUNT CREATED
  email    : mosseriy1@gmail.com
  password : 2jKPqsBcnVMznj
```

Sign in with it once; the app immediately requires a permanent password of your own.
The printed one stops working the moment you do.

---

## 3. Cloudflare Tunnel

Nothing is published to the internet by the compose file — the API binds to
`127.0.0.1` only. Two ways to put it online:

### Option A — tunnel inside compose (recommended)

1. In the Cloudflare Zero Trust dashboard: **Networks → Tunnels → Create a tunnel →
   Cloudflared**, copy the tunnel token.
2. Put it in `.env` as `CLOUDFLARE_TUNNEL_TOKEN=…`.
3. Add a public hostname routing to **`http://api:8000`** (the service name inside the
   Docker network, *not* localhost).
4. Start with the tunnel profile:

```bash
docker compose --profile tunnel up -d
```

### Option B — cloudflared already running on the host

Point the tunnel at `http://localhost:8080` and leave the `tunnel` profile off.

### Cloudflare settings that matter

- `PUBLIC_ORIGIN` in `.env` must equal the public hostname **exactly**, including
  `https://`. Session cookies, the CSRF check and the JWT issuer are all bound to it.
- Keep **SSL/TLS mode = Full**. The tunnel terminates TLS at Cloudflare.
- The app trusts `CF-Connecting-IP` for rate limiting and the audit log. That header is
  set by Cloudflare and cannot be spoofed through the tunnel. If you ever expose the API
  without Cloudflare, set `TRUST_CF_HEADERS=false`.
- Optional extra layer: put **Cloudflare Access** in front of the hostname and restrict it
  to your users' Google accounts. Two independent gates instead of one.

---

## 4. Roles and access

### Owner / admin — `mosseriy1@gmail.com`

Created automatically on first boot with a one-time password printed to the log (or taken
from `ADMIN_INITIAL_PASSWORD`). At the first sign-in the app forces a permanent password
of at least 10 characters containing a letter and a digit. Six wrong attempts lock the
account for 15 minutes; another admin can unlock it or reissue a one-time password.

Admin screens: dashboard, city picker, survey form, all records, **users**, **cities**,
**export**, **settings** (daily email + activity log), **my password**.

### Surveyors

The admin opens an account from **משתמשים** by entering an email address. The system
mints a one-time password and shows it on screen with a copy button — that is the only
time it is ever visible, since only its hash is stored. Hand it to the worker; at their
first sign-in they must choose their own password.

There is no sign-up form, no invite link that self-provisions, and no endpoint that
creates an account without an authenticated admin.

| Action | Surveyor | Admin |
|---|---|---|
| Fill a survey for any city | ✅ | ✅ |
| **See** every survey in the system | ✅ | ✅ |
| **Edit / delete** surveys | only their own | any |
| Change own password | ✅ | ✅ |
| Users, cities, exports, settings | ❌ | ✅ |

Opening a survey created by someone else shows the complete form read-only, with all
photos previewable and documents openable.

### Forgotten passwords

Two independent routes, so nobody is ever permanently stuck:

1. **Self-service** — *שכחתי את הסיסמה* on the login screen emails a single-use link,
   valid for 60 minutes (`RESET_TOKEN_TTL_MINUTES`). Using it drops every existing
   session for that account, in case the reset was triggered by a compromise. Requires
   SMTP.
2. **Admin reissue** — 🔑 next to any user in **משתמשים** mints a new one-time password
   and invalidates the old one plus all that user's sessions. Works with no SMTP at all,
   and covers the owner account too.

The reply to a reset request is identical whether or not the address exists, so the form
cannot be used to discover who has an account.

### Signatures

A signature is captured once and then locked — it cannot be redrawn or replaced by
anyone, including the surveyor who took it. Only an admin can reset it (`איפוס (מנהל)`
on the form), which deletes the old image and allows a fresh signature. Every reset is
written to the audit log.

---

## 5. Business numbers and branches

A business number identifies one business globally, and the same business may have
several branches. The uniqueness key is therefore **business number + city + address**.

Typing a business number into the search box shows every branch already recorded for it,
across all cities, as clickable buttons. Saving the same number at a new address creates
a new branch; saving it at an existing address is refused with a message pointing at the
existing record.

---

## 6. Excel exports

Both files are generated **server-side** (openpyxl), one worksheet per municipality,
right-to-left.

**קובץ נתונים מלא** — every survey field, plus the surveyor's name and timestamps.
Signatures and up to three photos are embedded as **real pictures** in the cells (the
old browser version could only paste base64 text). Untick *הטמע תמונות* for a small,
fast, text-only file.

**קובץ לתחשיב** — the A–V layout, unchanged from the original:

- one breakdown row per container entry (business name repeated on every row);
- `M = I*J*K*L` on the first row of each business;
- `O = M*N` inline on **every** breakdown row;
- `U = T*S*R*P` on every breakdown row;
- a summary row with `SUM()` over the block and `V = U(summary) − O(first row)`;
- `K` = 1.5 תעשייה / 2 מסחר, `L` = 0.8 לא מפנה / 1 otherwise, `T` = collections per month.

Columns J, N, R, S are left blank for the operator, exactly as before.

---

## 7. Daily report email

**הגדרות** screen, admin only:

- checkbox to turn the daily email on or off;
- send time, in Israel time (`Asia/Jerusalem`);
- scope — everything in the system, or only the last 24 hours;
- recipient (defaults to `mosseriy1@gmail.com`);
- **שלח עכשיו לבדיקה** to send immediately and confirm SMTP works.

The `worker` container re-reads the chosen time every minute, so changes take effect
without a restart. Both workbooks are attached.

---

## 8. Backups and restore

The `backup` service dumps the whole database — including every uploaded file — once a
day (default 02:00) and on every startup, keeping 30 days.

```bash
# List backups
docker compose exec backup ls -lh /backups

# Copy a backup out of the volume to the host
docker compose cp backup:/backups/surveys-20260821-020000.archive.gz ./
```

Restore:

```bash
docker compose cp ./surveys-20260821-020000.archive.gz backup:/tmp/restore.gz
docker compose exec backup mongorestore \
  --host mongo --username "$MONGO_ROOT_USER" --password "$MONGO_ROOT_PASSWORD" \
  --authenticationDatabase admin \
  --archive=/tmp/restore.gz --gzip --drop
```

> `--drop` replaces the current data. Take a fresh dump first if you are unsure.

Tune with `BACKUP_HOUR`, `BACKUP_MINUTE`, `BACKUP_RETENTION_DAYS` in `.env`.

---

## 9. Updating

```bash
docker compose pull
docker compose up -d --build
```

Containers are replaced; the four named volumes are untouched, so surveys, files,
backups and logs all survive. Nothing in the images holds state.

---

## 10. Everyday commands

```bash
docker compose logs -f api          # follow API logs
docker compose logs -f worker       # scheduler activity
docker compose restart api          # restart just the API
docker compose down                 # stop (volumes kept)
docker compose down -v              # stop AND DELETE ALL DATA - do not run casually
```

---

## 11. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| API exits immediately, log says `[config] FATAL` | A secret in `.env` is still a placeholder or shorter than 32 chars. |
| Do not know the owner password | `docker compose logs api \| grep -A4 "OWNER ACCOUNT CREATED"`. If the log has rotated away, reset it: see the next row. |
| Owner locked out entirely | Clear the password directly, then sign in with a new one-time password on the next boot: `docker compose exec mongo mongosh -u "$MONGO_ROOT_USER" -p "$MONGO_ROOT_PASSWORD" --authenticationDatabase admin surveys --eval 'db.users.deleteOne({email:"mosseriy1@gmail.com"})'` then `docker compose restart api` and read the log again. Surveys are untouched. |
| `כתובת המייל או הסיסמה שגויים` | Wrong password, or the address has no account. The message is deliberately the same for both so the form cannot be used to discover accounts. |
| `החשבון נעול זמנית` | Six failed attempts. Wait 15 minutes, or have an admin press 🔓 / 🔑 in **משתמשים**. |
| User never got the one-time password | It is shown once on screen when the account is created. If it was missed, press 🔑 next to the user to issue a new one. |
| *שכחתי את הסיסמה* says reset is not configured | `SMTP_USER` / `SMTP_PASSWORD` are unset. Either configure Gmail (§2.2) or use the admin 🔑 reissue instead. |
| Reset link says it is invalid | Links are single use and expire after 60 minutes. Request a new one. |
| Login works, every action then fails with CSRF | `PUBLIC_ORIGIN` does not match the address in the browser bar. They must be identical. |
| Daily email never arrives | `SMTP_PASSWORD` must be a Gmail **app password**, not the account password. Use **שלח עכשיו לבדיקה** to see the real error. |
| Camera button does nothing | Browsers only allow the camera over HTTPS (or `localhost`). Through the tunnel it works; over plain `http://<lan-ip>` it does not. |
| Whole team getting `יותר מדי בקשות` on login | They share one public IP. Raise `RATE_LOGIN` (per address) in `.env`; `RATE_LOGIN_ACCOUNT` stays low because that is the bucket that actually stops guessing. |
| Mongo will not start | MongoDB 5+ needs a CPU with AVX. On older hardware, change the image to `mongo:4.4`. |

---

## 12. Security

Full OWASP Top 10 mapping: [`docs/SECURITY.md`](docs/SECURITY.md).
