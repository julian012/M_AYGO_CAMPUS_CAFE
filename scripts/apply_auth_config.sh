#!/usr/bin/env bash
# Reads the PUBLIC Auth0 values from a file OUTSIDE the repo and writes
# frontend/config.local.js (git-ignored). It does not deploy anything.
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="${1:-$HOME/.campus-cafe-auth/auth0.env}"
[ -f "$ENV_FILE" ] || { echo "No existe $ENV_FILE" >&2; exit 1; }
set -a; . "$ENV_FILE"; set +a
for v in AUTH0_DOMAIN AUTH0_CLIENT_ID AUTH0_AUDIENCE; do
  val="${!v:-}"
  if [ -z "$val" ] || [[ "$val" == *REPLACE_ME* ]]; then echo "Falta rellenar $v en $ENV_FILE" >&2; exit 1; fi
done
[[ "$AUTH0_DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]] || { echo "AUTH0_DOMAIN inválido (sin https:// ni /)" >&2; exit 1; }
cat > frontend/config.local.js <<JS
// Generado por scripts/apply_auth_config.sh. Valores públicos; archivo ignorado por git.
window.CAMPUS_CAFE_AUTH = { domain: "$AUTH0_DOMAIN", clientId: "$AUTH0_CLIENT_ID", audience: "$AUTH0_AUDIENCE" };
JS
echo "Escrito frontend/config.local.js"
echo
echo "Despliegue (solo cuando lo autorices; revisa la cuenta con 'aws sts get-caller-identity'):"
echo "  sam build && sam deploy --stack-name campus-cafe --region us-east-1 --resolve-s3 --capabilities CAPABILITY_IAM \\"
echo "    --parameter-overrides FrontendOrigin=http://localhost:8080 \\"
echo "    Auth0IssuerUrl=https://$AUTH0_DOMAIN/ Auth0Audience=$AUTH0_AUDIENCE \\"
echo "    ExistingExecutionRoleArn=arn:aws:iam::318203795545:role/LabRole"
