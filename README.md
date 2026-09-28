# KubeMedic Lite

An AI incident-triage service for Kubernetes that runs entirely on a laptop for free. It watches for failing pods, asks a **local LLM** for the root cause and a fix, and writes the answer back into the cluster, so `kubectl describe pod` shows it.

```
┌──────────────── k3d cluster (Colima VM) ────────────────┐
│  ns: chaos                     ns: kubemedic            │
│  checkout-api  CrashLoop  ◀──┐  ┌──────────────────┐    │
│  report-worker OOMKilled  ◀──┼──│ kubemedic (py)   │────┼──▶ Ollama on the Mac
│  web-frontend  ImagePull  ◀──┘  │ poll → condense  │    │    qwen2.5:1.5b
│        ▲                        │ → cache → LLM    │    │    (host.k3d.internal)
│        └── Event: KubeMedicDiagnosis ◀─────────────┘    │
└─────────────────────────────────────────────────────────┘
```

## Example output
```
$ make diagnoses
checkout-api-…   [api/CrashLoopBackOff] …logs show "FATAL: required env var DATABASE_URL is not set".
                 Fix: set DATABASE_URL in the container's environment…
report-worker-…  [worker/OOMKilled] The container exceeded its memory limit… set it to 128Mi.
web-frontend-…   [web/ImagePullBackOff] The image "nginx:1.99.99-typo" does not exist… use a tag that exists.
```

## Quickstart
Needs Colima, k3d, kubectl, Ollama and make. No cloud accounts or paid APIs.
```bash
make demo        # start the cluster, pull the model, build, deploy KubeMedic and the broken workloads
make status      # compact pod table
make diagnoses   # KubeMedic's conclusions
make logs        # JSON log lines with LLM latency, token counts and cache hits
make reset       # clear diagnoses and run again
make down        # delete the cluster
```

## Design and optimizations
| Concern | What it does |
|---|---|
| **Token budget** | Logs are condensed first: repeated lines are removed, error lines plus the tail are kept, and the total is capped at 1.5 KB. The prompt is capped at 2.5 KB and the output at 120 tokens. A typical diagnosis is about 350 prompt tokens and takes 2–5 s on a laptop CPU. |
| **Caching** | Diagnoses are cached by fingerprint: failure type plus the pod's image, command and limits. Replicas and reschedules of the same broken workload cost no LLM calls, and `make logs` shows the cache hits. |
| **Stable fingerprints** | Logs are left out of the fingerprint, because around restarts they switch between the previous run's output and partial output. Pods are annotated with their fingerprint, so they're never diagnosed twice. |
| **Rules first, LLM second** | Code classifies the failure and adds a known fact for each failure type. The small model only explains and suggests a fix, which makes its answers noticeably more grounded. |
| **Least privilege** | A namespaced Role that can read pods, logs and events and write events and annotations, only in `chaos`. The container runs non-root, with a read-only root filesystem and all capabilities dropped. |
| **Singleton watcher** | The Deployment uses the `Recreate` strategy, so two watchers never run at once and never write duplicate diagnoses. |
| **Fast local loop** | The image is built on the host and loaded with `k3d image import` (no registry). A full rebuild and redeploy takes about 40 s. The model runs natively on the host, avoiding a 3 GB container image. |

## Built with Claude Code
This repo is set up so Claude Code works well in it:
- **`CLAUDE.md`**: a short architecture summary, commands and conventions, so each session starts with context instead of exploring.
- **`/k8s-debug` Skill** (`.claude/skills/k8s-debug`): a triage checklist that keeps output small (`make status`, custom-columns, `--previous --tail`) and compares its findings with KubeMedic's.
- **PostToolUse hook** (`.claude/hooks/validate-k8s.sh`): every YAML edit Claude makes is checked with **kubeconform** (schema) and **kube-linter** (security and best practice). It stays silent on success and returns errors so Claude fixes them. It caught a YAML quoting bug during development. `make check` runs the same checks by hand.
- **Quiet Makefile**: each target prints one line on success, which keeps the agent's context small.

## Stack
k3d (k3s) on Colima · Python 3.12 + the official `kubernetes` client · Ollama with `qwen2.5:1.5b` · kubeconform · kube-linter
