# Self-Hosted Recipe Book

A self-hosted family recipe book running on your home Kubernetes cluster.

## Features

- 📸 **Captures every recipe at add-time** — full-page PDF + screenshot via headless Chromium. Recipe websites die; your copies don't.
- 🤖 **LLM extraction** (Anthropic + OpenAI in parallel) — used **once** when adding a recipe, never again.
- 🔍 Full-text search across all fields
- 🏷️ 12 recipe categories
- 📤 **Share without account** — generates a tokenized URL (`example.com/share/<token>`) that anyone can open. Login is never exposed externally.
- 🖼️ Separate user-uploaded photo per recipe (independent of the captured screenshot)
- 🔒 Shared-password auth with signed cookie sessions
- 📱 Hebrew RTL, mobile-friendly

## Architecture

```
nginx-ingress
├── home.example.com → full app (LAN only)
└── example.com      → /share/* only (internet-facing)

recipe-book pod
├── FastAPI (Python) — backend + API
├── Playwright/Chromium — capture service (in-process)
├── Vite/React SPA — served as static files from FastAPI
└── PVC /data
    ├── images/          user-uploaded photos
    ├── captures/<id>/   page.pdf + page.jpg per recipe
    └── sessions/<id>/   temp captures during add flow (auto-cleaned)

MariaDB — your existing instance
```

## Prerequisites

- Kubernetes cluster with `nginx-ingress` controller
- Your existing MariaDB instance accessible from the cluster
- A container registry reachable from your cluster (or `imagePullPolicy: IfNotPresent` with a local image)
- DNS:
  - `home.example.com` → your k8s node/LB IP (internal DNS, e.g. Pi-hole/AdGuard)
  - `example.com` → your public IP (router port-forward to the k8s node/LB)
- At least one LLM API key: `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`

## Quick Start

### 1. Create the database

```sql
CREATE DATABASE recipes CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'recipes'@'%' IDENTIFIED BY 'your-db-password';
GRANT ALL PRIVILEGES ON recipes.* TO 'recipes'@'%';
FLUSH PRIVILEGES;
```

### 2. Build and push the image

```bash
# Build
docker build -t recipe-book:1.0.0 .

# Tag and push to your local registry (adjust URL)
docker tag recipe-book:1.0.0 registry.home.example.com/recipe-book:1.0.0
docker push registry.home.example.com/recipe-book:1.0.0
```

Update `k8s/deployment.yaml` → `image:` with your registry path.

### 3. Create the Secret

```bash
cp .env.example .env
# Edit .env — fill in DATABASE_URL, APP_PASSWORD, SESSION_SECRET, API keys

kubectl create secret generic recipe-book-secret \
  --from-env-file=.env \
  -n default
```

Generate a proper session secret:
```bash
openssl rand -hex 32
```

### 4. Apply manifests

```bash
kubectl apply -f k8s/pvc.yaml
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/ingress.yaml
```

### 5. Verify

```bash
kubectl get pods -n default -l app=recipe-book
kubectl logs -n default -l app=recipe-book --tail=50
```

Visit `http://home.example.com` (or `https://` if you set up TLS). You should see the login page with the 15 seed recipes already loaded.

## Local Development

```bash
# Requires ANTHROPIC_API_KEY or OPENAI_API_KEY in shell environment
docker compose up --build
```

Frontend dev server with hot reload:
```bash
cd frontend
npm install
npm run dev        # proxies /api to localhost:8000
```

Backend only (with venv):
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
DATABASE_URL="mariadb+pymysql://recipes:recipes@localhost:3306/recipes" \
  APP_PASSWORD=dev SESSION_SECRET=dev SECURE_COOKIES=False STATIC_DIR=../frontend/dist \
  uvicorn main:app --reload
```

## Updating

```bash
docker build -t recipe-book:1.1.0 .
docker push registry.home.example.com/recipe-book:1.1.0

# Update image in deployment.yaml, then:
kubectl apply -f k8s/deployment.yaml
kubectl rollout status deployment/recipe-book -n default
```

Because the strategy is `Recreate`, the pod is stopped before the new one starts. Downtime is ~15 seconds. For a family recipe book that's fine.

## Backup

The only things to back up:

| What | Where | How |
|------|-------|-----|
| Structured recipe data | MariaDB `recipes` database | `mysqldump -u recipes -p recipes > backup.sql` |
| Captures (PDF + screenshots) | PVC → `/data/captures/` | `kubectl cp default/<pod>:/data /local/backup` |
| User photos | PVC → `/data/images/` | same |

MariaDB backup can be automated with a CronJob. The captures are append-only and can be re-generated with the "re-capture" button on any recipe that still has a live URL.

## Re-capturing old recipes

The seed recipes from the WhatsApp chat were added without captures (only structured data for פאי רועים which came from a photo). To capture any recipe retroactively:

1. Open the recipe → if it has a URL, a refresh button appears in the tab strip
2. Click it — the backend opens the URL in Chromium, saves PDF + screenshot, and links them to the recipe

## Sharing

When you click 🔗 **Share** on a recipe:
- A permanent token is generated and stored in the DB
- `https://example.com/share/<token>` is copied to your clipboard
- That URL shows the recipe with tabs for structured view, screenshot, and PDF download
- The recipient needs no account and no LAN access
- To revoke: go back into the recipe and click "בטל שיתוף" (not yet in the UI — you can call `DELETE /api/recipes/{id}/share` directly, or add a button in Edit)

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | — | MariaDB connection string |
| `APP_PASSWORD` | `changeme` | Shared login password |
| `SESSION_SECRET` | — | Random 32-byte hex string for signing cookies |
| `SECURE_COOKIES` | `True` | Set `False` for plain HTTP dev |
| `SHARE_BASE_URL` | `https://example.com` | Base URL prepended to share tokens |
| `ANTHROPIC_API_KEY` | — | Enables Claude for extraction |
| `ANTHROPIC_MODEL` | `claude-haiku-4-5-20251001` | Model to use |
| `OPENAI_API_KEY` | — | Enables GPT for extraction |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model to use |
| `CAPTURE_TIMEOUT_SECONDS` | `60` | Max time for Playwright page load |
| `LLM_TIMEOUT_SECONDS` | `90` | Max time for LLM response |
| `MAX_IMAGE_SIZE_MB` | `8` | Max user-uploaded image size |
| `DATA_DIR` | `/data` | Root for captures, images, sessions |

## Notes on Playwright in k8s

- The container runs as root (required by Chromium's sandbox). This is typical for headless browser containers. If your cluster's PodSecurityPolicy or security context prohibits root, add `--no-sandbox` is already in the args but you may also need to set `securityContext.runAsUser: 0` explicitly.
- `replicas: 1` is required — the Playwright browser instance is held in a process-local singleton. Multiple replicas would each have their own browser, which is fine functionally, but the `ReadWriteOnce` PVC would block the second pod. If you need HA, switch to `ReadWriteMany` (NFS/Longhorn) and multiple replicas.
- Memory limit is set to `1.5Gi`. Chromium rendering a complex recipe page can spike to ~300MB. If you see OOMKilled pods, increase to `2Gi`.
