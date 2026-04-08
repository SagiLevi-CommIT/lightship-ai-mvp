#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# run_tests.sh  –  Lightship MVP E2E Test Runner
#
# Usage:
#   cd tests/
#   bash run_tests.sh [pytest-extra-args]
#
# Required AWS env:
#   AWS credentials in ~/.aws or via env vars (AWS_ACCESS_KEY_ID etc.)
#   AWS_REGION defaults to us-east-1
#
# Optional overrides:
#   ALB_DNS        - override the ALB DNS name
#   AWS_ACCOUNT_ID - override the account ID
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "──────────────────────────────────────────────────────"
echo "  Lightship MVP – E2E Test Suite"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "──────────────────────────────────────────────────────"

# ── 1. Verify AWS credentials ─────────────────────────────────────────────────
echo ""
echo "[1/3] Verifying AWS credentials …"
IDENTITY=$(aws sts get-caller-identity --region "${AWS_REGION:-us-east-1}" 2>&1) || {
    echo "ERROR: AWS credentials not configured or expired."
    echo "       Run: source ~/aws-cli-connect-nomfa.sh"
    exit 1
}
echo "      Account : $(echo "$IDENTITY" | python3 -c "import sys,json; print(json.load(sys.stdin)['Account'])")"
echo "      Role    : $(echo "$IDENTITY" | python3 -c "import sys,json; print(json.load(sys.stdin)['Arn'])")"

# ── 2. Install test dependencies ──────────────────────────────────────────────
echo ""
echo "[2/3] Installing test dependencies …"
pip install -q -r "${SCRIPT_DIR}/requirements-test.txt"

# ── 3. Run pytest ─────────────────────────────────────────────────────────────
echo ""
echo "[3/3] Running tests …"
echo "──────────────────────────────────────────────────────"

cd "${SCRIPT_DIR}"

# Default: run all tests except the slow E2E pipeline test
# Pass --run-e2e to include test_06_e2e_pipeline.py
EXTRA_ARGS=("$@")
E2E_MARKER="-m not e2e_pipeline"

for arg in "${EXTRA_ARGS[@]:-}"; do
    if [[ "$arg" == "--run-e2e" ]]; then
        E2E_MARKER=""
        EXTRA_ARGS=("${EXTRA_ARGS[@]/$arg}")
        break
    fi
done

python3 -m pytest \
    --tb=short \
    -v \
    --color=yes \
    ${E2E_MARKER:+$E2E_MARKER} \
    "${EXTRA_ARGS[@]:-}" \
    test_01_infrastructure.py \
    test_02_backend_api.py \
    test_03_dynamodb.py \
    test_04_s3.py \
    test_05_cloudwatch.py \
    ${E2E_MARKER:+} \
    2>&1 | tee /tmp/lightship_test_results.txt

EXIT_CODE=${PIPESTATUS[0]}

echo ""
echo "──────────────────────────────────────────────────────"
if [ $EXIT_CODE -eq 0 ]; then
    echo "  ✅  All tests PASSED"
else
    echo "  ❌  Some tests FAILED  (exit $EXIT_CODE)"
    echo "      Full log: /tmp/lightship_test_results.txt"
fi
echo "──────────────────────────────────────────────────────"
exit $EXIT_CODE
