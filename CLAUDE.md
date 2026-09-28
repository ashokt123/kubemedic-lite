# KubeMedic Lite

A Python service in k3d watches the `chaos` namespace for failing pods, asks a local LLM (Ollama on the Mac host, `qwen2.5:1.5b`) for a root cause + fix, and writes it back as a `KubeMedicDiagnosis` Event on the pod.

## Layout
- `app/kubemedic.py` — the whole service (poll → detect → condense logs → fingerprint/cache → Ollama → Event + annotation)
- `deploy/kubemedic.yaml` — namespace, SA, Role (scoped to `chaos`), ConfigMap, Deployment
- `chaos/chaos.yaml` — intentionally broken workloads (CrashLoop, OOMKilled, ImagePull). Don't "fix" them unless asked.
- `.claude/` — `/k8s-debug` skill; PostToolUse hook validates YAML with kubeconform + kube-linter

## Commands (quiet by default — prefer these over raw kubectl)
`make demo` (everything) · `make status` · `make diagnoses` · `make logs` · `make check` · `make reset` · `make down`

## Environment
- Intel Mac; Colima (2 CPU / 4 GB) → k3d cluster `kubemedic`. kubectl/docker/ollama live in `~/.local/bin`.
- Ollama runs on the Mac bound to localhost; pods reach it at `http://host.k3d.internal:11434`.
- Images are loaded with `k3d image import` (no registry); `imagePullPolicy: Never`.

## Conventions
- Every container: requests+limits, non-root, read-only root FS, no privilege escalation.
- Keep command output small: `--tail`, `-o custom-columns`, avoid `describe`/`-o yaml`.
- Before any long-running install/build: estimate time and tell the user first.
