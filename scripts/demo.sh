#!/usr/bin/env bash
# Pasos 4-7 de la demo por API (los pasos 1-3 y la parte visual se hacen en el navegador).
# Uso: scripts/demo.sh [API_URL]   (por defecto, la salida ApiBaseUrl del stack campus-cafe)
set -euo pipefail
out() { aws cloudformation describe-stacks --stack-name campus-cafe --query "Stacks[0].Outputs[?OutputKey==\`$1\`].OutputValue" --output text; }
API="${1:-$(out ApiBaseUrl)}"
KEY="demo-$(date +%s)"
BODY='{"items":[{"productId":"coffee","quantity":2},{"productId":"tea","quantity":1}]}'
hdr() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
post() { curl -s -i -X POST "$API/orders" -H 'content-type: application/json' -H "Idempotency-Key: $1" -d "$2" | tr -d '\r' | grep -iE '^(HTTP|location|apigw-requestid)|^\{'; }

hdr "Paso 2 (por API): crear pedido. Total esperado: 2 x 350 + 1 x 300 = 1000 centavos"
R=$(curl -s -i -X POST "$API/orders" -H 'content-type: application/json' -H "Idempotency-Key: $KEY" -d "$BODY" | tr -d '\r')
echo "$R" | grep -iE '^(HTTP|location|apigw-requestid)|^\{'
OID=$(echo "$R" | grep -o '"orderId": "[^"]*"' | cut -d'"' -f4)
RID=$(echo "$R" | grep -i '^apigw-requestid' | awk '{print $2}')

hdr "Paso 4: reintentar la misma creación (misma clave y cuerpo) -> 200 con el mismo orderId ($OID)"
post "$KEY" "$BODY"

hdr "Paso 5a: misma clave, otro cuerpo -> 409 IDEMPOTENCY_KEY_CONFLICT"
post "$KEY" '{"items":[{"productId":"coffee","quantity":5}]}'

hdr "Paso 5b: producto no disponible (muffin) -> 409 PRODUCT_UNAVAILABLE"
post "demo-unavail-$KEY" '{"items":[{"productId":"muffin","quantity":1}]}'

hdr "Paso 6: cancelar dos veces (3 s entre ambas) -> ambas CANCELLED y el mismo updatedAt = una sola transición"
for i in 1 2; do [ $i = 2 ] && sleep 3; curl -s -X POST "$API/orders/$OID/cancel" | python3 -c "import sys,json;d=json.load(sys.stdin);print('cancel $i ->',d['status'],'updatedAt',d['updatedAt'])"; done
curl -s "$API/orders/$OID" | python3 -c "import sys,json;d=json.load(sys.stdin);print('GET ->',d['status'],'total',d['totalCents'])"

hdr "Paso 7a: 5 envíos concurrentes con la misma clave -> un solo orderId, un solo 201"
CK="demo-conc-$KEY"
seq 5 | xargs -P5 -I{} curl -s -o /dev/null -w '%{http_code}\n' -X POST "$API/orders" -H 'content-type: application/json' -H "Idempotency-Key: $CK" -d '{"items":[{"productId":"tea","quantity":1}]}' | sort | uniq -c

hdr "Paso 7b: log correlacionado del primer pedido (requestId $RID)"
APP=$(out OrdersLogGroupName); ACC=$(out ApiAccessLogGroupName)
for i in $(seq 12); do
  A=$(aws logs filter-log-events --log-group-name "$ACC" --filter-pattern "\"$RID\"" --query 'events[].message' --output text)
  [ -n "$A" ] && break; sleep 5
done
echo "access log: ${A:-(aún no entregado; reintentar en un minuto)}"
echo "app log:    $(aws logs filter-log-events --log-group-name "$APP" --filter-pattern "\"$RID\"" --query 'events[].message' --output text)"
