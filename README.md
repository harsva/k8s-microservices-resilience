# k8s-microservices-resilience

A sample microservice and its Kubernetes manifests demonstrating the reliability
patterns that keep a service healthy through scaling and deployment events —
the kind of work that eliminates connection-exhaustion, duplicate processing,
and lock-timeout failures in production.

## Resilience patterns demonstrated

| Pattern                     | Where                                                        |
| --------------------------- | ------------------------------------------------------------ |
| **Redis distributed lock**  | `app/worker.py` — fencing-token lock with safe Lua release   |
| **Bounded connection pool** | `app/worker.py` — prevents Redis connection exhaustion       |
| **Graceful shutdown**       | SIGTERM → stop intake, drain in-flight, release locks, close pool |
| **Startup/readiness/liveness probes** | distinct probes so traffic only hits ready pods    |
| **Zero-downtime rolling deploy** | `maxUnavailable: 0`, `maxSurge: 1`, `preStop` drain     |
| **HPA + PDB**               | scale on CPU; keep ≥2 pods during voluntary disruptions      |
| **Rollout / rollback**      | see `docs/ROLLOUT.md`                                        |

## The distributed lock

Only one worker processes a given job key at a time. The lock uses a unique
fencing token and a Lua compare-and-delete on release, so a lock that expired
and was re-acquired by another worker is never deleted out from under it — the
correct way to avoid the classic "released someone else's lock" race.

## Graceful shutdown flow

```
SIGTERM ──▶ readiness flips to 503  (Service stops routing new traffic)
        ──▶ preStop sleep lets endpoint removal propagate
        ──▶ drain in-flight requests (bounded wait)
        ──▶ release held locks + disconnect Redis pool
        ──▶ exit 0   (before terminationGracePeriodSeconds SIGKILL)
```

## Layout

```
app/                      Flask worker: distributed lock + graceful shutdown + probes
  worker.py
  Dockerfile              non-root image
  requirements.txt
k8s/base/                 Redis, worker Deployment/Service, HPA, PDB
k8s/overlays/prod/        prod replica counts + pinned image tag
docs/ROLLOUT.md           rollout / rollback runbook
```

## Build & deploy

```bash
# Build and push the worker image (CI does this on release).
docker build -t ghcr.io/harsva/resilience-worker:v1.0.0 app/
docker push ghcr.io/harsva/resilience-worker:v1.0.0

# Deploy the prod overlay.
kubectl apply -k k8s/overlays/prod

# Drive some work through the distributed lock.
kubectl -n resilience-demo port-forward svc/worker 8080:80
curl -XPOST localhost:8080/work/order-123
```

## Validation

CI lints the Python app, builds the container image, renders both Kustomize
overlays, and validates the manifests with `kubeconform` (strict). See
`.github/workflows/validate.yml`.
