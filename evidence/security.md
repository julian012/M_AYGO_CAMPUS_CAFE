# Seguridad: revisión (2026-10-30)

Comandos de solo lectura contra la cuenta 318203795545 (`us-east-1`) y contra el código del frontend.

## Hallazgos verificados

| # | Comprobación | Resultado |
|---|---|---|
| 1 | Rol de ejecución de las dos Lambdas | Ambas usan `arn:aws:iam::318203795545:role/LabRole` (el mismo rol). |
| 2 | Aislamiento IAM entre servicios | **No se demuestra.** Con un rol compartido, Catalog podría escribir en Orders y viceversa. `LabRole` tiene 7 políticas administradas (SSM, EKS, ECR, 3 del lab); no se pudo leer el contenido de las del lab (`iam:GetPolicy` denegado por política explícita `Pvoclabs1`). |
| 3 | Camino de mínimo privilegio de `template.yaml` | Escrito (Catalog: `GetItem`,`Scan` sobre su tabla; Orders: `GetItem`,`PutItem`,`UpdateItem` sobre la suya), válido por `cfn-lint`/`sam validate`, **nunca desplegado** en AWS. |
| 4 | Variables de entorno | Solo nombres de tabla y tiempos. Sin secretos ni credenciales. |
| 5 | Credenciales en el navegador | `grep` de `AKIA|ASIA|secret|token|aws_access|sessiontoken` en `frontend/`: sin coincidencias. El frontend solo llama a la API HTTP; no usa SDK de AWS. |
| 6 | XSS en el frontend | Sin `innerHTML` ni `eval` en `app.js`; el contenido se inserta con nodos de texto. |
| 7 | Autenticación de la API | Las 5 rutas tienen `AuthorizationType: NONE`: la API es pública y sin autenticación (decisión de alcance; hay un comentario en la plantilla para añadir JWT). Cualquiera con la URL puede crear y **cancelar** pedidos si conoce el `orderId` (UUID, no adivinable, pero no hay dueño). |
| 8 | CORS | Solo `http://localhost:8080`, métodos `GET,POST,OPTIONS`, cabeceras `content-type,idempotency-key`. Verificado con un preflight real (204). CORS protege al navegador, **no** a un cliente como `curl`. |
| 9 | Permisos de invocación Lambda | Solo `apigateway.amazonaws.com`, con `SourceArn` limitado a las rutas de esta API. Sin Function URL pública. |
| 10 | Límite de peticiones (throttling) | No configurado en el stage (`DefaultRouteSettings` solo con `DetailedMetricsEnabled: false`): rigen los límites por defecto de la cuenta. Sin protección propia frente a abuso. |
| 11 | DynamoDB | `PAY_PER_REQUEST`. `SSEDescription` vacío = cifrado en reposo con clave propiedad de AWS (por defecto), sin CMK propia. |
| 12 | Registro de datos personales | Los logs guardan `requestId`, operación, resultado, estado, duración y `orderId`; el access log incluye la IP de origen. No se guarda el cuerpo del pedido. |
| 13 | Secretos en el repositorio | `grep` de `AKIA`/`ASIA[A-Z0-9]{12}` en `campus-cafe/` (sin `.venv` ni `.aws-sam`): sin coincidencias. |

## Riesgos a corregir o declarar

1. `../labsuser.pem` (clave privada) está en la carpeta del taller, fuera de `campus-cafe/`, y no se sube al repositorio. Solo comprobé que existe; no leí su contenido.
2. `evidence/observability.txt` incluía la IP pública del cliente; está enmascarada como `190.145.x.x`.
3. Las credenciales temporales del lab están en `~/.aws/credentials` (fuera del repo, caducan con el lab).

## Lo que no se verificó
- El contenido de las políticas del lab asociadas a `LabRole`.
- El despliegue con roles de mínimo privilegio (solo validación estática).
- Pruebas de abuso o carga contra la API; no se probó el comportamiento ante el límite de peticiones.
- Revisión de dependencias (`boto3` viene del runtime de Lambda; no se auditó).
