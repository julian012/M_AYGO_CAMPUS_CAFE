# Decisiones de diseño

## Decisión 1: límites de servicio y de datos

**Contexto.** La aplicación tiene dos capacidades distintas: publicar el menú (Catalog) y gestionar pedidos (Orders).

**Decisión.**
- Dos servicios, cada uno con su Lambda y **su propia tabla DynamoDB**. Solo Catalog accede a la tabla Catalog; solo Orders accede a la tabla Orders.
- Orders no lee la tabla Catalog. Pide los productos a Catalog **por HTTP, a través del mismo API Gateway**, con un plazo total de 3 s (1,5 s por intento) y 1 reintento.
- Orders copia nombre y precio del producto en el pedido (*snapshot*). Cambiar el precio en Catalog después no altera pedidos ya creados.
- El precio y el total los calcula siempre el servidor; el cliente no los controla.
- Una Lambda puede atender varias rutas de su servicio: la Lambda es una unidad de despliegue, el límite del servicio lo marcan los datos y la responsabilidad.

**Por qué.** Cada servicio puede cambiar su modelo de datos sin romper al otro. Si Catalog falla, Orders lo detecta y responde 503 en lugar de crear un pedido con datos dudosos. Con tablas separadas el fallo de un almacén no afecta al otro.

**Costes.** Una llamada HTTP extra por cada línea del pedido (más latencia, `POST /orders` ≈ 290 ms medidos en servidor, ver `evidence/observability.txt`) y un nuevo modo de fallo (Catalog caído). Se acepta a cambio del aislamiento.

**Qué falta demostrar.** El aislamiento *IAM* entre servicios. Con `LabRole` ambas funciones podrían tocar ambas tablas. La plantilla define roles de mínimo privilegio, pero no se pudieron desplegar en el Learner Lab (`iam:CreateRole` denegado). El aislamiento de fallos se prueba con `CatalogBaseUrlOverride` inválido (pendiente de ejecutar).

## Decisión 2: idempotencia atómica

**Problema.** Si el cliente reintenta un `POST /orders` (timeout, doble clic, red caída), no debe crearse un segundo pedido. Y si dos peticiones idénticas llegan a la vez, solo una debe crearlo.

**Decisión.**
- El cliente genera una `Idempotency-Key` al empezar el envío y la reutiliza solo en reintentos de ese mismo envío.
- En la tabla Orders se guardan, con `pk` distinto, dos ítems: `ORDER#<orderId>` (el pedido) e `IDEMP#<key>` (fingerprint de la petición normalizada, `orderId` y la respuesta original).
- Ambos se escriben en **una sola `TransactWriteItems`**, y cada `Put` lleva la condición `attribute_not_exists(pk)`. O se escriben los dos o ninguno.
- Antes de llamar a Catalog se busca `IDEMP#<key>` (lectura consistente). Si existe: mismo fingerprint → se devuelve la respuesta original (200) **sin volver a calcular precios**; distinto → 409 `IDEMPOTENCY_KEY_CONFLICT`.
- Si dos peticiones concurrentes pasan la comprobación inicial, la condición hace fallar la transacción de una de ellas (`TransactionCanceledException` con `ConditionalCheckFailed`). La perdedora vuelve a leer el registro y responde como replay.

**Por qué no otra cosa.**
- Un diccionario en memoria no sirve: cada invocación Lambda puede ir a un entorno distinto y se pierde al reiniciar.
- Dos escrituras separadas (primero el pedido, luego la clave) dejarían una ventana: si la ejecución se detiene entre ambas, habría un pedido sin clave y un reintento crearía un duplicado, o una clave sin pedido.

**Qué pasa si la ejecución se detiene en mitad de la operación.**
- Antes de la transacción: no se escribió nada; el reintento con la misma clave crea el pedido normalmente.
- Durante la transacción: DynamoDB garantiza todo o nada.
- Después de la transacción pero antes de responder: el pedido existe; el reintento con la misma clave devuelve el original (200).

**Evidencia real (AWS).** `evidence/acceptance.txt`: 5 peticiones concurrentes con la misma clave dieron cuatro 200 y un 201, con un solo `orderId`; el replay tras `update-function-configuration` devolvió el mismo pedido.

**Límites conocidos.**
- El replay devuelve la respuesta *original* de la creación (estado `CREATED`) aunque el pedido se haya cancelado después; para el estado actual se usa `GET /orders/{id}`.
- Las claves de idempotencia no caducan (no hay TTL): crecen con el uso.
- Si Catalog falla antes de la transacción, no se guarda nada, por lo que el reintento vuelve a consultar Catalog.
