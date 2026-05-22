#!/usr/bin/env bash
# Run all 6 RTTR configs in sequence and write per-run logs under logs/.
set -uo pipefail
cd "$(dirname "$0")"
mkdir -p logs data
CONFIGS=(
  configs/reflexion.yaml
  configs/pvd_standard.yaml
  configs/pvd_min1.yaml
  configs/pvd_self.yaml
  configs/pvd_retry.yaml
  configs/debate.yaml
)
for cfg in "${CONFIGS[@]}"; do
  name=$(basename "$cfg" .yaml)
  out="data/gpqa_results_${name}.json"
  log="logs/rttr_${name}.log"
  echo
  echo "════════════════════════════════════════════════════════════"
  echo "  Running: $cfg  →  $out"
  echo "════════════════════════════════════════════════════════════"
  python3 -m rttr.run --config "$cfg" 2>&1 | tee "$log"
  echo "Done: $cfg  (exit=$?)"
done
echo
echo "All RTTR runs complete."
