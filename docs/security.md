# ACT2: Seguridad de Campus Café (pista local)

**Pista de ejecución: LOCAL + verificación parcial en vivo.** Los servicios Orders y la persistencia se verifican con pruebas locales (moto). Después se desplegó la versión de ACT2 en el lab con un tenant Auth0 propio y se ejecutaron G1–G5 y CORS (ver sección 4). El login real (Alice, Bob y un usuario de solo lectura) se probó a mano en el navegador. Siguen sin verificar G6, A1–A4 y la restricción de invocación directa de Lambda.

## 1. Ruta → scope → autorización

| Ruta | Authorizer | Scope exigido | Regla en Orders |
|---|---|---|---|
| `GET /products`, `GET /products/{id}` | ninguno (público a propósito: catálogo sintético) | — | — |
| `POST /orders` | `Auth0Jwt` | `orders:create` | dueño = `(iss, sub)` del contexto verificado; rechaza campos de dueño |
| `GET /orders/{id}` | `Auth0Jwt` | `orders:read` | solo si el dueño guardado coincide; si no, 404 uniforme |
| `POST /orders/{id}/cancel` | `Auth0Jwt` | `orders:cancel` | `UpdateItem` condicionado a dueño y `status = CREATED` |

Un scope por ruta, porque el authorizer de HTTP API acepta el token si tiene **al menos uno** de los scopes configurados. No hay `DefaultAuthorizer`, así que cada ruta protegida lo declara explícitamente (`template.yaml`, eventos de `OrdersFunction`).

## 2. Esquema de dueño e idempotencia

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

## 3. Decision record

1. **¿Por qué estos puntos de aplicación?** El proveedor de identidad autentica (quién es el usuario) y emite el access token. API Gateway decide si el token es válido (firma, emisor, audiencia, tiempos) y si trae el scope de la ruta. Orders decide si **ese pedido concreto** es de ese usuario. Un token válido con `orders:read` demuestra que el usuario puede *usar la operación*, no que *este* pedido sea suyo: sin la comprobación de dueño, Bob leería el pedido de Alice con solo conocer el ID.
2. **¿Por qué `(iss, sub)`?** `sub` solo es único dentro de un emisor; el mismo `sub` bajo otro emisor es otra persona (probado, A5). El email y el nombre pueden cambiar o repetirse, y `customerId`/owner del navegador lo controla el atacante (se rechaza con 400). Los pedidos legacy sin dueño dan 404 y no se asignan automáticamente.
3. **¿Por qué este flujo de token?** La SPA es un cliente público: no puede guardar secretos, por eso Authorization Code + PKCE con el SDK oficial (sin implementar el protocolo a mano). Se usa el **access token** con la audiencia de la API; el ID token es para la SPA y no es credencial de esta API. Es público: dominio, client ID, audiencia, issuer. Secreto o nunca expuesto: no hay client secret; los tokens no se escriben en logs, URLs ni archivos (el SDK los mantiene en memoria).
4. **¿Por qué esta operación de datos?** La cancelación valida dueño **y** estado dentro del mismo `ConditionExpression`; una lectura previa seguida de escritura incondicional tendría una ventana de carrera. La idempotencia usa inserción condicional dentro de la transacción: dos peticiones duplicadas no pueden crear dos pedidos. La huella de la petición se canonicaliza así: solo `productId` y `quantity` de cada línea, ordenadas por `productId` (el orden de las líneas no cambia la huella), JSON con claves ordenadas y sin espacios, y SHA-256. El principal no entra en la huella: entra en la clave de idempotencia, `IDEMP#sha256(JSON([iss, sub, clave]))`, que al codificar la tupla como JSON evita colisiones por concatenación. Misma clave y misma huella bajo el mismo principal ⇒ replay del pedido original; misma clave con otra huella ⇒ 409. Con la misma clave bajo otro principal se obtiene un registro independiente: no hay replay ni 409 cruzado, y nunca se devuelve el pedido ajeno.
5. **¿Por qué catálogo público y token no reenviado?** El catálogo es sintético y público por decisión documentada de este ejercicio, no regla general. Orders llama a Catalog sin reenviar el token del usuario: no hay audiencia ni política de servicio diseñadas para ello, y no hace falta. Invocación y roles: se reutiliza el `ExistingExecutionRoleArn` (LabRole); **no se verificó** la política de invocación Lambda ni el acceso directo a la función en AWS.
6. **¿Qué queda?** Restricciones del lab: se usó `LabRole` en ambas funciones, así que no se demuestra mínimo privilegio ni aislamiento IAM entre servicios, y la restricción de invocación directa de Lambda no se verificó. Pruebas sin ejecutar (NOT RUN): G6 (ID token en lugar de access token) y A1–A4 (token expirado, `nbf`, audiencia y emisor incorrectos); la carrera de cancelación se probó con una simulación determinista, no con concurrencia real. Revocación: un access token emitido sigue siendo válido hasta `exp` y el logout no lo revoca. No se intentó para producción: migración de pedidos legacy (quedan inaccesibles por diseño), rotación de claves, política de tiempos de vida, rol de personal/mantenedores de catálogo (solo diseño, sección 6) ni auditoría o límites de tasa. Detalle en la sección 7.

## 4. Verificación

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

## 5. Garantías de fiabilidad conservadas
Totales y snapshot calculados en servidor (S8, S10); pedido + idempotencia en una transacción (S7); cancelación durable (`test_cancel_is_idempotent_and_persisted`, S3); Catalog caído → 503 sin pedido parcial (S8, línea base); `requestId` en logs (S9); tablas separadas (código). Toda la suite de la línea base sigue pasando (36 pruebas en total).

## 6. Diseño: personal de café y mantenedores de catálogo (no implementado)

| Actor | Acción | Recurso | Fuente de asignación confiable | Punto de aplicación |
|---|---|---|---|---|
| Staff de café | leer/gestionar pedidos | pedidos con `cafeId` = el suyo | asignación `(iss, sub) → cafeId` en una tabla de asignaciones gestionada por administración, o grupo/rol del IdP incluido en el token verificado; nunca un claim enviado por el navegador | Orders: el pedido guarda `cafeId` al crearse (derivado de la ruta/menú del servidor); la operación compara `cafeId` del pedido con la asignación del principal en el servidor y en la condición de escritura; scope `orders:staff` en el gateway es solo la primera barrera |
| Mantenedor de catálogo | modificar productos que tiene asignados | productos con `ownerGroup` = el suyo | asignación en el IdP/tabla del servicio Catalog | Catalog (servicio dueño de su tabla) con permiso propio `catalog:write` y condición por producto; **no** da permisos de administrador de AWS |

Dónde se guarda el identificador: atributo `cafeId` en el ítem del pedido (y en un GSI si hace falta listar por café). Restringe datos, no solo interfaz: la consulta y el write condicional incluyen `cafeId`. El staff de un café recibe 404 para pedidos de otro. Una implementación requeriría nuevos contratos, asignaciones confiables, pruebas de acceso entre cafés y aprobación del instructor.

## 7. Limitaciones
- **No verificado en nube:** ID token (G6), A1–A4 (token expirado, `nbf`, audiencia y emisor incorrectos) y la restricción de invocación directa de Lambda. El rechazo de tokens manipulados y sin token sí se probó (G2); la verificación de firma y emisor la hace el gateway y solo se observó indirectamente.
- **LabRole** es una restricción del laboratorio, **no** una política de mínimo privilegio verificada. Sin despliegue, el SAM crea roles de mínimo privilegio por servicio (ver plantilla). No se afirma que sea apto para producción.
- Revocación: un access token emitido sigue siendo válido hasta `exp`; el logout local no lo revoca. No se analizaron rotación de claves ni tiempos de vida.
- No hay migración de pedidos legacy; esos pedidos quedan inaccesibles por diseño.
- La carrera de cancelación se probó con una simulación determinista, no con concurrencia real contra DynamoDB.
- Los pedidos creados antes de este cambio (clave `IDEMP#<key>` y sin dueño) quedan huérfanos.
- **Proceso de trabajo con la IA (no bloqueante):** las pruebas en la nube (401, 403, 404) se hicieron a mano y la colaboración fue más lenta de lo necesario por la forma de pedir la información: tuve que ejecutar comandos fuera de la consola y copiar tokens, y varias veces copié algo distinto de lo pedido (el texto del comando en lugar del token, o el token sin `Bearer`), con lo que la petición ni llegaba a Orders. Además, al principio la IA no ejecutaba las llamadas con mis tokens hasta que le confirmé que, al ser un laboratorio con usuarios sintéticos, podía hacerlo. Ningún caso fue un bloqueo ni cambió un resultado: se repitieron las pruebas y los resultados de G1–G5 son los de las ejecuciones válidas. Los tokens usados quedaron en la conversación; por eso las contraseñas de los usuarios de prueba se cambian al terminar.

## 8. Uso de IA
- **Qué pedí:** inspeccionar el proyecto, proponer un plan mínimo por pasos (sin editar) y luego implementar: extracción del principal, dueño en lectura/cancelación condicional, idempotencia por principal, authorizer en la plantilla, SDK en el frontend y pruebas.
- **Qué cambié o rechacé:** descarté `NONE` como authorizer de las rutas públicas (SAM solo lo admite con `DefaultAuthorizer`; `cfn-lint` lo detectó) y quedó sin authorizer, sin `DefaultAuthorizer`. Se descartaron verificadores JWT propios.
- **Qué verifiqué yo / ejecuté:** las 36 pruebas, la prueba de mutación, `cfn-lint`, `sam validate --lint`, `sam build` y `node --check`. Además ejecuté a mano G1–G5 y CORS contra la API desplegada. **Sin verificar:** G6, A1–A4 y la restricción de invocación directa de Lambda.
