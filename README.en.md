<div align="center">
  <sub><b>English</b> &nbsp;·&nbsp; <a href="README.md">简体中文</a></sub>

  <img src="logo/app-login-brand.svg" height="84" alt="LAN Share">
  <h1>LAN Share</h1>
  <p><b>Turn that dusty spare Windows PC into a private cloud your whole family actually uses.</b></p>
  <p>No public IP. No NAS to buy. No handing your photos to a cloud provider.</p>

  [![CI](https://github.com/AK47ZZQ/lan-share/actions/workflows/ci.yml/badge.svg)](https://github.com/AK47ZZQ/lan-share/actions/workflows/ci.yml)
  ![server](https://img.shields.io/badge/server-v3.70-2563eb)
  ![app](https://img.shields.io/badge/app-v1.1.15%2B122-2563eb)
  ![platform](https://img.shields.io/badge/platform-Windows%20%C2%B7%20Web%20%C2%B7%20Android-64748b)
  ![tests](https://img.shields.io/badge/tests-560%20passed-16a34a)
  ![license](https://img.shields.io/badge/license-AGPL--3.0-blue)
</div>

---

## Why this exists

**Consumer cloud storage keeps getting worse.** Free tiers shrink, downloads get throttled, subscriptions creep up every year, and there's no telling when a service shuts down. Worse: your family photos, scanned documents and decade of backups physically sit on someone else's servers.

**Self-hosting looks great until you hit the public IP wall.** Home broadband lives behind carrier-grade NAT. Dialing from the ONT still doesn't get you a routable address. DDNS can't point at your house, and DNS resolves to the ISP's gateway. Most "build your own cloud" tutorials end right there.

**But you already own a spare computer.** An old desktop, a retired laptop, a mini PC — plugged in, running 24/7, with terabytes of disk, doing absolutely nothing.

LAN Share connects those three facts:

> One idle PC + one tunneling connection = a cloud you fully own and can open from anywhere.

Your data lives on your disks. The service runs on your machine. No subscriptions, no throttling, no scanning — and you can change whatever you want.

## What it actually solves

Not "share a folder" — SMB already does that. This project is about **the daily reality of one household sharing storage**:

- Photos taken on any phone back up at home automatically, no monthly cable-and-export ritual
- Everyone gets their own account and private space, invisible to others; shared folders are granted per directory
- Links you send to relatives expire on their own, with an access code and download cap
- Works outside the house too — LAN and public access are feature-identical, not a crippled subset

<p align="center">
  <img src="docs/screenshots/overview.png" width="560" alt="Admin dashboard overview">
</p>
<p align="center"><sub>Web · admin dashboard: storage breakdown, members and quotas, activity feed, throttle metrics</sub></p>

## Mobile app

The web UI is the admin console; the app is what everyone in the family actually opens. Native Flutter, not a wrapped webview.

<p align="center">
  <img src="docs/screenshots/app-home.png" width="182" alt="Home">
  <img src="docs/screenshots/app-photos.png" width="182" alt="Photos">
  <img src="docs/screenshots/app-todo.png" width="182" alt="To-do">
</p>
<p align="center">
  <img src="docs/screenshots/app-notes.png" width="182" alt="Notes">
  <img src="docs/screenshots/app-me-1.png" width="182" alt="Me">
  <img src="docs/screenshots/app-me-2.png" width="182" alt="Account and security">
</p>
<p align="center"><sub>Home · photo timeline · to-do · notes · profile · account & security</sub></p>

- **Automatic photo backup** — shoots straight to your disk; the home screen shows when the last backup finished, so you never have to remember
- **Photo timeline** — three-column masonry, grouped by month from EXIF capture time, toggle photos/videos, jump via calendar
- **Account & security in one place** — password, separate WebDAV password, pairing codes, recovery codes, TOTP authenticator, app lock
- **Same backend, same permissions as the web** — delete a file on the phone and it's gone on the web instantly

## Requirements

| Item | Version | Notes |
|---|---|---|
| OS | Windows 10 / 11 | Services registered via NSSM; Linux/macOS needs systemd units instead |
| Python | 3.11+ | Backend runtime |
| PostgreSQL | 16 | Production database (tests can fall back to SQLite) |
| Node.js | 18+ | Only needed to build the frontend |
| Caddy | v2 | Reverse proxy and TLS, goes in `bin/caddy.exe` |
| NSSM | 2.24+ | Registers Python and Caddy as Windows services |

All paths and ports live in one file, **`env.bat`**. Defaults are derived from the repo's own location, so cloning elsewhere needs no edits.

## First-time deployment

```bash
# 1. Dependencies
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 2. Database (create it only; tables are built on first start)
createdb -h 127.0.0.1 -p 5433 -U postgres lanshare

# 3. Frontend assets (build once; the backend serves web/dist directly)
cd web && npm install && npm run build && cd ..

# 4. Drop in the binaries: caddy.exe into bin\, nssm.exe onto PATH
#    Download links are in docs/OPERATIONS.md under "Dependencies"

# 5. Register and start (admin PowerShell)
.\install-service.bat
net start lan-share-postgresql
net start lan-share-api
net start lan-share-caddy
```

Open `https://<your-ip>:8765`. With the self-signed cert, click through the warning once. First launch walks you through creating the admin account; everyone else joins with an invite code you generate.

### Day-to-day

```bash
powershell Restart-Service lan-share-api     # after backend changes
powershell Restart-Service lan-share-caddy   # after Caddyfile changes
cd web && npm run build                      # after frontend changes (no restart needed)
check.bat                                     # quality gate, required after backend changes
```

| Entry point | Address | Auth |
|---|---|---|
| Web UI (Vue 3) | `https://<IP>:8765` | Application-level login |
| Public (tunnel) | `https://<your-tunnel-domain>:<port>` | Same, fully equivalent |
| WebDAV | `https://<IP>:8765/dav/` | Basic (member accounts) |
| Health probe | `/healthz` | Unauthenticated, LAN only |

> Public access is wired up through a tunnel; see [docs/OPERATIONS.md](docs/OPERATIONS.md). The probe is deliberately not exposed publicly — that's internal state, and the internet has no business probing it.

## Features

| Area | What you get |
|---|---|
| **Multi-user** | admin / member / guest roles; private + shared spaces; per-directory permission matrix (member × folder, with expiry); invite-only signup; private space quotas; disabling a user kicks every session immediately |
| **Files** | Grid and list views, search, recent files, multi-select batch ops; unified previewer (image zoom/rotate, text / Markdown / CSV / docx / xlsx / pptx, audio & video, HEIC / HEIF with EXIF orientation) |
| **Transfer** | Adaptive chunking (≤2 GB → 5 MB … >10 GB → 64 MB), resumable uploads, fingerprint-based instant upload, 3 concurrent streams, pausable queue, drag-drop and paste batching |
| **Sharing** | Public links + QR codes (expiry / access code / download cap), targeted shares, folder zip download |
| **Photos** | Timeline (EXIF-first), lazy-loaded thumbnails, album highlights, automatic mobile backup |
| **Notes & to-do** | Categorized notes, to-do lists, calendar view |
| **Notifications** | In-app notification center, family activity feed, SSE push, Webhooks (ntfy / Bark / Telegram) |
| **Security** | App-level auth, optional TOTP 2FA, session and device management, audit log, per-entry-point brute-force accounting |
| **Clients** | Vue 3 web (installable PWA), native Flutter app (photo backup / system share / notifications) |

Full capability list and per-release changes: [CHANGELOG.md](CHANGELOG.md) (130 KB, newest first — written in Chinese).

## How it compares

|  | LAN Share | Nextcloud | Synology DSM | Consumer clouds |
|---|---|---|---|---|
| Needs public IP | No, tunneled | Recommended | Yes | — |
| Hardware cost | 0 (reuse a PC) | 0 (still need a server) | From ~$400 | — |
| Windows-native | Yes | Barely | Yes | — |
| Codebase size | Readable | Millions of lines | Closed | Closed |
| Data ownership | Fully yours | Yours | Yours | Vendor's |

**The trade-off, stated plainly**: Nextcloud does far more and is a mature product — following it is a perfectly good choice. This project optimizes for exactly one scenario: *one Windows machine, one household*. Enterprise features were cut so the codebase stays small enough to read and modify yourself. Pick whichever you want: a patch of land you can till, or a turnkey suite.

## Who should not use this

Being honest — skip it if any of these apply:

- **You need enterprise features** (LDAP, compliance audit, S3 backend, clustering) → use Nextcloud or MinIO
- **You want turnkey** — this needs Python, PostgreSQL and Caddy installed, plus comfort with admin PowerShell
- **You don't have a Windows machine** — the service scripts are `.bat` + NSSM; Linux needs its own systemd units
- **You need an iOS client** — `mobile/ios/` exists but is unbuilt and unverified; only the Android APK ships

## Architecture

```
                         ┌───────────────────────────────┐
external devices ──tunnel :8767──▶ │  Caddy v2 (TLS / proxy / auth) │
LAN devices ──local net :8765─▶    │  strips inbound XFF,           │
                                   │  injects entry-point marker    │
                                   └───────────────┬───────────────┘
                                                   │
        ┌──────────────────────────────────────────┼──────────────────────────┐
        ▼                                          ▼                          ▼
  /files/*  forward_auth first             /api/* /dav/* /s/*         /assets/* →
  then backend authorizes the            /up /preview/* /thumb/*     web/dist
  download (never file_server            ──▶ FastAPI :8766           (Vue 3 SPA
  directly — that leaks other                  │                     + deep-link
  users' private/)                             ├─ PostgreSQL 16 :5433  fallback)
                                               ├─ data/ storage root
                                               ├─ throttle / CSRF / brute-force / audit
                                               └─ SSE event stream
```

**Backend** FastAPI + uvicorn · **Gateway** Caddy v2 (no plugins) · **Database** PostgreSQL 16 + psycopg (prod) / SQLite (test)
**Frontend** Vue 3 + Vite + TypeScript · **App** Flutter (Dio + Riverpod + sqflite + secure_storage)
**Imaging** Pillow + pillow-heif (HEIC / EXIF) · **Other** pyotp (TOTP), qrcode, ffmpeg (optional)

## Scale

```
Backend  app/       12,628 lines    68 modules (api 29 · services 14 · store 11)
Web      src/        6,789 lines    Vue 3 + TypeScript
App      lib/       29,172 lines    Flutter
Tests    tests/     10,571 lines    65 files · 560 cases
API      143 paths / 167 operations (contracts/openapi.json)
```

The same test suite runs against both SQLite and PostgreSQL (isolated temp schema). The local gate `check.bat` has eight stages: pytest baseline + ruff + mypy + pip-audit + UI layering guard + SQLite dialect guard + docs baseline guard + security ablation.

## Docs

| What you need | Where |
|---|---|
| Full API reference | [docs/API.md](docs/API.md) |
| Security model | [docs/SECURITY.md](docs/SECURITY.md) |
| Operations (services / backup / certs / failures) | [docs/OPERATIONS.md](docs/OPERATIONS.md) |
| Dev environment and commands | [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) |
| Directory layout | [docs/PROJECT-LAYOUT.md](docs/PROJECT-LAYOUT.md) |
| App visual spec | [docs/DESIGN-v3/APP-VISUAL-SPEC.md](docs/DESIGN-v3/APP-VISUAL-SPEC.md) |
| Release history | [CHANGELOG.md](CHANGELOG.md) |

<details>
<summary><b>FAQ</b></summary>

| Problem | Fix |
|---|---|
| Certificate warning | Expected with a self-signed cert — click through, or import `certs/cert.pem` into your root store |
| Forgot admin password | Truncate the users table and hit `/api/setup` again (only works while empty; first user becomes admin). Re-invite members afterwards |
| How do members join | Admin → Invite → generate a code → they use "join with invite code" on the login page |
| Public share won't open | Make sure you copied the public link (configure the public address in the admin panel first) and that the tunnel is up |
| Mapping a network drive fails on Windows | The native WebClient is picky about self-signed certs — use RaiDrive or WinSCP |
| Frontend changes don't show up | Hard-refresh once (Ctrl+F5); after that, hashed asset names update automatically |
| Health probe invisible from outside | By design — it's LAN-only, internal state isn't exposed publicly |
| `install-service.bat` says nssm not found | Put `nssm.exe` on PATH, or set `LANSHARE_NSSM` to its location |

</details>

## License

[GNU AGPL-3.0](LICENSE).

Use it, modify it, self-host it, build on it — all fine. The one hard rule: **if you run a modified version as a network service for others, your changes must also be released under AGPL-3.0.** Running it inside your own home or company carries no such obligation.

`data/`, `env/`, `.venv/`, credentials and certificates are all gitignored (see `.gitignore`).
