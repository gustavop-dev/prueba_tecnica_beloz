#!/usr/bin/env bash
# End-to-end API walkthrough against a running stack (`make up`), with an assertion per step.
# open → confirm → purchase → rejected purchase → freeze → rejected purchase → unfreeze → repay
# → IDOR check → close → rejected purchase → ledger.
# Tokens and the webhook secret default to the development values of .env.example.
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:${APP_PORT:-8000}}"
CUSTOMER_TOKEN="${CUSTOMER_TOKEN:-local-dev-customer-a-token}"
OTHER_CUSTOMER_TOKEN="${OTHER_CUSTOMER_TOKEN:-local-dev-customer-b-token}"
WEBHOOK_SIGNING_SECRET="${WEBHOOK_SIGNING_SECRET:-local-dev-only-webhook-signing-secret-000}"

STATUS=""
BODY=""

signature() { # signature <raw body> — what the provider sends: t=<unix>,v1=HMAC-SHA256("<t>.<body>")
  python3 -c '
import hashlib, hmac, sys, time
timestamp = int(time.time())
digest = hmac.new(sys.argv[1].encode(), f"{timestamp}.{sys.argv[2]}".encode(), hashlib.sha256)
print(f"t={timestamp},v1={digest.hexdigest()}")' "$WEBHOOK_SIGNING_SECRET" "$1"
}

call() { # call <method> <path> <token|webhook> [json-body]
  local method="$1" path="$2" token="$3" data="${4:-}"
  local args=(-sS -X "$method" -w '\n%{http_code}')
  if [ "$token" = "webhook" ]; then
    args+=(-H "X-Webhook-Signature: $(signature "$data")")
  else
    args+=(-H "Authorization: Bearer $token")
  fi
  if [ "$method" = "POST" ]; then
    args+=(-H "Content-Type: application/json" -H "Idempotency-Key: $(python3 -c 'import uuid; print(uuid.uuid4())')" -d "${data:-{\}}")
  fi
  local output
  output="$(curl "${args[@]}" "$BASE_URL$path")"
  STATUS="${output##*$'\n'}"
  BODY="${output%$'\n'*}"
}

field() { # field <dotted.path>  — reads a value from $BODY
  python3 -c '
import json, sys
value = json.loads(sys.argv[1])
for key in sys.argv[2].split("."):
    value = value[int(key)] if isinstance(value, list) else value[key]
print(value)' "$BODY" "$1"
}

expect() { # expect <step> <status> [<field> <value>]...
  local step="$1" status="$2"
  shift 2
  if [ "$STATUS" != "$status" ]; then
    echo "FAIL  $step: HTTP $STATUS (expected $status): $BODY" >&2
    exit 1
  fi
  while [ "$#" -gt 0 ]; do
    local actual
    actual="$(field "$1")"
    if [ "$actual" != "$2" ]; then
      echo "FAIL  $step: $1=$actual (expected $2)" >&2
      exit 1
    fi
    shift 2
  done
  echo "ok    $step"
}

call POST /accounts "$CUSTOMER_TOKEN" '{"deposit_amount_minor": 100000}'
expect "1. request opening with a 1,000.00 USD deposit" 201 deposit.status PENDING
ACCOUNT_ID="$(field account_id)"
DEPOSIT_ID="$(field deposit.id)"

call GET "/accounts/$ACCOUNT_ID" "$CUSTOMER_TOKEN"
expect "2. the account does not exist before confirmation" 404 error.code NOT_FOUND

call POST /webhooks/payments webhook "{\"deposit_id\": \"$DEPOSIT_ID\", \"outcome\": \"succeeded\"}"
expect "3. provider confirms the deposit" 200 deposit.status CONFIRMED account_id "$ACCOUNT_ID"

call GET "/accounts/$ACCOUNT_ID" "$CUSTOMER_TOKEN"
expect "4. account active, limit = deposit, card active" 200 \
  status ACTIVE credit_limit_minor 100000 available_credit_minor 100000 card.status ACTIVE
CARD_ID="$(field card.id)"

call POST "/cards/$CARD_ID/authorizations" "$CUSTOMER_TOKEN" '{"amount_minor": 30000}'
expect "5. purchase of 300.00 approved and recorded as debt" 201 \
  status APPROVED outstanding_minor 30000 available_credit_minor 70000

call POST "/cards/$CARD_ID/authorizations" "$CUSTOMER_TOKEN" '{"amount_minor": 80000}'
expect "6. purchase above the available credit rejected" 422 error.code INSUFFICIENT_CREDIT

call POST "/cards/$CARD_ID/freeze" "$CUSTOMER_TOKEN"
expect "7. freeze the card" 200 card.status INACTIVE

call POST "/cards/$CARD_ID/authorizations" "$CUSTOMER_TOKEN" '{"amount_minor": 1000}'
expect "8. a frozen card cannot purchase" 409 error.code CARD_NOT_ACTIVE

call POST "/cards/$CARD_ID/unfreeze" "$CUSTOMER_TOKEN"
expect "9. unfreeze the card" 200 card.status ACTIVE

call POST "/accounts/$ACCOUNT_ID/repayments" "$CUSTOMER_TOKEN" '{"amount_minor": 10000}'
expect "10. repayment of 100.00" 201 outstanding_minor 20000

call POST "/accounts/$ACCOUNT_ID/repayments" "$CUSTOMER_TOKEN" '{"amount_minor": 20001}'
expect "11. repayment above the debt rejected" 422 error.code PAYMENT_EXCEEDS_DEBT

call GET "/accounts/$ACCOUNT_ID" "$OTHER_CUSTOMER_TOKEN"
expect "12. another customer cannot see the account" 404 error.code NOT_FOUND

call POST "/accounts/$ACCOUNT_ID/close" "$CUSTOMER_TOKEN"
expect "13. close: collateral pays the 200.00 debt, 800.00 refunded" 200 \
  status CLOSED debt_at_closure_minor 20000 refund_minor 80000 deposit.status PARTIALLY_APPLIED

call POST "/cards/$CARD_ID/authorizations" "$CUSTOMER_TOKEN" '{"amount_minor": 100}'
expect "14. no operation after closure" 409 error.code ACCOUNT_CLOSED

call GET "/accounts/$ACCOUNT_ID/ledger" "$CUSTOMER_TOKEN"
expect "15. ledger: deposit, purchase, repayment, closure (freezing moves no money)" 200 \
  entries.0.kind DEPOSIT_CONFIRMED entries.1.kind PURCHASE entries.2.kind REPAYMENT entries.3.kind CLOSURE

echo "Walkthrough completed: account $ACCOUNT_ID"
