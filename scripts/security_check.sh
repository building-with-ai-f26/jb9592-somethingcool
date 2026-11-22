#!/usr/bin/env bash
# Build 3 security checks for Tailor ("AI app, no database").
# Usage:  scripts/security_check.sh https://<your-app>.onrender.com
#         scripts/security_check.sh http://127.0.0.1:8000
# 1. Searches the repo (files + full git history) and the live page for secret keys.
# 2. Calls the AI route 30 times in a row; the rate limit should return 429.
#    The calls send no resume, so none of them reach the paid AI model ($0).
set -uo pipefail
URL="${1:?usage: $0 <base-url>}"
URL="${URL%/}"
cd "$(dirname "$0")/.."

# Prefixes from the assignment, plus the real shapes of those keys.
PREFIXES='sb_secret_|service_role|sk-or-|sk-ant-|sk-proj-|sk_live_'
REAL_KEYS='sk-or-v1-[0-9a-f]{64}|sk-ant-[A-Za-z0-9_-]{30,}|sk-proj-[A-Za-z0-9_-]{30,}|sk_live_[A-Za-z0-9]{20,}|sb_secret_[A-Za-z0-9_-]{20,}'
# Our own OpenRouter key from .env (read here, never printed).
OWN_KEY="$(grep -E '^OPENROUTER_API_KEY=' .env 2>/dev/null | cut -d= -f2- | tr -d '"'"'"' ')"

echo "== 1a. Repo: lines mentioning a key prefix (expected: placeholders and docs only)"
git grep -nE "$PREFIXES" -- ':!scripts/security_check.sh' | cut -c1-140 || echo "   none"
echo
echo "== 1b. Repo files + all git history: anything shaped like a REAL key (expected: none)"
hits=$( { git grep -nE "$REAL_KEYS"; git log --all -p | grep -nE "$REAL_KEYS"; } 2>/dev/null | wc -l | tr -d ' ')
own=0
if [ -n "$OWN_KEY" ]; then
  own=$( { git grep -nF "$OWN_KEY"; git log --all -p | grep -nF "$OWN_KEY"; } 2>/dev/null | wc -l | tr -d ' ')
fi
echo "   real-key-shaped matches: $hits | our own OpenRouter key found: $own time(s)"
echo
echo "== 1c. Live site $URL: page source checked for key prefixes (expected: none)"
page="$(curl -s "$URL/")"
echo "   page size: ${#page} bytes"
if echo "$page" | grep -qE "$PREFIXES"; then echo "   FOUND key prefix in live page!"; else echo "   none found"; fi
if [ -n "$OWN_KEY" ] && echo "$page" | grep -qF "$OWN_KEY"; then echo "   FOUND our own key in live page!"; fi
echo "   (PostHog phc_ key present: $(echo "$page" | grep -c 'phc_') line(s). That key is public by design.)"
echo
echo "== 2. Rate limit: 30 calls in a row to POST $URL/api/tailor"
codes=""
for i in $(seq 1 30); do
  c=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$URL/api/tailor" -H 'content-type: application/json' -d '{"url":""}')
  codes="$codes $c"
done
echo "  $codes"
n429=$(echo "$codes" | tr ' ' '\n' | grep -c '^429$')
echo "   -> $n429 of 30 calls returned 429 (rate limited)"
echo "   sample 429 body: $(curl -s -X POST "$URL/api/tailor" -H 'content-type: application/json' -d '{"url":""}')"
