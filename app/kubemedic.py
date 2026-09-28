"""KubeMedic Lite: watches for failing pods and asks a local LLM (Ollama) why.

Each diagnosis is written back to the cluster as a Kubernetes Event on the pod,
so `kubectl describe pod` shows it. Designed to be cheap: logs are condensed
before prompting, and diagnoses are cached by failure fingerprint.
"""

import hashlib
import json
import os
import re
import time
import urllib.request
from datetime import datetime, timezone

from kubernetes import client, config

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://host.k3d.internal:11434")
MODEL = os.getenv("MODEL", "qwen2.5:1.5b")
NAMESPACES = [n.strip() for n in os.getenv("WATCH_NAMESPACES", "chaos").split(",") if n.strip()]
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "20"))
MAX_LOG_LINES = int(os.getenv("MAX_LOG_LINES", "30"))
MAX_PROMPT_CHARS = int(os.getenv("MAX_PROMPT_CHARS", "2500"))

FAILING_WAITING = {"CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull",
                   "CreateContainerConfigError", "InvalidImageName"}
# States that flap between two names; normalize so the fingerprint (and cache) stays stable.
NORMALIZE = {"ErrImagePull": "ImagePullBackOff"}
# Deterministic facts per failure mode: cheap rules do classification, the LLM only explains.
REASON_FACTS = {
    "OOMKilled": "The kernel killed the container for using more memory than its limit. "
                 "A fix must set the limit ABOVE the current value, or reduce the memory the app uses.",
    "ImagePullBackOff": "The image reference could not be pulled (tag/repo does not exist or no access). "
                        "A fix must use an image tag that actually exists.",
    "CrashLoopBackOff": "The process starts and exits non-zero repeatedly; the logs show why.",
    "CreateContainerConfigError": "A referenced ConfigMap/Secret or key is missing.",
}
ANNOTATION = "kubemedic.io/signature"
ERROR_HINTS = re.compile(r"error|fatal|exception|panic|denied|refused|not found|missing|killed", re.I)

cache = {}  # fingerprint -> diagnosis text
stats = {"diagnosed": 0, "cache_hits": 0, "prompt_tokens": 0, "output_tokens": 0}


def log(event, **fields):
    print(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "event": event, **fields}), flush=True)


def find_failure(pod):
    """Return (container, reason, detail) for the first failing container, else None."""
    for cs in pod.status.container_statuses or []:
        waiting = cs.state.waiting if cs.state else None
        last = cs.last_state.terminated if cs.last_state else None
        if last and last.reason == "OOMKilled":
            return cs.name, "OOMKilled", f"exit code {last.exit_code}"
        crash_looping = last and last.exit_code != 0 and cs.restart_count >= 2
        if (waiting and waiting.reason in FAILING_WAITING) or crash_looping:
            reason = waiting.reason if waiting and waiting.reason in FAILING_WAITING else "CrashLoopBackOff"
            detail = (waiting.message or "") if waiting else ""
            if last:
                detail += f" (last exit code {last.exit_code}, reason {last.reason})"
            return cs.name, NORMALIZE.get(reason, reason), detail.strip()
    return None


def condense_logs(raw):
    """Keep error-looking lines plus the tail, dedupe lines that only differ by numbers."""
    lines = [l for l in raw.splitlines() if l.strip()]
    seen, kept = set(), []
    for line in lines:
        key = re.sub(r"\d+", "#", line)
        if key in seen:
            continue
        seen.add(key)
        kept.append(line)
    important = [l for l in kept if ERROR_HINTS.search(l)]
    tail = kept[-10:]
    merged = list(dict.fromkeys(important[-MAX_LOG_LINES:] + tail))
    return "\n".join(merged)[-1500:]


def gather_context(v1, pod, container, reason, detail):
    logs = ""
    if reason not in {"ImagePullBackOff", "ErrImagePull", "InvalidImageName"}:
        # The previous container's logs are briefly unavailable around a restart; fall back to current.
        for previous in (True, False):
            try:
                logs = v1.read_namespaced_pod_log(pod.metadata.name, pod.metadata.namespace,
                                                  container=container, previous=previous,
                                                  tail_lines=200)
            except client.ApiException:
                logs = ""
            if logs.strip():
                break
    events = v1.list_namespaced_event(pod.metadata.namespace,
                                      field_selector=f"involvedObject.name={pod.metadata.name},type=Warning")
    warnings = sorted({e.message for e in events.items if e.message})[-5:]
    spec = next(c for c in pod.spec.containers if c.name == container)
    limits = (spec.resources.limits or {}) if spec.resources else {}
    return {
        "reason": reason,
        "known_fact": REASON_FACTS.get(reason, ""),
        "detail": detail,
        "image": spec.image,
        "command": " ".join((spec.command or []) + (spec.args or []))[:300],
        "limits": limits,
        "events": warnings,
        "logs": condense_logs(logs),
    }


def fingerprint(ctx):
    # Same pod template (image, command, limits) failing the same way => same diagnosis.
    # Logs are deliberately excluded: around restarts they flap between previous/partial output.
    key = f'{ctx["reason"]}|{ctx["image"]}|{ctx["command"]}|{sorted(ctx["limits"].items())}'
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def ask_llm(ctx):
    prompt = (
        "You are a Kubernetes SRE. In at most 2 sentences: the root cause (trust known_fact), "
        "then one concrete fix grounded in the logs, image, or limits below. No preamble.\n\n"
        + json.dumps(ctx, indent=1)
    )[:MAX_PROMPT_CHARS]
    body = json.dumps({"model": MODEL, "prompt": prompt, "stream": False, "keep_alive": "30m",
                       "options": {"temperature": 0.2, "num_predict": 120}}).encode()
    req = urllib.request.Request(f"{OLLAMA_URL}/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        out = json.load(resp)
    stats["prompt_tokens"] += out.get("prompt_eval_count", 0)
    stats["output_tokens"] += out.get("eval_count", 0)
    return out["response"].strip(), out.get("total_duration", 0) / 1e9


def record(v1, pod, container, reason, sig, diagnosis):
    now = datetime.now(timezone.utc)
    v1.create_namespaced_event(pod.metadata.namespace, client.CoreV1Event(
        metadata=client.V1ObjectMeta(generate_name=f"{pod.metadata.name}-kubemedic-"),
        involved_object=client.V1ObjectReference(kind="Pod", name=pod.metadata.name,
                                                 namespace=pod.metadata.namespace,
                                                 uid=pod.metadata.uid),
        reason="KubeMedicDiagnosis", type="Warning",
        message=f"[{container}/{reason}] {diagnosis}"[:1000],
        first_timestamp=now, last_timestamp=now, count=1,
        source=client.V1EventSource(component="kubemedic")))
    v1.patch_namespaced_pod(pod.metadata.name, pod.metadata.namespace,
                            {"metadata": {"annotations": {ANNOTATION: sig}}})


def scan(v1):
    for ns in NAMESPACES:
        for pod in v1.list_namespaced_pod(ns).items:
            failure = find_failure(pod)
            if not failure:
                continue
            container, reason, detail = failure
            ctx = gather_context(v1, pod, container, reason, detail)
            if reason in {"CrashLoopBackOff", "OOMKilled"} and not ctx["logs"]:
                continue  # no evidence yet (mid-restart); retry next poll rather than guess
            sig = fingerprint(ctx)
            if (pod.metadata.annotations or {}).get(ANNOTATION) == sig:
                continue  # already diagnosed this pod for this failure
            if sig in cache:
                stats["cache_hits"] += 1
                diagnosis, secs = cache[sig], 0.0
            else:
                diagnosis, secs = ask_llm(ctx)
                cache[sig] = diagnosis
                stats["diagnosed"] += 1
            record(v1, pod, container, reason, sig, diagnosis)
            log("diagnosis", pod=f"{ns}/{pod.metadata.name}", reason=reason,
                cached=secs == 0.0, llm_seconds=round(secs, 1), **stats)


def main():
    config.load_incluster_config()
    v1 = client.CoreV1Api()
    log("start", model=MODEL, ollama=OLLAMA_URL, namespaces=NAMESPACES)
    while True:
        try:
            scan(v1)
        except Exception as e:  # keep watching; surface the error in logs
            log("error", error=f"{type(e).__name__}: {e}"[:300])
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
