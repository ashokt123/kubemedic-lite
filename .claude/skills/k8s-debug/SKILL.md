---
name: k8s-debug
description: Triage a failing Kubernetes workload in this cluster (CrashLoopBackOff, OOMKilled, ImagePullBackOff, Pending, probe failures). Use when a pod is unhealthy, the user asks why something is broken, or to compare against KubeMedic's diagnosis.
---

# Kubernetes failure triage

Goal: find the root cause with the **least output possible**, then propose (and, if asked, apply) the fix.

## 1. Overview first — never dump raw YAML
```bash
make status          # compact table: waiting reason, last termination, restarts
make diagnoses       # what KubeMedic already concluded
```

## 2. Drill into ONE pod, narrowly
```bash
kubectl get events -n <ns> --field-selector involvedObject.name=<pod>,type=Warning -o custom-columns=REASON:.reason,MSG:.message
kubectl logs -n <ns> <pod> --previous --tail=30      # crashed container's last run
kubectl get pod -n <ns> <pod> -o jsonpath='{.spec.containers[*].resources}{"\n"}{.spec.containers[*].image}{"\n"}'
```
Avoid `kubectl describe` and `-o yaml` unless the above is inconclusive — they are long.

## 3. Map symptom → likely cause
| Symptom | Check | Typical fix |
|---|---|---|
| CrashLoopBackOff | `logs --previous` | missing env/config, bad command, failing dependency |
| OOMKilled (lastState) | memory limit vs. app need | raise limit or reduce memory use |
| ImagePullBackOff / ErrImagePull | image tag in events | fix tag/registry/pull secret |
| Pending | events: Insufficient cpu/memory, unbound PVC | lower requests / fix PVC |
| CreateContainerConfigError | events | missing ConfigMap/Secret key |

## 4. Report
One line per pod: `pod — root cause — fix`. Say whether you agree with KubeMedic's diagnosis and why.

## 5. Fixing
Edit the manifest in `chaos/` or `deploy/` (the validation hook runs automatically), then `kubectl apply -f <file>`. Only fix `chaos/` workloads when the user asks — they are broken on purpose.
