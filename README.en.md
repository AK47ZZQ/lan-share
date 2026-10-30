<div align="right">
  <sub><b>English</b> &nbsp;·&nbsp; <a href="README.md">简体中文</a></sub>
</div>

<div align="center">
  <img src="logo/app-login-brand.svg" height="84" alt="LAN Share">
  <h1>LAN Share</h1>
  <p><b>Turn that dusty Windows PC at home into a private cloud drive for the whole family.</b></p>
  <p>Works without a public IP. No NAS to buy. Your photos never sit in someone else's datacenter.</p>

  [![CI](https://github.com/AK47ZZQ/lan-share/actions/workflows/ci.yml/badge.svg)](https://github.com/AK47ZZQ/lan-share/actions/workflows/ci.yml)
  ![server](https://img.shields.io/badge/server-v3.70-2563eb)
  ![app](https://img.shields.io/badge/app-v1.1.17%2B124-2563eb)
  ![platform](https://img.shields.io/badge/platform-Windows%20%C2%B7%20Web%20%C2%B7%20Android-64748b)
  ![tests](https://img.shields.io/badge/tests-560%20passed-16a34a)
  ![license](https://img.shields.io/badge/license-AGPL--3.0-blue)
</div>

---

## How this started

There's a Windows desktop at home that stays plugged in all year. The electricity bill is nothing. The machine does nothing.

Meanwhile I was paying for cloud storage, and the free tier kept shrinking.

So I tried to put that box to work. Read a pile of tutorials. They all died at the same place: I don't have a public IP.

My connection sits behind carrier NAT. Even dialing PPPoE on the modem doesn't hand out a real address. I pointed DDNS at a domain once and it resolved to a 100.64.x.x — which does not route to my house. Most self-hosting guides quietly assume you have a public IP. The rest say "just request one from your ISP" and stop there, because most residential ISPs won't give you one anymore.

I got around it with a tunnel. Bring the tunnel up and the service at home is reachable from anywhere. Slower, but fine — nobody's family photos care about two extra seconds.

After that there was no reason to keep renting storage.

Which is also why everything runs on Windows. That machine is Windows. Reinstalling it just for this wasn't worth it.

## What it actually does

Not "share a folder" — SMB already does that, and it wouldn't justify any of this code. This is about the day-to-day of a household.

Photos get backed up the moment you take them. No monthly cable-and-copy ritual, and no panic when a phone goes missing.

Everyone gets their own account and private space, invisible to the others. Shared folders are authorized per directory, with optional expiry.

Links you send to relatives carry an extraction code and an expiration date. When the date passes they stop working, and you don't have to remember to clean up.

It works away from home, with the exact same feature set as on the LAN. That's the part I use most — pulling a scanned document out of the house while travelling.

<p align="center">
  <img src="docs/screenshots/overview.png" width="560" alt="Admin dashboard · overview">
</p>
<p align="center"><sub>Web · admin dashboard: storage breakdown, members and quotas, activity feed, live rate observation</sub></p>

## The app

The web UI is where you manage things as the owner. The app is what your family actually opens. Native Flutter, not a webview wrapper.

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
<p align="center"><sub>Home · photo timeline · to-do · notes · me · account and security</sub></p>

Photo backup is the main draw. It uploads as you shoot, and the home screen tells you when the last backup happened so you don't have to wonder.

Photos are grouped by month using the EXIF capture date, shown in a three-column waterfall. You can switch between photos and videos, and the calendar in the corner jumps straight to a given month.

Passwords, a separate WebDAV password, pairing codes, recovery codes, authenticator setup and app lock all live on one "account and security" page instead of scattered across menus.

The phone and the web share one backend and one set of permissions. Delete something on the phone and it's gone after a refresh on the web.

## Check these first

| Item | Version | Notes |
|---|---|---|
| OS | Windows 10 / 11 | Services registered via NSSM; on Linux you'll write your own systemd units |
| Python | 3.11+ | Backend runtime |
| PostgreSQL | 16 | Production; tests can fall back to SQLite |
| Node.js | 18+ | Only needed to build the frontend |
| Caddy | v2 | Reverse proxy and TLS, drop it at `bin/caddy.exe` |
| NSSM | 2.24+ | Runs Python and Caddy as Windows services |

Every path and port lives in one file, **`env.bat`**, derived from wherever the repo itself sits. Move the repo and nothing needs editing.

## Install

```bash
# 1. Dependencies
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 2. Database (create it empty; tables are built on first start)
createdb -h 127.0.0.1 -p 5433 -U postgres lanshare

# 3. Frontend (build once, the backend serves the output directly)
cd web && npm install && npm run build && cd ..

# 4. Binaries: caddy.exe into bin\, nssm.exe onto your PATH
#    Download links are in docs/OPERATIONS.md under "dependencies"

# 5. Register the services and start them (elevated PowerShell)
.\install-service.bat
net start lan-share-postgresql
net start lan-share-api
net start lan-share-caddy
```

Open `https://<your-ip>:8765`. The self-signed certificate will scare the browser; click through it. First launch walks you through creating the admin account — everyone else joins with an invite code you generate.

### Day to day

```bash
powershell Restart-Service lan-share-api     # backend changes
powershell Restart-Service lan-share-caddy   # Caddyfile changes
cd web && npm run build                      # frontend changes (no restart needed)
check.bat                                     # run this after touching the backend
```

| Entry | Address | Auth |
|---|---|---|
| Web (Vue 3) | `https://<IP>:8765` | Application login |
| Public (tunnel) | `https://<your-tunnel-host>:<port>` | Same, full feature parity |
| WebDAV | `https://<IP>:8765/dav/` | Basic (member account) |
| Health probe | `/healthz` | Unauthenticated, LAN only |

The probe is deliberately not exposed to the internet. It reports internal state, and internal state isn't something strangers should be able to scan. Tunnel setup is covered in [docs/OPERATIONS.md](docs/OPERATIONS.md).

## What's in it

| Area | What you get |
|---|---|
| **Multi-user** | admin / member / guest roles; separate private and shared spaces; per-directory authorization as a member × directory matrix with optional expiry; invite-only signup; private space quotas; disabling an account kicks every session instantly |
| **Files** | Grid and list views, search, recent files, multi-select batch actions; a unified previewer handling images (zoom, rotate), text / Markdown / CSV / docx / xlsx / pptx, audio and video, plus HEIC and HEIF with EXIF orientation correction |
| **Transfer** | Chunk sizes that adapt to file size (5 MB under 2 GB, 64 MB above 10 GB), resumable uploads, instant upload by fingerprint, three concurrent streams, pausable queue, drag-and-drop and paste batching |
| **Sharing** | Public links with QR codes, expiry dates, extraction codes and download caps; targeted shares to a specific member; directory downloads packed as an archive |
| **Photos** | Timeline driven by EXIF, lazy-loaded thumbnails, album highlights, automatic mobile backup |
| **Notes and to-dos** | Categorized notebooks, checklists, calendar view |
| **Notifications** | In-app notification center, family activity feed, SSE push, webhooks to ntfy / Bark / Telegram |
| **Security** | Application-level auth, optional TOTP two-factor, session and device management, audit log, per-entry brute-force accounting |
| **Clients** | Vue 3 web app (PWA, installable offline) and a native Flutter app (photo backup, system share, notification center) |

Version-by-version history is in [CHANGELOG.md](CHANGELOG.md) — 130 KB, newest first, written in Chinese.

## Compared to alternatives

|  | LAN Share | Nextcloud | Synology DSM | Commercial cloud |
|---|---|---|---|---|
| Needs a public IP | No, uses a tunnel | Recommended | Yes | — |
| Hardware cost | 0 (an old PC) | 0 (you supply the machine) | From 3000 CNY | — |
| Native on Windows | Yes | Barely | Yes | — |
| Code size | Readable end to end | Millions of lines | Closed | Closed |
| Where your data lives | Your disk | Your disk | Your disk | Their datacenter |

Nextcloud does far more than this and is a genuinely mature product; going with it is a reasonable choice. This project optimizes for exactly one scenario — one Windows machine, one household — and cuts every enterprise feature to keep the codebase small enough that I can read it and change it on a whim.

Whether you want a plot of land you can dig in, or a fully furnished house, is your call.

## When not to use this

A few honest reasons to walk away:

- You need LDAP, compliance auditing, S3 backends or multi-node setups. Use Nextcloud or MinIO.
- You want something that works out of the box. This needs Python, PostgreSQL and Caddy installed, plus an elevated PowerShell.
- Your machine isn't Windows. The service scripts are `.bat` plus NSSM; on Linux you're writing systemd units.
- You need an iOS client. `mobile/ios/` has a project but it's never been built or verified; only the Android APK ships.

## Architecture

```
                         ┌───────────────────────────────┐
Internet ──tunnel :8767─▶│  Caddy v2 (TLS / proxy / auth)│
LAN      ──local :8765──▶│  strips inbound XFF, tags origin
                         └───────────────┬───────────────┘
                                         │
        ┌────────────────────────────────┼────────────────────────────┐
        ▼                                ▼                            ▼
  /files/* → forward_auth          /api/* /dav/* /s/*           /assets/* →
  then backend decides access      /up /preview/* /thumb/*      web/dist
  (never file_server directly —    ──▶ FastAPI :8766           (Vue 3 SPA
   a crafted path would read            │                       + history fallback)
   someone else's private/)             ├─ PostgreSQL 16 :5433
                                        ├─ data/ directory
                                        ├─ rate limit / CSRF / brute force / audit
                                        └─ SSE events
```

`/files/` goes through `forward_auth` before the backend rules on access because serving it with `file_server` directly lets a crafted path read another user's `private/`. Learned that one the hard way.

**Backend** FastAPI + uvicorn · **Gateway** Caddy v2, stock build · **Database** PostgreSQL 16 + psycopg in production, SQLite for tests
**Frontend** Vue 3 + Vite + TypeScript · **App** Flutter with Dio, Riverpod, sqflite and secure_storage
**Imaging** Pillow + pillow-heif for HEIC and EXIF · **Also** pyotp for TOTP, qrcode, optional ffmpeg

## Size

```
backend  app/     12,628 lines    68 modules (api 29 · services 14 · store 11)
web      src/      6,789 lines    Vue 3 + TypeScript
app      lib/     29,172 lines    Flutter
tests    tests/   10,571 lines    65 files · 560 tests
API contract       143 paths / 167 operations   (contracts/openapi.json)
```

The same test suite runs against both SQLite and PostgreSQL, isolated in a temporary schema so it never touches your real data. The local gate `check.bat` runs eight stages: pytest baseline, ruff, mypy, pip-audit, a UI layering guard, a SQLite dialect guard, a docs baseline guard and a security ablation pass.

## Docs

| What you want | Where |
|---|---|
| Full API | [docs/API.md](docs/API.md) |
| Security model | [docs/SECURITY.md](docs/SECURITY.md) |
| Operations (services, backups, certificates, failures) | [docs/OPERATIONS.md](docs/OPERATIONS.md) |
| Dev environment and commands | [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) |
| Directory layout | [docs/PROJECT-LAYOUT.md](docs/PROJECT-LAYOUT.md) |
| App visual spec | [docs/DESIGN-v3/APP-VISUAL-SPEC.md](docs/DESIGN-v3/APP-VISUAL-SPEC.md) |
| Version history | [CHANGELOG.md](CHANGELOG.md) (Chinese) |

<details>
<summary><b>FAQ</b></summary>

| Problem | Fix |
|---|---|
| Certificate not trusted | It's self-signed. Click through, or import `certs/cert.pem` into your system trust store |
| Forgot the admin password | Empty the users table and re-run `/api/setup` — it only opens on an empty table, and the first user becomes admin. Re-invite members after |
| How do family members join | Admin → Invites → generate a code → they pick "join with invite code" on the login page |
| A public share link won't open | Check you copied the public link (the admin panel needs the public address configured first), then confirm the tunnel is up |
| Mapping a network drive on Windows fails | The built-in WebClient is picky about self-signed certificates. Use RaiDrive or WinSCP |
| Frontend changes don't show up | Hard refresh with Ctrl+F5. Asset filenames are hashed afterwards, so later builds update themselves |
| The health probe is invisible from the internet | By design. It's LAN-only and never exposes internal state publicly |
| `install-service.bat` can't find nssm | Put `nssm.exe` on your PATH, or set `LANSHARE_NSSM` to point at it |

</details>

## License

[GNU AGPL-3.0](LICENSE).

Use it, change it, run it yourself — all fine. One hard rule: if you modify it and offer it to others as a network service, your changes have to be released under AGPL-3.0 too. Running it for your own household doesn't trigger that.

`data/`, `env/`, `.venv/`, credentials and certificates are excluded from the repo. See `.gitignore`.
