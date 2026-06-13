# Self-Hosting on a VPS (OPTIONAL)

> **This is an optional deployment path.** Managed hosting (e.g. Railway) is the
> default and the simplest way to run this framework — you do not need a VPS to
> use it. Reach for this guide only if you specifically want a 24/7 instance on
> infrastructure you fully control.
>
> **No credentials, provisioning, or hosting are included here.** You bring your
> own VPS, SSH keys, and provider API keys. The files under `deploy/vps/` are
> plain scripts and a systemd unit — read them before running them.

This guide provisions a [Hostinger](https://www.hostinger.com/vps-hosting) VPS
(Ubuntu) to run the orchestration loop continuously, with auto-restart. The same
steps work on any Ubuntu 22.04/24.04 VPS; Hostinger is the example provider.

---

## What "running 24/7" means here

The framework is a set of stdlib-only one-shot CLI scripts, not a server. The
self-host loop (`deploy/vps/run_loop.sh`) simply runs the router on an interval
and reports the resume queue, so unrouted tasks keep getting routed and
interrupted work stays visible without a human at a terminal. systemd keeps the
loop alive and restarts it on crash.

There is **no inbound network service** — nothing listens on a port. The only
open port is SSH. This keeps the attack surface tiny.

---

## 1. Create the VPS (Hostinger)

1. In the Hostinger panel, create a **KVM VPS** with **Ubuntu 24.04** (or 22.04).
2. Set the root password / upload your SSH public key during creation.
3. Note the server's public IP.

There is nothing Hostinger-specific beyond this; any Ubuntu VPS works.

## 2. First SSH login + SSH hardening

```bash
ssh root@YOUR_SERVER_IP
```

Harden SSH (edit `/etc/ssh/sshd_config`):

```bash
# Use key-based auth only and disable root password login.
PermitRootLogin prohibit-password
PasswordAuthentication no
```

Then reload: `sudo systemctl reload ssh`.

> Make sure your SSH **public** key is in `~/.ssh/authorized_keys` *before*
> disabling password auth, or you will lock yourself out.

## 3. Create a non-root sudo user (recommended)

```bash
adduser deploy
usermod -aG sudo deploy
rsync --archive --chown=deploy:deploy ~/.ssh /home/deploy/
```

Log back in as `deploy` and use `sudo` from here on. (The orchestration loop
itself runs as a separate locked-down `orchestrator` service account created by
the setup script — not as `deploy` or `root`.)

## 4. Firewall (ufw)

```bash
sudo ufw allow OpenSSH
sudo ufw enable
sudo ufw status
```

`setup.sh` also does this, but enabling it up front is good hygiene. Only SSH
is allowed in; no other ports are opened.

## 5. Install Python 3.11 + git

```bash
sudo apt update
sudo apt install -y git python3.11 python3.11-venv
```

On Ubuntu 22.04, Python 3.11 comes from the `deadsnakes` PPA (the setup script
adds it automatically). On 24.04 it is available natively.

## 6. One-shot install

Clone the repo and run the provisioning script (it is idempotent):

```bash
sudo git clone https://github.com/InonB2/multi-agent-orchestration.git /opt/orchestration
cd /opt/orchestration
sudo bash deploy/vps/setup.sh
```

`setup.sh` will:

- install Python 3.11 + git + ufw,
- create the non-root `orchestrator` service user,
- create a virtualenv and install `pytest`/`flake8`/`tomli` (the core framework
  is stdlib-only; these are for tests/lint and TOML parsing on Python < 3.11),
- copy `deploy/vps/.env.example` → `/opt/orchestration/.env` (mode `600`),
- install and enable the `orchestration-loop` systemd service,
- enable the ufw firewall (SSH only).

## 7. Inject secrets (.env — never committed)

Edit the generated env file and add only the provider keys you use:

```bash
sudo -u orchestrator nano /opt/orchestration/.env
```

```ini
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
ROUTER_INTERVAL=300
```

`.env` is in `.gitignore` and is **never** committed. systemd loads it via
`EnvironmentFile=`; secrets exist only on the box. After editing:

```bash
sudo systemctl restart orchestration-loop
```

## 8. Verify

```bash
systemctl status orchestration-loop      # should be 'active (running)'
journalctl -u orchestration-loop -f      # live logs of each routing pass
```

To feed it work, drop a `tasks/active_tasks.json` (see
`examples/sample_active_tasks.json` for the shape) into `/opt/orchestration/tasks/`.

---

## Updating / redeploying

```bash
cd /opt/orchestration
sudo bash deploy/vps/update.sh
```

`update.sh` pulls the latest `main`, refreshes deps, **runs the test suite, and
restarts the service only if tests pass** — a broken pull never takes the
running loop down.

---

## Alternative: a systemd timer instead of a loop

If you prefer discrete scheduled runs over a long-lived process, replace the
service with a `oneshot` unit + timer (e.g. every 5 minutes). The loop approach
in `run_loop.sh` is simpler and is the documented default; the timer pattern is
left as an exercise and is a drop-in replacement for `ExecStart`.

---

## Optional: also host the Andy API Gateway + the 3 agent CLIs (MMOI)

The loop above is a headless task router with **no inbound port**. If you instead
(or additionally) want this box to host the **Andy API Gateway** (the FastAPI
routing service, normally on Railway) and run the three agent CLIs
(`claude`, `codex`, `agy`) for a CLI-default routing strategy, three extra
scripts under `deploy/vps/` cover it:

| Script | Purpose |
|---|---|
| `install_clis.sh` | Installs Node 20 + Claude Code + Codex CLI + Antigravity (`agy`) under a non-root `mmoi` user. Binaries only — you log each CLI in interactively once. |
| `setup_gateway.sh` | Installs Python 3.11 + Caddy, creates a venv for the gateway at `/opt/andy-gateway`, installs the `andy-gateway` systemd unit (uvicorn on loopback) and a Caddy reverse-proxy with **automatic HTTPS**, and opens ufw 80/443. |
| `andy-gateway.service` / `Caddyfile.example` | Templates rendered by `setup_gateway.sh`. |

```bash
# 1. Put the gateway source on the box (its own repo, may be private):
sudo git clone <your-andy-gateway-repo> /opt/andy-gateway   # or scp it up

# 2. Install the CLIs (then log each one in as the mmoi user — see below):
sudo bash deploy/vps/install_clis.sh

# 3. Stand up the gateway behind Caddy TLS (set your domain for a real cert):
sudo GATEWAY_DOMAIN=andy.example.com bash deploy/vps/setup_gateway.sh

# 4. Add secrets, then restart:
sudo -u mmoi nano /opt/andy-gateway/.env   # ANDY_API_KEY + provider keys
sudo systemctl restart andy-gateway
```

Log each CLI in once, interactively, as the `mmoi` user:

```bash
sudo -iu mmoi
claude setup-token   # OAuth token (Pro/Max). Generate ON THIS BOX, never copy one.
codex login          # device-code login -> ~/.codex/auth.json
agy login            # device-code login -> libsecret keyring
```

> **ToS flags (read honestly):** `claude -p` draws from a capped monthly
> Agent-SDK credit on subscription plans from **2026-06-15**; OpenAI does **not**
> officially support headless automation of a personal ChatGPT plan (API key is
> the recommended programmatic path); Antigravity is a **free preview** with no
> SLA. Keep the HTTP-API fallback (provider keys in `.env`) for all three.

The gateway binds **127.0.0.1 only**; Caddy is the sole public entrypoint
(ports 80/443). A full operator walkthrough lives in the owner's
`HOSTINGER_VPS_SETUP_GUIDE.md`.

## Security notes

- The loop runs as the unprivileged `orchestrator` user, not root, with systemd
  hardening (`NoNewPrivileges`, `ProtectSystem=full`, `ProtectHome`, restricted
  `ReadWritePaths`).
- No inbound ports beyond SSH. No web server, no database, no public endpoint.
- Secrets live only in `/opt/orchestration/.env` (mode `600`, gitignored).
- Keep the box patched: `sudo apt update && sudo apt upgrade` (or enable
  `unattended-upgrades`).
- **Nothing in this repo provisions infrastructure or contains credentials.**
