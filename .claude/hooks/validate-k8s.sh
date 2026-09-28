#!/usr/bin/env bash
# PostToolUse hook: validate Kubernetes YAML that Claude just edited.
# Silent on success; on failure prints problems to stderr and exits 2 so Claude sees them and fixes them.
# Also usable directly: validate-k8s.sh --all
set -uo pipefail
cd "$(dirname "$0")/../.."

if [ "${1:-}" = "--all" ]; then
  files=$(ls deploy/*.yaml chaos/*.yaml)
else
  file=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))')
  case "$file" in
    */deploy/*.yaml|*/chaos/*.yaml) files="$file" ;;
    *) exit 0 ;;
  esac
fi

out=$(kubeconform -strict -summary -output text $files 2>&1) || { echo "kubeconform failed:" >&2; echo "$out" | grep -v "^Summary" >&2; exit 2; }
out=$(kube-linter lint --config .kube-linter.yaml $files 2>&1) || { echo "kube-linter failed:" >&2; echo "$out" | grep -E "^\S+: \(object:" >&2; exit 2; }
[ "${1:-}" = "--all" ] && echo "manifests valid"
exit 0
