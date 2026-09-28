# Quiet by design: targets print one line on success and details only on failure.
CLUSTER := kubemedic
IMAGE   := kubemedic:dev
MODEL   := qwen2.5:1.5b
export PATH := $(HOME)/.local/bin:$(PATH)

.PHONY: up down model build deploy chaos demo status diagnoses logs check reset

up:  ## Start Colima + k3d cluster (idempotent)
	@colima status >/dev/null 2>&1 || colima start --cpu 2 --memory 4 --disk 40
	@k3d cluster list $(CLUSTER) >/dev/null 2>&1 || k3d cluster create $(CLUSTER) --agents 0 --k3s-arg "--disable=traefik@server:0" --wait
	@echo "cluster $(CLUSTER) up"

model:  ## Ensure Ollama is serving on the Mac and the model is pulled
	@curl -sf localhost:11434 >/dev/null || (nohup ollama serve >/tmp/ollama.log 2>&1 & sleep 2)
	@ollama list | grep -q "$(MODEL)" || ollama pull $(MODEL)
	@echo "ollama ready with $(MODEL)"

build:  ## Build the image and load it straight into k3d (no registry)
	@docker build -q -t $(IMAGE) app >/dev/null
	@k3d image import $(IMAGE) -c $(CLUSTER) >/dev/null 2>&1
	@echo "built and imported $(IMAGE)"

chaos:  ## Deploy the broken-on-purpose workloads
	@kubectl apply -f chaos/chaos.yaml >/dev/null && echo "chaos workloads applied"

deploy: chaos build  ## Deploy KubeMedic (needs the chaos namespace for its Role)
	@kubectl apply -f deploy/kubemedic.yaml >/dev/null
	@kubectl -n kubemedic rollout restart deploy/kubemedic >/dev/null
	@kubectl -n kubemedic rollout status deploy/kubemedic --timeout=90s >/dev/null && echo "kubemedic deployed"

demo: up model deploy  ## Everything, end to end
	@echo "watch diagnoses with: make diagnoses"

status:  ## Compact pod overview
	@kubectl get pods -n chaos -o custom-columns='POD:.metadata.name,STATUS:.status.containerStatuses[0].state.waiting.reason,LAST:.status.containerStatuses[0].lastState.terminated.reason,RESTARTS:.status.containerStatuses[0].restartCount'
	@kubectl get pods -n kubemedic --no-headers

diagnoses:  ## Show KubeMedic's diagnoses
	@kubectl get events -n chaos --field-selector reason=KubeMedicDiagnosis -o custom-columns='POD:.involvedObject.name,DIAGNOSIS:.message' | cut -c1-400

logs:  ## KubeMedic logs (JSON lines incl. token + cache stats)
	@kubectl logs -n kubemedic deploy/kubemedic --tail=20

check:  ## Validate manifests (same checks as the Claude Code hook)
	@.claude/hooks/validate-k8s.sh --all

reset:  ## Clear diagnoses so KubeMedic runs again (stop first so it can't re-annotate mid-reset)
	@kubectl -n kubemedic scale deploy/kubemedic --replicas=0 >/dev/null
	@kubectl -n kubemedic wait --for=delete pod -l app=kubemedic --timeout=60s >/dev/null 2>&1 || true
	@kubectl delete events -n chaos --field-selector reason=KubeMedicDiagnosis >/dev/null
	@kubectl annotate pods -n chaos --all kubemedic.io/signature- >/dev/null
	@kubectl -n kubemedic scale deploy/kubemedic --replicas=1 >/dev/null && echo "diagnoses cleared, cache reset"

down:  ## Delete the cluster (Colima keeps running)
	@k3d cluster delete $(CLUSTER) >/dev/null && echo "cluster deleted"
