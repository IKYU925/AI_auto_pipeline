#!/usr/bin/env bash
# usage: sample_top.sh <out.csv> <seconds> [interval]
set -euo pipefail
out="$1"; dur="$2"; step="${3:-30}"
export KUBECONFIG="$HOME/project/infra_local_set/kubeconfig"
[ -f "$out" ] || echo "ts,kind,ns,name,cpu_m,mem_mi" > "$out"
end=$(( $(date +%s) + dur ))
while [ "$(date +%s)" -lt "$end" ]; do
  ts=$(date +%s)
  kubectl top nodes --no-headers | awk -v ts="$ts" '{gsub("m","",$2); gsub("Mi","",$4); print ts",node,-,"$1","$2","$4}' >> "$out"
  kubectl top pods -A --no-headers | awk -v ts="$ts" '{gsub("m","",$3); gsub("Mi","",$4); print ts",pod,"$1","$2","$3","$4}' >> "$out"
  sleep "$step"
done
