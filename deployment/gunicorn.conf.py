# ──────────────────────────────────────────────────────────────────────────────
# Gunicorn — Production Configuration
# WiFi Hotspot API
# ──────────────────────────────────────────────────────────────────────────────

import multiprocessing
import os

# ─── Server Socket ───────────────────────────────────────────────────────────
bind = "127.0.0.1:8000"
backlog = 2048

# ─── Workers ─────────────────────────────────────────────────────────────────
# Formula: (2 x CPU) + 1 — for ARM 2 OCPUs = 5 workers
workers = int(os.environ.get("GUNICORN_WORKERS", multiprocessing.cpu_count() * 2 + 1))
worker_class = "gthread"
threads = 4
worker_connections = 1000
timeout = 30
keepalive = 5

# ─── Restart workers periodically to prevent memory leaks ────────────────────
max_requests = 2000
max_requests_jitter = 200

# ─── Logging ─────────────────────────────────────────────────────────────────
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info").lower()
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# ─── Process Naming ──────────────────────────────────────────────────────────
proc_name = "wifi-hotspot"

# ─── Security ────────────────────────────────────────────────────────────────
limit_request_line = 8190
limit_request_fields = 100
limit_request_field_size = 8190

# ─── Server Mechanics ────────────────────────────────────────────────────────
preload_app = True
daemon = False
tmp_upload_dir = None

# ─── Graceful timeout ────────────────────────────────────────────────────────
graceful_timeout = 30

def on_starting(server):
    """Called just before the master process is initialized."""
    pass

def post_fork(server, worker):
    """Called just after a worker has been forked."""
    server.log.info("Worker spawned (pid: %s)", worker.pid)

def pre_exec(server):
    """Called just before a new master process is forked."""
    server.log.info("Forked child, re-executing.")

def when_ready(server):
    """Called just after the server is started."""
    server.log.info("WiFi Hotspot API server is ready. PID: %s", os.getpid())
