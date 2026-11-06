# Campus Café: ACT2, diseño de aplicaciones cloud seguras

**Repositorio:** https://github.com/julian012/M_AYGO_CAMPUS_CAFE (rama `main`)

## Dónde está el trabajo de ACT2 en el repositorio

| Qué | Dónde |
|---|---|
| Identidad (principal `(iss, sub)`, falla cerrada) | `services/orders/auth.py` |
| Rechazo de campos de dueño enviados por el cliente | `services/orders/domain.py` |
| Dueño en datos, cancelación condicional, idempotencia por principal | `services/orders/repository.py`, `services/orders/service.py`, `services/orders/app.py` |
| Authorizer JWT, scopes por ruta, CORS, parámetros Auth0 | `template.yaml` |
| Login con PKCE y access token en `Authorization` | `frontend/app.js`, `frontend/index.html`, `frontend/config.js` |
| Generación de la config local de Auth0 (valores públicos, ignorada por git) | `scripts/apply_auth_config.sh` |
| 36 pruebas (4 Catalog + 32 Orders) | `tests/orders/test_orders.py` |
| Decision record, matriz de verificación, limitaciones, nota de IA | `docs/security.md` |
| Guion de demo | `docs/demo.md` |
| Evidencia local y en vivo (sin tokens) | `evidence/security-local-checks.txt`, `evidence/security-live-checks.md` |
| Estado y pendientes | `TASKS.md`, `README.md` (sección 6) |

**Curso:** AYGO(M) · Arquitectura de aplicaciones masivamente distribuidas — ACT2 *Designing Secure Cloud Applications* (entrega 06-nov-2026)

**Repositorio (código, plantilla, pruebas, documentación y evidencias):** https://github.com/julian012/M_AYGO_CAMPUS_CAFE

**API desplegada:** `https://xp4tl33vtf.execute-api.us-east-1.amazonaws.com` (AWS Academy Learner Lab, `us-east-1`, stack `campus-cafe`)

**Contenido de este PDF:** 1) Resumen · 2) Decision record (respuestas a las seis preguntas) · 3) Rutas y autorización · 4) Esquema de dueño e idempotencia · 5) Verificación (local y en la nube) · 6) Evidencia en vivo · 7) Fiabilidad conservada · 8) Diseño staff/catálogo · 9) Limitaciones · 10) Demo · 11) Uso de IA · Anexo: salida de pruebas locales.

---

## 1. Resumen

Alice y Bob pueden crear, leer y cancelar **solo sus propios pedidos**, aunque ambos tengan tokens válidos y conozcan el ID del otro. Tres capas, cada una con una decisión distinta:

- **Auth0** autentica al usuario y emite un *access token* RS256 para la audiencia de la API (Authorization Code + PKCE desde la SPA).
- **API Gateway** (authorizer JWT nativo) valida firma, emisor, audiencia y tiempos, y exige un scope por ruta (`orders:create`, `orders:read`, `orders:cancel`). El catálogo sigue público.
- **Orders** decide si *ese* pedido es del principal `(iss, sub)` tomado solo del contexto verificado: lectura y cancelación condicionadas al dueño; pedido ajeno, inexistente o legacy ⇒ el mismo 404; la idempotencia es por principal.

Se conservaron las garantías de ACT1 (precios del servidor, transacción pedido+idempotencia, 503 sin pedido parcial, `requestId`, tablas separadas).

**Estado de la evidencia.** Local (moto): 36 pruebas pasan (4 Catalog + 32 Orders). En la nube, ejecutadas a mano: G1–G5 y CORS **PASS**. **NOT RUN:** G6 (ID token en lugar de access token) y A1–A4 (token expirado, `nbf`, audiencia y emisor incorrectos). La restricción de invocación directa de Lambda no se verificó, y `LabRole` no es una política de mínimo privilegio verificada.

**Alcance y pista.** Los servicios Orders y la persistencia se verifican con pruebas locales (moto). Después se desplegó la versión de ACT2 en el lab con un tenant Auth0 propio y se ejecutaron G1–G5 y CORS (ver sección 4). El login real (Alice, Bob y un usuario de solo lectura) se probó a mano en el navegador. Siguen sin verificar G6, A1–A4 y la restricción de invocación directa de Lambda.

## 2. Decision record

1. **¿Por qué estos puntos de aplicación?** El proveedor de identidad autentica (quién es el usuario) y emite el access token. API Gateway decide si el token es válido (firma, emisor, audiencia, tiempos) y si trae el scope de la ruta. Orders decide si **ese pedido concreto** es de ese usuario. Un token válido con `orders:read` demuestra que el usuario puede *usar la operación*, no que *este* pedido sea suyo: sin la comprobación de dueño, Bob leería el pedido de Alice con solo conocer el ID.
2. **¿Por qué `(iss, sub)`?** `sub` solo es único dentro de un emisor; el mismo `sub` bajo otro emisor es otra persona (probado, A5). El email y el nombre pueden cambiar o repetirse, y `customerId`/owner del navegador lo controla el atacante (se rechaza con 400). Los pedidos legacy sin dueño dan 404 y no se asignan automáticamente.
3. **¿Por qué este flujo de token?** La SPA es un cliente público: no puede guardar secretos, por eso Authorization Code + PKCE con el SDK oficial (sin implementar el protocolo a mano). Se usa el **access token** con la audiencia de la API; el ID token es para la SPA y no es credencial de esta API. Es público: dominio, client ID, audiencia, issuer. Secreto o nunca expuesto: no hay client secret; los tokens no se escriben en logs, URLs ni archivos (el SDK los mantiene en memoria).
4. **¿Por qué esta operación de datos?** La cancelación valida dueño **y** estado dentro del mismo `ConditionExpression`; una lectura previa seguida de escritura incondicional tendría una ventana de carrera. La idempotencia usa inserción condicional dentro de la transacción: dos peticiones duplicadas no pueden crear dos pedidos. La huella de la petición se canonicaliza así: solo `productId` y `quantity` de cada línea, ordenadas por `productId` (el orden de las líneas no cambia la huella), JSON con claves ordenadas y sin espacios, y SHA-256. El principal no entra en la huella: entra en la clave de idempotencia, `IDEMP#sha256(JSON([iss, sub, clave]))`, que al codificar la tupla como JSON evita colisiones por concatenación. Misma clave y misma huella bajo el mismo principal ⇒ replay del pedido original; misma clave con otra huella ⇒ 409. Con la misma clave bajo otro principal se obtiene un registro independiente: no hay replay ni 409 cruzado, y nunca se devuelve el pedido ajeno.
5. **¿Por qué catálogo público y token no reenviado?** El catálogo es sintético y público por decisión documentada de este ejercicio, no regla general. Orders llama a Catalog sin reenviar el token del usuario: no hay audiencia ni política de servicio diseñadas para ello, y no hace falta. Invocación y roles: se reutiliza el `ExistingExecutionRoleArn` (LabRole); **no se verificó** la política de invocación Lambda ni el acceso directo a la función en AWS.
6. **¿Qué queda?** Restricciones del lab: se usó `LabRole` en ambas funciones, así que no se demuestra mínimo privilegio ni aislamiento IAM entre servicios, y la restricción de invocación directa de Lambda no se verificó. Pruebas sin ejecutar (NOT RUN): G6 (ID token en lugar de access token) y A1–A4 (token expirado, `nbf`, audiencia y emisor incorrectos); la carrera de cancelación se probó con una simulación determinista, no con concurrencia real. Revocación: un access token emitido sigue siendo válido hasta `exp` y el logout no lo revoca. No se intentó para producción: migración de pedidos legacy (quedan inaccesibles por diseño), rotación de claves, política de tiempos de vida, rol de personal/mantenedores de catálogo (solo diseño, sección 6) ni auditoría o límites de tasa. Detalle en la sección 7.

## 3. Ruta → scope → autorización

| Ruta | Authorizer | Scope exigido | Regla en Orders |
|---|---|---|---|
| `GET /products`, `GET /products/{id}` | ninguno (público a propósito: catálogo sintético) | — | — |
| `POST /orders` | `Auth0Jwt` | `orders:create` | dueño = `(iss, sub)` del contexto verificado; rechaza campos de dueño |
| `GET /orders/{id}` | `Auth0Jwt` | `orders:read` | solo si el dueño guardado coincide; si no, 404 uniforme |
| `POST /orders/{id}/cancel` | `Auth0Jwt` | `orders:cancel` | `UpdateItem` condicionado a dueño y `status = CREATED` |

Un scope por ruta, porque el authorizer de HTTP API acepta el token si tiene **al menos uno** de los scopes configurados. No hay `DefaultAuthorizer`, así que cada ruta protegida lo declara explícitamente (`template.yaml`, eventos de `OrdersFunction`).

## 4. Esquema de dueño e idempotencia

Tabla Orders (clave `pk`):

| `pk` | Atributos relevantes |
|---|---|
| `ORDER#<orderId>` | pedido + `ownerIssuer`, `ownerSubject` (nunca se devuelven al cliente) |
| `IDEMP#<sha256>` | `ownerIssuer`, `ownerSubject`, `fingerprint`, `orderId`, `response` |

- **Clave de idempotencia:** `sha256( json.dumps([iss, sub, clientKey], separators=(",",":")) )`. La codificación JSON de la tupla es inequívoca (sin delimitadores ambiguos) y acota la longitud. Probado: `("iss","a|b"),"c"` ≠ `("iss","a"),"b|c"` ≠ `("iss|a","b"),"c"`.
- **Hash canónico del request:** SHA-256 de los ítems validados (`productId`, `quantity`), ordenados por `productId`, JSON con claves ordenadas y sin espacios. Excluye totales y precios (los calcula el servidor). Es el `fingerprint` de la línea base, sin cambios.
- **Mismo principal + clave + request:** replay del original con su snapshot de precios. **Mismo principal + clave + otro request:** `409`. **Otro principal + misma clave:** espacio independiente.
- **Escritura atómica:** sin cambios, `TransactWriteItems` con `attribute_not_exists(pk)` en el pedido y en el registro de idempotencia. Si pierde la carrera, vuelve a leer el registro del ganador y lo reproduce o da 409; nunca una escritura incondicional.
- **Datos legacy:** un pedido sin `ownerIssuer`/`ownerSubject` no es de nadie: 404. No hay backfill.

## 5. Verificación

Entorno `local` = pruebas unitarias con moto (`scripts/run_unit_tests.sh`, salida en `evidence/security-local-checks.txt`). El contexto de authorizer en esas pruebas es un **fixture fabricado**: prueba la lógica de Orders, no la verificación del JWT, ni API Gateway, ni IAM desplegado.

### Frontera desplegada (G)
Evidencia redactada en `evidence/security-live-checks.md`. Entorno: HTTP API desplegada en el lab, tenant Auth0 propio, usuarios sintéticos.

| ID | Entorno | Prueba | Esperado | Resultado |
|---|---|---|---|---|
| G1 | HTTP API/lab | GET público sin token | 200 | **PASS** (200, 200) |
| G2 | HTTP API/lab | Orders sin token / token manipulado | 401 | **PASS** (401 en las 3 rutas y con token manipulado) |
| G3 | HTTP API/lab | Alice y Bob crean y leen los suyos | éxito, dueño derivado | **PASS**: 201/200 cada uno; dueño en DynamoDB = su `sub` |
| G4 | HTTP API/lab | Bob lee/cancela pedido de Alice y viceversa | 404 uniforme, sin cambios | **PASS**: 404 y 404 en ambos sentidos; ambos pedidos siguen `CREATED` sin modificar |
| G5 | HTTP API/lab | Token válido sin el scope | 403 | **PASS**: usuario con rol de solo lectura; `POST /orders` → 403 en el access log, sin ejecución de Orders ni pedido nuevo |
| G6 | HTTP API/lab | ID token en lugar de access token | rechazado | **NOT RUN** |

CORS: preflight de `OPTIONS /orders` desde `http://localhost:8080` responde 204 con `authorization`, `content-type` e `idempotency-key` permitidos (PASS).

### Servicio y persistencia (S)

| ID | Entorno | Prueba (`tests/orders/test_orders.py`) | Resultado |
|---|---|---|---|
| S1 | local | `test_s1_missing_or_malformed_context_fails_closed_without_writes`, `test_s1_identity_in_headers_or_body_is_ignored`, `test_s1_forged_owner_fields_rejected_and_nothing_persisted` | PASS |
| S2 | local | `test_s2_read_own_foreign_missing_and_legacy_ownerless` | PASS |
| S3 | local | `test_s3_foreign_cancel_is_404_and_changes_nothing`, `test_s3_cancel_legacy_ownerless_is_404_and_untouched` | PASS |
| S4 | local | `test_s4_cancel_write_condition_includes_owner_and_status`, `test_s4_stale_read_cannot_win_against_racing_status_change`, `test_s4_owner_flip_between_check_and_write_is_refused` (carrera **simulada** alterando el ítem justo antes del write) | PASS |
| S5 | local | `test_s5_replay_then_changed_payload_conflicts`, `test_s5_equivalent_json_canonicalization` | PASS |
| S6 | local | `test_s6_bob_same_client_key_gets_independent_order` | PASS |
| S7 | local | `test_s7_concurrent_duplicates_from_same_principal_one_order` (hilos), `test_s7_conditional_failure_replays_the_winner_and_leaves_no_orphan` | PASS |
| S8 | local | `test_s8_tampered_price_ignored_and_catalog_failure_leaves_nothing`, más los tests de la línea base | PASS |
| S9 | local | `test_s9_logs_have_request_id_and_no_identity_or_tokens` (correlación y sin tokens); separación de tablas por inspección de código: Orders solo usa `ORDERS_TABLE` | PASS (logs) / revisión (tablas) |
| S10 | local | `test_s10_replay_after_catalog_price_change_keeps_snapshot_for_same_principal_only` | PASS |

### Extras (A)

| ID | Resultado |
|---|---|
| A1 expirado, A2 `nbf` futuro, A3 audiencia incorrecta, A4 emisor no confiable | **NOT RUN**: requieren tokens firmados y el authorizer desplegado; no se añadió un segundo verificador JWT en Orders |
| A5 mismo `sub`, otro issuer | PASS local (`test_a5_...`); además `test_expected_issuer_pin_rejects_other_issuer` (defensa en profundidad) |

### Otros chequeos
- Prueba de mutación manual: al quitar dueño/estado de la condición de cancelación fallaron 5 pruebas (la suite detecta la regresión).
- `cfn-lint`: mismos 3 hallazgos previos (E3031, 2×W1028); sin hallazgos nuevos. `sam validate --lint` y `sam build` correctos.
- Frontend: sintaxis revisada (`node --check`) y revisión de código; login real (Authorization Code + PKCE) ejecutado a mano en el navegador con Alice, Bob y el usuario de solo lectura (G3–G5).

## 6. Evidencia en vivo (API desplegada)

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

## 7. Garantías de fiabilidad conservadas
Totales y snapshot calculados en servidor (S8, S10); pedido + idempotencia en una transacción (S7); cancelación durable (`test_cancel_is_idempotent_and_persisted`, S3); Catalog caído → 503 sin pedido parcial (S8, línea base); `requestId` en logs (S9); tablas separadas (código). Toda la suite de la línea base sigue pasando (36 pruebas en total).

## 8. Diseño: personal de café y mantenedores de catálogo (no implementado)

| Actor | Acción | Recurso | Fuente de asignación confiable | Punto de aplicación |
|---|---|---|---|---|
| Staff de café | leer/gestionar pedidos | pedidos con `cafeId` = el suyo | asignación `(iss, sub) → cafeId` en una tabla de asignaciones gestionada por administración, o grupo/rol del IdP incluido en el token verificado; nunca un claim enviado por el navegador | Orders: el pedido guarda `cafeId` al crearse (derivado de la ruta/menú del servidor); la operación compara `cafeId` del pedido con la asignación del principal en el servidor y en la condición de escritura; scope `orders:staff` en el gateway es solo la primera barrera |
| Mantenedor de catálogo | modificar productos que tiene asignados | productos con `ownerGroup` = el suyo | asignación en el IdP/tabla del servicio Catalog | Catalog (servicio dueño de su tabla) con permiso propio `catalog:write` y condición por producto; **no** da permisos de administrador de AWS |

Dónde se guarda el identificador: atributo `cafeId` en el ítem del pedido (y en un GSI si hace falta listar por café). Restringe datos, no solo interfaz: la consulta y el write condicional incluyen `cafeId`. El staff de un café recibe 404 para pedidos de otro. Una implementación requeriría nuevos contratos, asignaciones confiables, pruebas de acceso entre cafés y aprobación del instructor.

## 9. Limitaciones
- **No verificado en nube:** ID token (G6), A1–A4 (token expirado, `nbf`, audiencia y emisor incorrectos) y la restricción de invocación directa de Lambda. El rechazo de tokens manipulados y sin token sí se probó (G2); la verificación de firma y emisor la hace el gateway y solo se observó indirectamente.
- **LabRole** es una restricción del laboratorio, **no** una política de mínimo privilegio verificada. Sin despliegue, el SAM crea roles de mínimo privilegio por servicio (ver plantilla). No se afirma que sea apto para producción.
- Revocación: un access token emitido sigue siendo válido hasta `exp`; el logout local no lo revoca. No se analizaron rotación de claves ni tiempos de vida.
- No hay migración de pedidos legacy; esos pedidos quedan inaccesibles por diseño.
- La carrera de cancelación se probó con una simulación determinista, no con concurrencia real contra DynamoDB.
- Los pedidos creados antes de este cambio (clave `IDEMP#<key>` y sin dueño) quedan huérfanos.
- **Proceso de trabajo con la IA (no bloqueante):** las pruebas en la nube (401, 403, 404) se hicieron a mano y la colaboración fue más lenta de lo necesario por la forma de pedir la información: tuve que ejecutar comandos fuera de la consola y copiar tokens, y varias veces copié algo distinto de lo pedido (el texto del comando en lugar del token, o el token sin `Bearer`), con lo que la petición ni llegaba a Orders. Además, al principio la IA no ejecutaba las llamadas con mis tokens hasta que le confirmé que, al ser un laboratorio con usuarios sintéticos, podía hacerlo. Ningún caso fue un bloqueo ni cambió un resultado: se repitieron las pruebas y los resultados de G1–G5 son los de las ejecuciones válidas. Los tokens usados quedaron en la conversación; por eso las contraseñas de los usuarios de prueba se cambian al terminar.

## 10. Demo

Orden del handout (Tarea 7). Cada paso indica **qué mostrar**, **qué decir** y **qué evidencia** respalda lo que se ve. Se declara qué es en vivo (API desplegada) y qué es local (pruebas con moto).

### Antes de empezar (checklist)
- [ ] Learner Lab activo y `aws sts get-caller-identity` responde.
- [ ] `curl -s -o /dev/null -w "%{http_code}\n" https://xp4tl33vtf.execute-api.us-east-1.amazonaws.com/products` imprime `200`.
- [ ] `cd frontend && python3 -m http.server 8080` corriendo (mismo puerto que Auth0 y CORS).
- [ ] Dos ventanas de navegador separadas (una normal y una de incógnito): **Alice** y **Bob**, sin mezclar sesiones.
- [ ] Terminal con `scripts/`-adyacente lista: `cd campus-cafe`.
- [ ] Cerrados: el archivo `credenciales`, DevTools con el header `Authorization` visible y cualquier captura con tokens.

### 1. Catálogo público y pedido de Alice (EN VIVO, ~1 min)
- **Mostrar:** sin iniciar sesión se ve el menú; el botón de enviar está deshabilitado. Iniciar sesión como Alice, agregar un producto, enviar.
- **Decir:** el catálogo es público por decisión documentada (datos sintéticos). Los pedidos exigen access token con audiencia de la API; el login usa el SDK oficial de Auth0 con Authorization Code + PKCE. Nunca hay client secret en el navegador.
- **Evidencia:** G1, G2, G3 en `evidence/security-live-checks.md`.
- Anotar el `orderId` de Alice (`ALICE_ID`).

### 2. Lectura de Alice y Bob denegado (EN VIVO, ~1.5 min)
- **Mostrar:** Alice lee su pedido (aparece en "Mis pedidos"). En la ventana de Bob, buscar `ALICE_ID` → "Pedido no encontrado".
- **Mostrar (opcional, terminal):** `evidence/security-live-checks.md`, filas G4: Bob lee y cancela el pedido de Alice con 404 uniforme; el pedido de Alice sigue en `CREATED` sin tocar.
- **Decir:** un token válido con `orders:read` no autoriza *este* pedido. Orders compara el dueño guardado `(iss, sub)` con el principal que sacó del contexto verificado por el gateway. Pedido ajeno, inexistente y legacy sin dueño dan el mismo 404, sin payload ni dueño.
- **Evidencia:** G4 (en vivo) y S2, S3 (local).

### 3. Replay del mismo principal y conflicto por payload distinto (LOCAL, ~1 min)
La interfaz genera una clave aleatoria por envío, así que esto se demuestra con pruebas.
```bash
.venv/bin/python -m unittest discover -s tests/orders -k test_s5 -v
```
- **Mostrar:** `test_s5_replay_then_changed_payload_conflicts` y `test_s5_equivalent_json_canonicalization` en `ok`.
- **Decir:** misma clave y mismo request devuelve el pedido original (con su snapshot de precios); mismo principal y clave con otro request devuelve 409 y no crea un segundo pedido. El hash canónico ordena las líneas por `productId` e ignora totales y precios.
- **Etiqueta:** local, DynamoDB simulado (moto); no es evidencia de la nube.

### 4. Pedido independiente de Bob con la misma clave (LOCAL, ~45 s)
```bash
.venv/bin/python -m unittest discover -s tests/orders -k test_s6 -k test_idempotency_key_encoding -v
```
- **Mostrar:** `test_s6_bob_same_client_key_gets_independent_order` y `test_idempotency_key_encoding_has_no_collisions` en `ok`.
- **Decir:** la clave de idempotencia es `sha256([iss, sub, clientKey])`: otro principal con la misma clave tiene un espacio independiente y nunca recibe la respuesta de Alice.

### 5. Una prueba de fiabilidad y una limitación (≈2 min)
**Fiabilidad (LOCAL):**
```bash
.venv/bin/python -m unittest discover -s tests/orders -k test_s8 -k test_s4_cancel_write -v
```
- **Mostrar:** `test_s8_tampered_price_ignored_and_catalog_failure_leaves_nothing` y `test_s4_cancel_write_condition_includes_owner_and_status` en `ok`.
- **Decir:** el precio lo calcula el servidor; si Catalog falla, 503 sin pedido parcial; la cancelación valida dueño **y** estado dentro del `ConditionExpression`, no con una lectura previa.
- **Anécdota real (opcional):** en la prueba en vivo hubo un 503 `CATALOG_UNAVAILABLE` sin pedido creado, y el reintento con la misma clave creó el pedido (ver G3). Causa no verificada.

**Limitación declarada:**
- **Mostrar:** `docs/security.md`, sección 4 (matriz) y sección 7.
- **Decir:** G6 (ID token) está **NOT RUN**; A1–A4 (token expirado, `nbf`, audiencia y emisor incorrectos) están **NOT RUN**; la restricción de invocación directa de Lambda no se verificó; `LabRole` es una restricción del laboratorio, no una política de mínimo privilegio verificada; los access tokens siguen vigentes hasta `exp` aunque se cierre sesión; no es apto para producción.

### Cierre (15 s)
Autoevaluación del handout: *¿puedo señalar evidencia de que Bob, con identidad y permisos válidos, no puede exponer ni alterar el pedido de Alice, ni siquiera mediante un reintento?* Sí: lectura y cancelación cruzadas en vivo (G4), reintento y misma clave con otro principal en pruebas locales (S5, S6, S7). Lo que no se probó en la nube se declaró arriba.

### Plan B si algo falla
- **El lab expiró o `/products` da 500:** renovar el lab y las credenciales; mientras tanto, mostrar `evidence/security-live-checks.md` y ejecutar solo los pasos 3-5 (locales).
- **El login falla:** revisar URLs en Auth0 (`http://localhost:8080` exacto) y el puerto del servidor.
- **No usar** tokens pegados en el chat ni en pantalla; no mostrar el archivo `credenciales`.

## 11. Uso de IA
- **Qué pedí:** inspeccionar el proyecto, proponer un plan mínimo por pasos (sin editar) y luego implementar: extracción del principal, dueño en lectura/cancelación condicional, idempotencia por principal, authorizer en la plantilla, SDK en el frontend y pruebas.
- **Qué cambié o rechacé:** descarté `NONE` como authorizer de las rutas públicas (SAM solo lo admite con `DefaultAuthorizer`; `cfn-lint` lo detectó) y quedó sin authorizer, sin `DefaultAuthorizer`. Se descartaron verificadores JWT propios.
- **Qué verifiqué yo / ejecuté:** las 36 pruebas, la prueba de mutación, `cfn-lint`, `sam validate --lint`, `sam build` y `node --check`. Además ejecuté a mano G1–G5 y CORS contra la API desplegada. **Sin verificar:** G6, A1–A4 y la restricción de invocación directa de Lambda.

## Anexo. Salida de las pruebas locales

Tomada de `evidence/security-local-checks.txt` (entorno local, moto).

```
# Security checks (local) - 2026-11-02T21:36:52Z
$ scripts/run_unit_tests.sh
test_get_product (test_catalog.CatalogTests) ... /Users/jcserna/Documents/obsidian/Notes/ISIS_AYGO_M_Arquitectura_gobernabilidad/Talleres/2026_10_29/campus-cafe/.venv/lib/python3.9/site-packages/boto3/compat.py:89: PythonDeprecationWarning: Boto3 will no longer support Python 3.9 starting April 29, 2026. To continue receiving service updates, bug fixes, and security updates please upgrade to Python 3.10 or later. More information can be found here: https://aws.amazon.com/blogs/developer/python-support-policy-updates-for-aws-sdks-and-tools/
test_list_has_five_products_with_one_unavailable (test_catalog.CatalogTests) ... ok
test_store_failure_returns_503 (test_catalog.CatalogTests) ... ok
test_unknown_product_404 (test_catalog.CatalogTests) ... ok
Ran 4 tests in 0.344s
OK
test_a5_same_subject_different_issuer_is_a_different_principal (test_orders.OrdersTests) ... /Users/jcserna/Documents/obsidian/Notes/ISIS_AYGO_M_Arquitectura_gobernabilidad/Talleres/2026_10_29/campus-cafe/.venv/lib/python3.9/site-packages/boto3/compat.py:89: PythonDeprecationWarning: Boto3 will no longer support Python 3.9 starting April 29, 2026. To continue receiving service updates, bug fixes, and security updates please upgrade to Python 3.10 or later. More information can be found here: https://aws.amazon.com/blogs/developer/python-support-policy-updates-for-aws-sdks-and-tools/
test_cancel_is_idempotent_and_persisted (test_orders.OrdersTests) ... ok
test_cancel_unknown_order_404_and_creates_nothing (test_orders.OrdersTests) ... ok
test_catalog_failure_returns_503_and_stores_nothing (test_orders.OrdersTests) ... ok
test_client_price_override_is_ignored (test_orders.OrdersTests) ... ok
test_create_computes_total_server_side (test_orders.OrdersTests) ... ok
test_created_order_persists_server_derived_owner_and_hides_it (test_orders.OrdersTests) ... ok
test_expected_issuer_pin_rejects_other_issuer (test_orders.OrdersTests) ... ok
test_five_concurrent_identical_requests_create_one_order (test_orders.OrdersTests) ... ok
test_get_order_survives_new_repository_instance (test_orders.OrdersTests) ... ok
test_get_unknown_order_404 (test_orders.OrdersTests) ... ok
test_idempotency_key_encoding_has_no_collisions (test_orders.OrdersTests) ... ok
test_invalid_requests_return_400 (test_orders.OrdersTests) ... ok
test_missing_key_and_bad_json_return_400 (test_orders.OrdersTests) ... ok
test_replay_is_stable_after_price_change (test_orders.OrdersTests) ... ok
test_replay_returns_original_without_new_order_or_catalog_call (test_orders.OrdersTests) ... ok
test_s10_replay_after_catalog_price_change_keeps_snapshot_for_same_principal_only (test_orders.OrdersTests) ... ok
test_s1_forged_owner_fields_rejected_and_nothing_persisted (test_orders.OrdersTests) ... ok
test_s1_identity_in_headers_or_body_is_ignored (test_orders.OrdersTests) ... ok
test_s1_missing_or_malformed_context_fails_closed_without_writes (test_orders.OrdersTests) ... ok
test_s2_read_own_foreign_missing_and_legacy_ownerless (test_orders.OrdersTests) ... ok
test_s3_cancel_legacy_ownerless_is_404_and_untouched (test_orders.OrdersTests) ... ok
test_s3_foreign_cancel_is_404_and_changes_nothing (test_orders.OrdersTests) ... ok
test_s4_cancel_write_condition_includes_owner_and_status (test_orders.OrdersTests) ... ok
test_s4_owner_flip_between_check_and_write_is_refused (test_orders.OrdersTests) ... ok
test_s4_stale_read_cannot_win_against_racing_status_change (test_orders.OrdersTests)
A pre-check would pass, but the status changes before the write: the conditional ... ok
test_s5_equivalent_json_canonicalization (test_orders.OrdersTests) ... ok
test_s5_replay_then_changed_payload_conflicts (test_orders.OrdersTests) ... ok
test_s6_bob_same_client_key_gets_independent_order (test_orders.OrdersTests) ... ok
test_s7_concurrent_duplicates_from_same_principal_one_order (test_orders.OrdersTests) ... ok
test_s7_conditional_failure_replays_the_winner_and_leaves_no_orphan (test_orders.OrdersTests)
Simulate losing the race: winner's record appears between our lookup and our write. ... ok
test_s8_tampered_price_ignored_and_catalog_failure_leaves_nothing (test_orders.OrdersTests) ... ok
test_s9_logs_have_request_id_and_no_identity_or_tokens (test_orders.OrdersTests) ... ok
test_same_key_different_request_conflicts (test_orders.OrdersTests) ... ok
test_unknown_product_400_and_unavailable_409 (test_orders.OrdersTests) ... ok
test_unknown_route_404 (test_orders.OrdersTests) ... ok
Ran 36 tests in 2.229s
OK

$ .venv/bin/cfn-lint template.yaml  (only the 3 pre-existing findings)
E3031 '' does not match '^arn:(aws[a-zA-Z-]*)?:iam::\\d{12}:
W1028 ['Fn::If', 1] is not reachable. When setting condition
W1028 ['Fn::If', 1] is not reachable. When setting condition
$ sam validate --lint
/Users/jcserna/Documents/obsidian/Notes/ISIS_AYGO_M_Arquitectura_gobernabilidad/Talleres/2026_10_29/campus-cafe/template.yaml is a valid SAM Template
```
