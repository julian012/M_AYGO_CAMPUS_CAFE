# Verificación en vivo (API desplegada en el Learner Lab) - 2026-11-04

Entorno: stack `campus-cafe`, us-east-1, authorizer JWT nativo (HTTP API), tenant Auth0 propio, usuarios sintéticos Alice y Bob.
Tokens NO incluidos. Solo se muestran estados y metadatos mínimos. Los IDs de pedido son sintéticos.
Alice = pedido `3c3d9684…`; Bob = pedido `8e3117be…`. Dueño comprobado en DynamoDB (`ownerIssuer`/`ownerSubject` ≠ vacío; Alice y Bob distintos).

| ID | Boundary/environment | Acción | Esperado | Observado | Resultado |
|---|---|---|---|---|---|
| G1 | HTTP API/lab | GET /products y /products/coffee sin token | 200 | 200, 200 | PASS |
| G2 | HTTP API/lab | POST /orders, GET /orders/x, POST …/cancel sin token | 401 | 401, 401, 401 | PASS |
| G2 | HTTP API/lab | GET /orders/x con token manipulado (`aaa.bbb.ccc`) | 401 | 401 | PASS |
| CORS | HTTP API/lab | OPTIONS /orders desde `http://localhost:8080` con `authorization` | 204 + headers permitidos | 204; allow-origin localhost:8080; allow-headers authorization,content-type,idempotency-key | PASS |
| G3 | HTTP API/lab, navegador | Alice crea y lee su pedido; Bob crea y lee el suyo | 201/200, dueño derivado en servidor | Alice 201/200; Bob 201/200 (tras un 503 CATALOG_UNAVAILABLE sin pedido y reintento con la misma clave); dueños en DynamoDB = Alice y Bob respectivamente | PASS |
| G4 | HTTP API/lab, curl | Bob: GET y cancel sobre pedido de Alice | 404 uniforme, sin cambios | 404, 404 `{"code":"ORDER_NOT_FOUND","message":"Order not found"}`; pedido de Alice `CREATED`, updatedAt = createdAt | PASS |
| G4 | HTTP API/lab, curl | Alice: GET y cancel sobre pedido de Bob | 404 uniforme, sin cambios | 404, 404 (mismo cuerpo); pedido de Bob `CREATED`, updatedAt = createdAt; su propio pedido 200 | PASS |
| G5 | HTTP API/lab, navegador | Carol (rol `cafe-readonly`, solo `orders:read`) envía un pedido desde la interfaz | 403 sin ejecutar Orders | Interfaz: "Forbidden". Access log: `POST /orders` 403 (02:26:36). Logs de Orders: sin ejecución. DynamoDB: sin pedido nuevo (2 pedidos con dueño, los de Alice y Bob) | PASS |
| G6 | HTTP API/lab | ID token en lugar del access token | rechazado | no ejecutado | NOT RUN |

Metadatos de los access tokens usados (inspeccionados en local, firma NO verificada por la herramienta; la validó el gateway):
`iss=https://dev-4ngm0udxgzqn6o01.us.auth0.com/`, `aud` incluye `https://campus-cafe/api`, `alg=RS256`, `scope=openid profile orders:create orders:read orders:cancel`.

Nota G5: el usuario se identifica por la sesión y la hora; el access log no guarda identidades. No se inspeccionó el scope del token de Carol.

Limitación: las atribuciones de los 404 de lectura hechas desde el navegador se apoyan en la sesión usada y en los tiempos de los logs, que no guardan identidades a propósito. Las pruebas con curl (G4) sí identifican al usuario por el token.
