# Despliegue en AWS (Learner Lab)

Guía de los comandos con los que se desplegó Campus Café y por qué se hace cada paso.

- **Cuenta / rol:** AWS Academy Learner Lab, rol `voclabs`, cuenta `318203795545`
- **Región:** `us-east-1`
- **Stack:** `campus-cafe`
- **Runtime:** Python 3.12 (arm64)

## 0. Prerrequisitos

```bash
aws --version        # AWS CLI 2.x
sam --version        # SAM CLI 1.170.0 o superior
python3 --version    # solo para pruebas locales y seed (boto3)
```

Credenciales del lab: en el Learner Lab, **AWS Details → AWS CLI → Show** y pegar el contenido en `~/.aws/credentials`
(perfil `[default]` con `aws_access_key_id`, `aws_secret_access_key` y `aws_session_token`). La región va en `~/.aws/config`:

```ini
[default]
region = us-east-1
```

> Las credenciales **caducan** al reiniciar o terminar la sesión del lab. Si un comando devuelve `ExpiredToken`
> o `InvalidClientTokenId`, vuelve a copiarlas.

## 1. Comprobar identidad y región

```bash
aws sts get-caller-identity
aws configure list
```

Debe mostrar el rol `voclabs` y la cuenta `318203795545`. Confirmarlo antes de desplegar evita crear recursos en la cuenta equivocada.

## 2. Verificaciones locales (sin AWS)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
scripts/run_unit_tests.sh        # 19 pruebas con moto
.venv/bin/cfn-lint template.yaml
sam validate --lint
```

## 3. Compilar

```bash
sam build
```

Empaqueta `services/catalog/` y `services/orders/` en `.aws-sam/build/`.

## 4. Desplegar

```bash
sam deploy \
  --stack-name campus-cafe \
  --region us-east-1 \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
      FrontendOrigin=http://localhost:8080 \
      ExistingExecutionRoleArn=arn:aws:iam::318203795545:role/LabRole
```

| Opción | Para qué sirve |
|---|---|
| `--resolve-s3` | SAM crea/usa un bucket administrado para subir el código |
| `--capabilities CAPABILITY_IAM` | Autoriza a CloudFormation a gestionar recursos IAM de la plantilla |
| `FrontendOrigin` | Origen exacto permitido por CORS (el frontend servido en local) |
| `ExistingExecutionRoleArn` | Usa `LabRole` para ambas funciones |

### Por qué `ExistingExecutionRoleArn`

El primer `sam deploy` falló porque el Learner Lab **prohíbe `iam:CreateRole`**. La plantilla acepta ese parámetro y,
si se informa, no crea roles propios: usa `LabRole` en las dos funciones.

- Sin el parámetro, la plantilla crea un rol de mínimo privilegio por servicio (diseño original). Ese camino solo se validó con `cfn-lint`/`sam validate`, **no en AWS**.
- Con `LabRole` **no se demuestra aislamiento IAM** entre Catalog y Orders. Hay que declararlo en el README y en la sección de seguridad.

## 5. Obtener las salidas del stack

```bash
aws cloudformation describe-stacks --stack-name campus-cafe \
  --query 'Stacks[0].Outputs' --output table
```

Salidas: `ApiBaseUrl`, `CatalogTableName`, `OrdersTableName`, `OrdersFunctionName` y los nombres de los log groups.

URL de la API desplegada: `https://xp4tl33vtf.execute-api.us-east-1.amazonaws.com`

## 6. Cargar el catálogo (seed)

```bash
python3 scripts/seed.py --stack campus-cafe --region us-east-1
```

Inserta los 5 productos de `scripts/seed_data.json`. Se puede repetir sin duplicar (sobrescribe las mismas claves).

## 7. Probar la API

```bash
API=https://xp4tl33vtf.execute-api.us-east-1.amazonaws.com

# Catálogo
curl -i "$API/products"

# Crear pedido (idempotente)
curl -i -X POST "$API/orders" \
  -H 'content-type: application/json' \
  -H 'Idempotency-Key: demo-001' \
  -d '{"items":[{"productId":"<ID_DE_UN_PRODUCTO>","quantity":2}]}'
# Repetir el mismo comando: 200 con el mismo orderId (replay), no un pedido nuevo.

# Consultar y cancelar
curl -i "$API/orders/<orderId>"
curl -i -X POST "$API/orders/<orderId>/cancel"
```

Resultados verificados: `GET /products` 200, `POST /orders` 201 con `Location`, replay 200 con el mismo `orderId`.

## 8. Redesplegar tras cambios

```bash
sam validate --lint && sam build && sam deploy \
  --stack-name campus-cafe --region us-east-1 --resolve-s3 --capabilities CAPABILITY_IAM \
  --parameter-overrides FrontendOrigin=http://localhost:8080 \
  ExistingExecutionRoleArn=arn:aws:iam::318203795545:role/LabRole
```

> Hay que pasar **todos** los parámetros en cada despliegue. Si omites `ExistingExecutionRoleArn`, el parámetro vuelve a `""` y falla por `iam:CreateRole`.

### Prueba de aislamiento de fallos (Catalog caído)

```bash
# Inyectar fallo: Orders no alcanza Catalog
sam deploy ... --parameter-overrides FrontendOrigin=http://localhost:8080 \
  ExistingExecutionRoleArn=arn:aws:iam::318203795545:role/LabRole \
  CatalogBaseUrlOverride=http://10.255.255.1
# Esperado: POST /orders -> 503 y 0 pedidos creados

# Restaurar (probado): un valor vacío solo se acepta con la sintaxis ParameterKey/ParameterValue;
# "CatalogBaseUrlOverride=" a secas lo rechaza sam deploy.
sam deploy ... --parameter-overrides \
  ParameterKey=FrontendOrigin,ParameterValue=http://localhost:8080 \
  ParameterKey=ExistingExecutionRoleArn,ParameterValue=arn:aws:iam::318203795545:role/LabRole \
  ParameterKey=CatalogBaseUrlOverride,ParameterValue='""'
# Comprobar: aws lambda get-function-configuration --function-name <OrdersFunctionName> --query 'Environment.Variables.CATALOG_BASE_URL'
```

### Cambiar el origen del frontend

Si el frontend se sirve en otro origen, cambiar `FrontendOrigin` y redesplegar (CORS exige coincidencia exacta).

## 9. Observabilidad

```bash
aws logs tail <OrdersLogGroupName> --since 15m --follow
aws logs tail <ApiAccessLogGroupName> --since 15m
```

El `requestId` del access log del API Gateway aparece también en los logs JSON de la aplicación.

## 10. Limpieza

```bash
sam delete --stack-name campus-cafe --region us-east-1
```

Las tablas no tienen `DeletionPolicy: Retain`, así que se borran con el stack. Hacerlo al terminar la entrega.

## Problemas frecuentes

| Síntoma | Causa | Solución |
|---|---|---|
| `ExpiredToken` / `InvalidClientTokenId` | Credenciales del lab caducadas | Copiar de nuevo desde AWS Details |
| `iam:CreateRole` denegado | Falta `ExistingExecutionRoleArn` | Pasarlo en `--parameter-overrides` |
| El navegador bloquea por CORS | Origen distinto de `FrontendOrigin` | Servir en `localhost:8080` o redesplegar con otro origen |
| `AlreadyExists` / stack en `ROLLBACK_COMPLETE` | Despliegue fallido previo | `sam delete --stack-name campus-cafe` y repetir |
