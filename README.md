
# Faltasi Wealth – DevOps & Deployment Documentation

## Environment Overview

| Component | Details |
| :--- | :--- |
| **Domain** | `faltasi.wapangaji.com` |
| **Server IP** | `140.99.254.193` |
| **App Stack** | FastAPI + Postgres + Redis (Docker) |
| **CI/CD** | Woodpecker CI + SSH deploy |
| **Reverse Proxy** | Nginx (+ Cloudflare in front) |
| **Database Migrations** | Alembic |

---

## 1. DNS & Cloudflare Setup

### 1.1. Cloudflare DNS
Log in to Cloudflare for the domain `wapangaji.com` and add an **A record**:

* **Name:** `faltasi`
* **Type:** `A`
* **IPv4 address:** `140.99.254.193`
* **Proxy status:** Proxied (orange cloud) – Cloudflare will sit in front.
* **TTL:** Auto

**Verification:**
Ensure the subdomain resolves correctly. You should see a Cloudflare IP (e.g., `172.67.x.x`), not your server IP.
```bash
ping faltasi.wapangaji.com
````

### 1.2. Cloudflare SSL Mode

Navigate to **Cloudflare → SSL/TLS**:

  * Set **SSL mode** to **Full (strict)** (Recommended).
  * *Flow:* Client → Cloudflare (HTTPS) → Origin Server (HTTPS with valid cert).

-----

## 2\. SSL Certificates on the Server

We use a certificate/key pair for `faltasi.wapangaji.com`.

### 2.1. File Locations

Place the files on the server (via `scp` or `sftp`):

  * **Public Cert:** `/etc/ssl/certs/faltasi_wapangaji_com.crt`
  * **Private Key:** `/etc/ssl/private/faltasi_wapangaji_com.key`

### 2.2. Permissions

Secure the keys so only root can read the private key.

```bash
sudo chown root:root /etc/ssl/certs/faltasi_wapangaji_com.crt
sudo chown root:root /etc/ssl/private/faltasi_wapangaji_com.key
sudo chmod 644 /etc/ssl/certs/faltasi_wapangaji_com.crt
sudo chmod 600 /etc/ssl/private/faltasi_wapangaji_com.key
```

-----

## 3\. Nginx Reverse Proxy Setup

### 3.1. Install Nginx

```bash
sudo apt update
sudo apt install -y nginx
sudo systemctl status nginx
```

### 3.2. Nginx Site Config

Create the configuration file:

```bash
sudo nano /etc/nginx/sites-available/faltasi.wapangaji.com
```

**Configuration Content:**

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name faltasi.wapangaji.com;

    # Redirect all HTTP to HTTPS
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;

    server_name faltasi.wapangaji.com;

    # SSL cert + key
    ssl_certificate     /etc/ssl/certs/faltasi_wapangaji_com.crt;
    ssl_certificate_key /etc/ssl/private/faltasi_wapangaji_com.key;

    # Security headers
    add_header X-Frame-Options DENY;
    add_header X-Content-Type-Options nosniff;

    # Reverse proxy to FastAPI backend running on localhost:8000 (Docker)
    location / {
        proxy_pass [http://127.0.0.1:8000](http://127.0.0.1:8000);

        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### 3.3. Enable Site & Reload

```bash
# Enable site
sudo ln -s /etc/nginx/sites-available/faltasi.wapangaji.com \
           /etc/nginx/sites-enabled/faltasi.wapangaji.com

# Test config
sudo nginx -t

# Reload Nginx
sudo systemctl reload nginx
```

*Now `https://faltasi.wapangaji.com` will proxy to `127.0.0.1:8000`.*

-----

## 4\. Application Stack – Docker Setup

  * **Directory:** `/srv/faltasi-wealth`
  * **Repository:** `https://github.com/obmsuya/Backend-Faltasi-Wealth-`

### 4.1. docker-compose.yml

Located at `/srv/faltasi-wealth/docker-compose.yml`:

```yaml
services:
  db:
    image: postgres:15
    restart: always
    environment:
      POSTGRES_USER: faltasi
      POSTGRES_PASSWORD: faltasi
      POSTGRES_DB: faltasi_wealth
    ports:
      - "5432:5432"
    volumes:
      - db_data:/var/lib/postgresql/data

  redis:
    image: redis:7.4
    restart: always
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data

  backend:
    build:
      context: .
      dockerfile: Dockerfile
    restart: always
    depends_on:
      - db
      - redis
    env_file:
      - .env
    environment:
      DATABASE_URL: postgresql://faltasi:faltasi@db:5432/faltasi_wealth
      SECRET_KEY: <your_app_secret_key>
      REDIS_URL: redis://redis:6379/0
    ports:
      - "8000:8000"
    volumes:
      - .:/app

volumes:
  db_data:
  redis_data:
```

### 4.2. Dockerfile

Located at `/srv/faltasi-wealth/Dockerfile`:

```dockerfile
FROM tiangolo/uvicorn-gunicorn-fastapi:python3.11

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# App will be served via uvicorn, proxied by Nginx
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 4.3. Manual Startup

```bash
cd /srv/faltasi-wealth
docker compose up -d --build
docker ps
```

**Expected Output:** Containers for `backend`, `db`, and `redis` are healthy.

-----

## 5\. Alembic Migrations (Database Schema)

Faltasi Wealth uses **SQLAlchemy + Alembic**.

### 5.1. Database Config (`app/database.py`)

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
import os

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    DATABASE_URL = "postgresql://faltasi:faltasi@localhost:5432/faltasi_wealth"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

### 5.2. Alembic Config (`alembic.ini`)

Ensure the `sqlalchemy.url` points to the docker service name `db`:

```ini
[alembic]
script_location = alembic
prepend_sys_path = .
sqlalchemy.url = postgresql://faltasi:faltasi@db:5432/faltasi_wealth
```

### 5.3. Migration Workflow

**Local (Development):**
Generate and push migrations.

```bash
alembic revision --autogenerate -m "description"
alembic upgrade head
git add alembic/versions/*.py
git commit -m "Add new migration"
git push origin main
```

**Server (Production):**
The server **only applies** existing migrations.

```bash
docker compose exec backend alembic upgrade head
```

**Check Status:**

```bash
# Check current revision
docker compose exec backend alembic current

# Inspect tables via PSQL
docker compose exec db psql -U faltasi -d faltasi_wealth -c '\dt'
```

-----

## 6\. Woodpecker CI/CD Setup

Woodpecker runs in a separate stack at `/srv/woodpecker`.

### 6.1. Woodpecker `docker-compose.yml`

```yaml
services:
  woodpecker-server:
    image: woodpeckerci/woodpecker-server:latest
    container_name: woodpecker-server
    restart: unless-stopped
    ports:
      - "8001:8000"
    volumes:
      - woodpecker-server-data:/var/lib/woodpecker
    environment:
      WOODPECKER_OPEN: "true"
      WOODPECKER_HOST: "[http://140.99.254.193:8001](http://140.99.254.193:8001)"
      WOODPECKER_SERVER_ADDR: ":8000"
      WOODPECKER_GITHUB: "true"
      WOODPECKER_GITHUB_CLIENT: "<YOUR_GITHUB_CLIENT_ID>"
      WOODPECKER_GITHUB_SECRET: "<YOUR_GITHUB_CLIENT_SECRET>"
      WOODPECKER_GITHUB_SCOPE: "repo,admin:repo_hook,user:email"
      WOODPECKER_AGENT_SECRET: "<YOUR_AGENT_SECRET>"

  woodpecker-agent:
    image: woodpeckerci/woodpecker-agent:latest
    container_name: woodpecker-agent
    restart: unless-stopped
    depends_on:
      - woodpecker-server
    privileged: true
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    environment:
      WOODPECKER_SERVER: "woodpecker-server:9000"
      WOODPECKER_AGENT_SECRET: "<YOUR_AGENT_SECRET>"

volumes:
  woodpecker-server-data:
```

-----

## 7\. SSH Deploy Key

Woodpecker uses `appleboy/drone-ssh` to deploy.

1.  **Generate Key on Server:**
    ```bash
    ssh-keygen -t ed25519 -C "woodpecker-deploy"
    # Press enter for default path and NO passphrase
    ```
2.  **Authorize Key:**
    ```bash
    cat ~/.ssh/id_ed25519.pub >> ~/.ssh/authorized_keys
    ```
3.  **Get Private Key:**
    ```bash
    cat ~/.ssh/id_ed25519
    ```
    *Copy the output. This goes into your `.woodpecker.yml`.*

-----

## 8\. CI/CD Pipeline Configuration

File: `.woodpecker.yml` (in the root of the backend repo).

```yaml
when:
  - event: push
    branch: main

steps:
  - name: deploy
    image: appleboy/drone-ssh
    settings:
      host: "140.99.254.193"
      username: "root"
      port: 22
      # PASTE THE PRIVATE KEY CONTENT BELOW
      key: |
        -----BEGIN OPENSSH PRIVATE KEY-----
        <YOUR_ID_ED25519_PRIVATE_KEY_CONTENT>
        -----END OPENSSH PRIVATE KEY-----
      script:
        - cd /srv/faltasi-wealth
        - git pull origin main
        - docker compose down || true
        - docker compose up -d --build --remove-orphans
        - docker compose exec backend alembic upgrade head
```

-----

## 9\. End-to-End Summary

1.  **Server:** Install Docker/Nginx, setup SSL permissions.
2.  **DNS:** Point Cloudflare A record to `140.99.254.193` (Proxied).
3.  **Nginx:** Configure Reverse Proxy (Port 443 → 127.0.0.1:8000).
4.  **App:** Clone repo to `/srv/faltasi-wealth`, add `.env`, run `docker compose up`.
5.  **CI/CD:** Setup Woodpecker, generate SSH keys, and commit `.woodpecker.yml`.
6.  **Deploy:** Push to `main` → Woodpecker pulls code, rebuilds containers, and migrates DB automatically.

<!-- end list -->

