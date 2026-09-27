"""
Resilient worker microservice.

Demonstrates production reliability patterns that keep a service healthy through
scaling and deployment events:

* Redis distributed lock (fencing-token style) so only one worker processes a
  given job key at a time — the pattern used to eliminate duplicate work and
  lock-timeout failures during scale-out.
* Separate liveness / readiness / startup probes so Kubernetes only routes
  traffic to genuinely ready pods and restarts only genuinely dead ones.
* Graceful shutdown: on SIGTERM the process stops accepting new work, finishes
  in-flight requests, releases held locks, and closes the Redis pool — avoiding
  connection-exhaustion and half-done work during rolling deploys.
"""

import os
import signal
import threading
import time
import uuid
from contextlib import contextmanager

import redis
from flask import Flask, jsonify

APP = Flask(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
LOCK_TTL_MS = int(os.environ.get("LOCK_TTL_MS", "10000"))

# Bounded connection pool prevents connection exhaustion under load / failover.
_pool = redis.ConnectionPool.from_url(REDIS_URL, max_connections=20, socket_timeout=2)
_redis = redis.Redis(connection_pool=_pool)

# Shared state for health + graceful shutdown.
_state = {
    "ready": False,
    "shutting_down": False,
    "inflight": 0,
}
_state_lock = threading.Lock()

# Lua script: release the lock only if we still own it (compare fencing token).
_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""


@contextmanager
def distributed_lock(key: str, ttl_ms: int = LOCK_TTL_MS):
    """Acquire a Redis distributed lock with a unique fencing token.

    Yields True if the lock was acquired, False otherwise. Releases safely so a
    lock that already expired and was re-acquired by another worker is never
    deleted out from under that worker.
    """
    token = str(uuid.uuid4())
    lock_key = f"lock:{key}"
    acquired = _redis.set(lock_key, token, nx=True, px=ttl_ms)
    try:
        yield bool(acquired)
    finally:
        if acquired:
            try:
                _redis.eval(_RELEASE_LUA, 1, lock_key, token)
            except redis.RedisError:
                # Lock will expire via TTL even if release fails.
                pass


def _do_work(job_key: str) -> dict:
    with _state_lock:
        _state["inflight"] += 1
    try:
        with distributed_lock(job_key) as have_lock:
            if not have_lock:
                return {"job": job_key, "status": "skipped", "reason": "lock held by another worker"}
            # Simulate idempotent unit of work.
            time.sleep(0.2)
            return {"job": job_key, "status": "processed", "worker": os.environ.get("HOSTNAME", "local")}
    finally:
        with _state_lock:
            _state["inflight"] -= 1


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------
@APP.route("/healthz")
def healthz():
    """Liveness: process is up. Never fails due to downstream deps."""
    return jsonify(status="alive"), 200


@APP.route("/readyz")
def readyz():
    """Readiness: only serve traffic when ready and not shutting down, and Redis reachable."""
    with _state_lock:
        if _state["shutting_down"] or not _state["ready"]:
            return jsonify(status="not-ready"), 503
    try:
        _redis.ping()
    except redis.RedisError:
        return jsonify(status="redis-unavailable"), 503
    return jsonify(status="ready"), 200


@APP.route("/startupz")
def startupz():
    """Startup probe: gate liveness until initial warm-up completes."""
    with _state_lock:
        return (jsonify(status="started"), 200) if _state["ready"] else (jsonify(status="starting"), 503)


@APP.route("/work/<job_key>", methods=["POST"])
def work(job_key: str):
    with _state_lock:
        if _state["shutting_down"]:
            return jsonify(status="draining"), 503
    return jsonify(_do_work(job_key)), 200


# ---------------------------------------------------------------------------
# Lifecycle: warm-up + graceful shutdown
# ---------------------------------------------------------------------------
def _warmup():
    # Simulate cache priming / dependency checks before declaring readiness.
    time.sleep(2)
    with _state_lock:
        _state["ready"] = True


def _graceful_shutdown(signum, _frame):
    """On SIGTERM: stop accepting work, drain in-flight, close pool."""
    with _state_lock:
        _state["shutting_down"] = True
        _state["ready"] = False

    # Wait for in-flight work to finish (bounded).
    deadline = time.time() + 25
    while time.time() < deadline:
        with _state_lock:
            if _state["inflight"] == 0:
                break
        time.sleep(0.2)

    try:
        _pool.disconnect()
    except Exception as exc:  # noqa: BLE001
        print(f"error disconnecting Redis pool during shutdown: {exc}")
    os._exit(0)


def main():
    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT, _graceful_shutdown)
    threading.Thread(target=_warmup, daemon=True).start()
    port = int(os.environ.get("PORT", "8080"))
    APP.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
