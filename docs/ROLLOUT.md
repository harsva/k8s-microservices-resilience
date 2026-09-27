# Rollout / Rollback Runbook

## Deploy a new version

```bash
# CI builds ghcr.io/iharshaavardhan/resilience-worker:<new-tag> and updates the overlay.
kubectl apply -k k8s/overlays/prod
kubectl -n resilience-demo rollout status deployment/worker --timeout=120s
```

Because the Deployment uses `maxUnavailable: 0` and `maxSurge: 1` with readiness
gating, the rollout replaces pods one at a time and never drops below the
desired replica count — a bad image simply fails readiness and the rollout stalls
rather than taking down the service.

## Verify

```bash
kubectl -n resilience-demo get pods -l app.kubernetes.io/name=worker
kubectl -n resilience-demo rollout history deployment/worker
```

## Roll back

If the new version misbehaves (elevated errors, failing readiness, SLO burn):

```bash
# Roll back to the immediately previous revision.
kubectl -n resilience-demo rollout undo deployment/worker

# Or to a specific known-good revision.
kubectl -n resilience-demo rollout undo deployment/worker --to-revision=3

kubectl -n resilience-demo rollout status deployment/worker
```

## Guardrails

- **PodDisruptionBudget** (`minAvailable: 2`) blocks node drains that would take
  the service below two pods.
- **Graceful shutdown** (SIGTERM handling + `preStop`) ensures in-flight work
  finishes and locks are released during both rollout and rollback.
- **HPA** continues to operate across a rollout; new pods inherit the same scaling
  policy.

## Post-change validation (SRE checklist)

1. `rollout status` reports success.
2. Error-budget burn (see `k8s-sre-platform` SLO alerts) is nominal for 15 min.
3. No crash-loop / not-ready alerts.
4. Distributed-lock contention metrics are within normal range.
