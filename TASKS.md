# Campus Café: lista de tareas

**Entrega:** viernes 30-oct-2026, 23:59 · **Stack:** Python 3.12 + AWS SAM · **Última actualización:** 2026-11-02 (ACT2 añadida en la sección 7)

Leyenda: `[x]` hecho y verificado · `[~]` escrito, falta verificar contra AWS · `[ ]` pendiente · 👤 lo debe hacer una persona (requiere su cuenta/decisión)

## 0. Preparación (👤 antes de desplegar)
- [x] Acceso al sandbox: AWS Academy Learner Lab, rol `voclabs`, región `us-east-1`, cuenta 318203795545
- [x] AWS CLI 2.39.2 instalado
- [x] SAM CLI 1.170.0 instalado
- [x] `aws sts get-caller-identity` funciona (las credenciales del lab caducan: renovar al reiniciar el lab)
- [x] `python3.12` desplegó sin error en `us-east-1`
- [x] 👤 Trabajo individual (sin reparto con pareja); confirmar con el profesor si algo no está claro

## 1. Diseño y contrato
- [x] Estructura del monorepo
- [x] `openapi.yaml` (5 operaciones, errores, esquemas): valida con `openapi-spec-validator`
- [x] Modelo de datos: tabla Catalog (`productId`) y tabla Orders (`pk` = `ORDER#id` / `IDEMP#key`)
- [ ] 👤 Poder explicar: límites de servicio e idempotencia atómica (checkpoint de los primeros 15 min)

## 2. Backend (Python)
- [x] Catalog: `GET /products`, `GET /products/{id}`; 503 si falla el almacén
- [x] Orders: crear, consultar, cancelar
- [x] Validación (1–10 líneas, cantidad 1–10, sin duplicados, bool/float rechazados)
- [x] Total y snapshot de precios calculados en el servidor
- [x] Idempotencia atómica (`TransactWriteItems` con condición `attribute_not_exists`), fingerprint, replay, 409
- [x] Llamada a Catalog por el gateway con deadline (3 s) y 1 reintento seguro; timeout ⇒ 503 sin pedido
- [x] Logs JSON con `requestId`, operación, resultado, duración y `orderId`
- [x] Pruebas unitarias con DynamoDB simulado (moto): **19 pasan** (`scripts/run_unit_tests.sh`)
  - Nota: moto prueba la lógica, no reemplaza la evidencia contra AWS real

## 3. Infraestructura (SAM)
- [x] `template.yaml`: HttpApi (payload 2.0), 2 funciones, 2 tablas, roles mínimos por servicio, log groups con retención, access log, CORS, outputs. `cfn-lint` hoy reporta E3031 y 2×W1028 por el parámetro opcional `ExistingExecutionRoleArn` con valor por defecto vacío (ver `evidence/local-checks.txt`); `sam validate --lint` sí es válido y el stack despliega
- [x] `sam validate --lint`: válida
- [x] `sam build`: correcto
- [x] Stack `campus-cafe` desplegado (ver comando y nota sobre `LabRole` abajo)
- [x] Seed: 5 productos cargados
- [x] Probado en AWS real: `GET /products` 200, `POST /orders` 201 con `Location`, replay 200 con el mismo orderId
  - API: https://xp4tl33vtf.execute-api.us-east-1.amazonaws.com

## 4. Frontend
- [x] Menú (con producto no disponible marcado)
- [x] Carrito + envío con `Idempotency-Key` (se genera al empezar el envío, se reutiliza en reintentos) (creación y reintento tras fallo probados en navegador)
- [x] Confirmación y limpiar el carrito solo tras éxito
- [x] IDs de pedido en `localStorage`; recuperar tras recargar; campo para buscar por ID (verificado en navegador con captura)
- [x] Cancelar pedido (probado en navegador)
- [x] URL de la API configurable; servir en `localhost:8080` (coincide con CORS)

## 5. Evidencias (deben ser salidas reales, guardadas en `evidence/`)
- [x] Script de aceptación contra la API desplegada (`scripts/acceptance.py`) → `evidence/acceptance.txt` (26/26)
- [x] Corrección: pruebas positivas/negativas + intento de sobrescribir precio → `evidence/acceptance.txt` (26/26)
- [x] Fiabilidad: 5 peticiones concurrentes ⇒ 1 pedido; replay tras `update-function-configuration` → `evidence/acceptance.txt` (26/26)
- [x] Persistencia: leer el mismo pedido tras cambio de configuración de la Lambda → `evidence/acceptance.txt` (26/26)
- [x] Aislamiento de fallos: desplegar con `CatalogBaseUrlOverride` inválido ⇒ 503 y 0 pedidos → `evidence/fault-isolation.txt`
- [x] Rendimiento: 3 de calentamiento + 20 lecturas secuenciales; tiempos, p95, región, runtime, cold start aparte → `evidence/performance.txt` (p95 161 ms; cold start 2229 ms cliente / Init 369 ms)
- [x] Seguridad: revisar políticas IAM y que el navegador no tiene credenciales → `evidence/security.md`
- [x] Observabilidad: seguir un pedido del access log al log de la aplicación (mismo `requestId`) → `evidence/observability.txt`

## 6. Documentación y entrega
- [x] README (regiones, URLs, comandos, resultados, demo y limpieza)
- [x] Diagrama de arquitectura breve (`docs/`) → `docs/architecture.md`
- [x] Dos decisiones: límites de servicio/datos e idempotencia atómica (`docs/decisions.md`) → `docs/decisions.md`
- [x] Anotar lo que **no** se verificó → README sección 9
- [~] Ensayar la demo (7 pasos): `scripts/demo.sh` ensayado por API (pasos 4-7); faltan pasos 1-3 en navegador. Repositorio subido

## 7. ACT2: Diseño de aplicaciones cloud seguras (pista LOCAL)
**Entrega:** viernes 6-nov-2026, 20:59 · **Pista:** local + verificación parcial en vivo (tenant Auth0 propio, stack redesplegado el 2026-11-04) · Detalle en [docs/security.md](docs/security.md)

### Hecho y verificado en local (moto): 36 pruebas pasan (4 Catalog + 32 Orders)
- [x] Línea base: 19 pruebas pasaban antes de cambiar nada; 3 casos de abuso mapeados a su frontera
- [x] `services/orders/auth.py`: principal `(iss, sub)` solo desde `requestContext.authorizer.jwt.claims`; falla cerrada (401); `EXPECTED_ISSUER` opcional
- [x] Creación: dueño derivado en servidor; campos `owner*`/`customerId` ⇒ 400 `FORBIDDEN_FIELD`
- [x] Lectura: solo el dueño; inexistente, ajeno y legacy ⇒ el mismo 404 `Order not found`
- [x] Cancelación: `ConditionExpression` con dueño **y** estado; sin backfill de pedidos legacy
- [x] Idempotencia: clave `IDEMP#sha256([iss, sub, key])`, replay/409 por principal, inserción condicional atómica conservada
- [x] Garantías de ACT1 conservadas (precios del servidor, transacción, 503 sin pedido parcial, `requestId`, tablas separadas)
- [x] Prueba de mutación: al quitar dueño/estado de la condición de cancelación fallan 5 pruebas
- [x] `template.yaml`: authorizer JWT nativo solo en las 3 rutas de Orders (un scope por ruta), parámetros `Auth0IssuerUrl`/`Auth0Audience`, CORS con `authorization`; `cfn-lint` sin hallazgos nuevos, `sam validate --lint` y `sam build` correctos
- [x] Frontend: SDK Auth0 (v2.1.3 + SRI), PKCE, access token en `Authorization`, pedidos por usuario (solo `node --check` y revisión de código)
- [x] Documentación: `docs/security.md` (rutas→scopes, esquema, decision record, matriz, política staff/catálogo, limitaciones, nota de IA), README sección 6, `evidence/security-local-checks.txt`

### BLOCKED / NOT RUN (se declara, no se inventa)
- [x] G1–G5 y CORS contra la API desplegada: PASS (`evidence/security-live-checks.md`)
- [ ] 👤 G6 (ID token en lugar del access token): NOT RUN
- [ ] 👤 A1–A4 (token expirado, `nbf`, audiencia, emisor): requieren tokens firmados y authorizer desplegado
- [x] Login real de Alice y Bob en navegador y CORS/preflight desplegado
- [ ] 👤 Restricción de invocación directa de Lambda (no verificada)
- [ ] 👤 Revisar y reescribir con tus palabras la nota de uso de IA y las limitaciones de `docs/security.md`

### Pendiente antes de entregar
- [x] Guion de demo (Tarea 7) en `docs/demo.md` (comandos locales verificados)
- [ ] 👤 Ensayar la demo (pasos 1-2 en navegador con Alice y Bob)
- [ ] Decidir si se adapta `scripts/acceptance.py` (llama a Orders sin token: no sirve contra una API protegida) o se deja documentado
- [ ] Entregar por el canal del curso, sin credenciales ni evidencia privada del lab

## Despliegue en el Learner Lab: desviación a documentar
El primer `sam deploy` falló: el lab prohíbe `iam:CreateRole`. La plantilla ahora acepta `ExistingExecutionRoleArn`; el despliegue se hizo con `LabRole` para ambas funciones:
```bash
sam deploy --stack-name campus-cafe --region us-east-1 --resolve-s3 --capabilities CAPABILITY_IAM \
  --parameter-overrides FrontendOrigin=http://localhost:8080 \
  ExistingExecutionRoleArn=arn:aws:iam::318203795545:role/LabRole
```
- Sin ese parámetro la plantilla crea un rol de mínimo privilegio por servicio (diseño original). **Ese camino no se pudo probar en AWS** aquí; solo `cfn-lint`/`sam validate`.
- Con `LabRole` **no se demuestra** aislamiento IAM entre servicios: declararlo en el README y en la sección de seguridad.

> Nota ACT2: el stack desplegado sigue siendo la versión de ACT1 (sin authorizer). Un redespliegue de ACT2 exige además `Auth0IssuerUrl` y `Auth0Audience` (ver README sección 6); los pedidos previos quedan sin dueño y devuelven 404 por diseño.

## Pendiente a vigilar
- Las pruebas locales corren en Python 3.9 con moto; la Lambda usará 3.12. El código evita sintaxis nueva, pero la prueba real es el despliegue.
- `moto` no mide cold start ni latencia real; esas cifras solo valen medidas en AWS.
- Limpieza al final: `sam delete`. Las tablas no tienen `DeletionPolicy: Retain`, así que se borran con el stack.
