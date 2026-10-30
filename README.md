# Campus Café

Aplicación serverless de pedidos: navegador → API Gateway (HTTP API, payload 2.0) → dos servicios Lambda en Python 3.12 (Catalog y Orders), cada uno con su propia tabla DynamoDB. Infraestructura con AWS SAM. El estado del trabajo está en [TASKS.md](TASKS.md).

- **Región / cuenta:** `us-east-1`, AWS Academy Learner Lab (rol `voclabs`)
- **Stack:** `campus-cafe`
- **API desplegada:** `https://xp4tl33vtf.execute-api.us-east-1.amazonaws.com`
- **Arquitectura y decisiones:** [docs/architecture.md](docs/architecture.md) · [docs/decisions.md](docs/decisions.md)
- **Despliegue paso a paso:** [DEPLOY_AWS.md](DEPLOY_AWS.md)

```
campus-cafe/
├── openapi.yaml          contrato HTTP público
├── template.yaml         plantilla SAM (único camino de despliegue)
├── services/catalog/     Lambda Catalog (dueña de la tabla Catalog)
├── services/orders/      Lambda Orders (dueña de la tabla Orders; llama a Catalog por el gateway)
├── frontend/             HTML/CSS/JS sin dependencias
├── scripts/              seed.py, seed_data.json, acceptance.py, perf.py, run_unit_tests.sh
├── tests/                pruebas unitarias (moto)
├── docs/                 diagrama y decisiones
└── evidence/             salidas reales de las comprobaciones
```

## 1. Requisitos
AWS CLI 2.x, SAM CLI 1.170+, Python 3 con `venv`, credenciales del Learner Lab en `~/.aws/credentials` (caducan al reiniciar el lab). No hay credenciales en el código.

## 2. Comprobaciones locales (sin AWS)
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
scripts/run_unit_tests.sh            # 19 pruebas (4 Catalog + 15 Orders) con DynamoDB simulado (moto)
.venv/bin/cfn-lint template.yaml
sam validate --lint
```

## 3. Despliegue
```bash
aws sts get-caller-identity          # confirmar cuenta y región antes de desplegar
sam build
sam deploy --stack-name campus-cafe --region us-east-1 --resolve-s3 --capabilities CAPABILITY_IAM \
  --parameter-overrides FrontendOrigin=http://localhost:8080 \
  ExistingExecutionRoleArn=arn:aws:iam::318203795545:role/LabRole
python3 scripts/seed.py --stack campus-cafe --region us-east-1     # repetible: carga 5 productos
```
`ExistingExecutionRoleArn` es necesario en el Learner Lab porque deniega `iam:CreateRole`. Fuera del lab, omítelo y la plantilla crea un rol de mínimo privilegio por servicio. Detalles y problemas frecuentes en [DEPLOY_AWS.md](DEPLOY_AWS.md).

## 4. Frontend
```bash
cd frontend && python3 -m http.server 8080
```
Abrir `http://localhost:8080`. El puerto es obligatorio: CORS solo permite ese origen. La URL de la API está en `frontend/config.js` y se puede cambiar desde la cabecera de la página (se guarda en el navegador). Si sirves el frontend en otro origen, redespliega con `FrontendOrigin=<origen>`.

## 5. Verificación contra la API desplegada
```bash
FN=$(aws cloudformation describe-stacks --stack-name campus-cafe \
  --query 'Stacks[0].Outputs[?OutputKey==`OrdersFunctionName`].OutputValue' --output text)
python3 scripts/acceptance.py --api https://xp4tl33vtf.execute-api.us-east-1.amazonaws.com --orders-function "$FN"
python3 scripts/perf.py --api https://xp4tl33vtf.execute-api.us-east-1.amazonaws.com --catalog-function <CatalogFunctionName>
```
`acceptance.py` y `perf.py` modifican la *descripción* de la función Lambda (para forzar nuevos entornos de ejecución); no cambian el comportamiento.

## 6. Resultados medidos (ver `evidence/`)

| Comprobación | Resultado | Archivo |
|---|---|---|
| Aceptación (positivas, negativas, precio del cliente ignorado, cancelación) | 26/26 | `evidence/acceptance.txt` |
| 5 peticiones concurrentes con la misma clave | un solo pedido (cuatro 200 y un 201) | `evidence/acceptance.txt` |
| Persistencia tras cambiar la configuración de la Lambda | mismo pedido y mismo replay | `evidence/acceptance.txt` |
| Rendimiento `GET /products` (20 lecturas en caliente, desde local) | mediana 124 ms, p95 161 ms | `evidence/performance.txt` |
| Cold start | 2229 ms medidos desde el cliente; `Init Duration` 369 ms y `Duration` 1269 ms en el log de Lambda | `evidence/performance.txt` |
| Aislamiento de fallos (Catalog inalcanzable) | 503, 0 pedidos nuevos, Catalog sigue en 200 | `evidence/fault-isolation.txt` |
| Observabilidad | mismo `requestId` en la cabecera, el access log y el log de Orders | `evidence/observability.txt` |
| Seguridad | revisión de solo lectura, riesgos declarados | `evidence/security.md` |

## 7. Secuencia de demostración
1. Abrir `http://localhost:8080`: el menú se carga desde la API (el Muffin sale como no disponible).
2. Agregar productos y enviar: el total lo calcula el servidor a partir de los precios de Catalog.
3. Recargar la página: el pedido reaparece desde `localStorage` + `GET /orders/{id}`.
4. Reintentar la creación original (mismo `Idempotency-Key` y cuerpo con `curl`): 200 con el mismo `orderId`.
5. Misma clave con otro cuerpo → 409 `IDEMPOTENCY_KEY_CONFLICT`; producto `muffin` → 409 `PRODUCT_UNAVAILABLE`.
6. Cancelar dos veces: ambas devuelven `CANCELLED` (una sola transición persistida).
7. Mostrar el `requestId` en el access log y en el log de Orders, y la comprobación concurrente (`acceptance.py`).

Los pasos 4 a 7 se pueden ejecutar por API con un solo comando (crea un pedido nuevo en cada ejecución):
```bash
scripts/demo.sh
```
Muestra el replay (mismo `orderId`), los dos 409, la doble cancelación (mismo `updatedAt`), los 5 envíos concurrentes (cuatro 200 y un 201) y el `requestId` en el access log y en el log de Orders. El access log puede tardar hasta un minuto en entregarse. Los pasos 1 a 3 se hacen en el navegador. La salida del script incluye la IP real del cliente; no la pegues sin enmascarar en la evidencia.

## 8. Limpieza
```bash
sam delete --stack-name campus-cafe --region us-east-1
```
Las tablas no tienen `DeletionPolicy: Retain`: se borran con el stack. Hacerlo solo después de entregar.

## 9. Qué NO se verificó
- **Aislamiento IAM entre servicios:** ambas funciones usan `LabRole`. Los roles de mínimo privilegio de la plantilla solo se validaron de forma estática (`cfn-lint`, `sam validate`); nunca se desplegaron.
- **Contenido de las políticas del lab** asociadas a `LabRole`: `iam:GetPolicy` está denegado.
- **Frontend en el navegador:** se comprobaron manualmente la carga del menú, la creación de pedidos, la cancelación, el reintento tras un fallo y la búsqueda por ID (tras corregir un defecto inicial que mostraba todos los pedidos). Una captura muestra los pedidos guardados recuperados con su estado real. No se probó en otros navegadores ni en pantallas pequeñas.
- **Rendimiento:** una sola ejecución de 20 lecturas desde una máquina local en Colombia; el p95 es indicativo. Cold start medido una vez. No se hicieron pruebas de carga.
- **Plazo de Catalog:** el segundo pedido fallido tardó 3,0 s en el servidor, pero el primero 4,1 s (por encima del plazo de 3 s) sin causa verificada. Solo se probó un timeout por IP sin ruta, no un rechazo inmediato de conexión.
- **Correlación Orders → Catalog:** el `requestId` no se propaga a la llamada interna; no se puede seguir una petición entre los dos servicios por ID.
- **Seguridad de la API:** sin autenticación ni límite de peticiones propios (decisión de alcance); no se probó abuso.
- **Python:** las pruebas unitarias corren en Python 3.9 con moto; la Lambda usa 3.12. Moto prueba la lógica, no latencia ni cold start. El código se probó en 3.12 solo por el despliegue y la aceptación reales.
- **Claves de idempotencia:** no caducan (sin TTL).
- **Limpieza (`sam delete`):** documentada, no ejecutada.
- **Evidencia corregida:** un commit intermedio de `evidence/fault-isolation.txt` afirmó por error que el despliegue estaba restaurado; la restauración inicial falló y se repitió. Ver el historial de git y el archivo.
