# Guion de demo ACT2 (≈6-7 min)

Orden del handout (Tarea 7). Cada paso indica **qué mostrar**, **qué decir** y **qué evidencia** respalda lo que se ve. Se declara qué es en vivo (API desplegada) y qué es local (pruebas con moto).

## Antes de empezar (checklist)
- [ ] Learner Lab activo y `aws sts get-caller-identity` responde.
- [ ] `curl -s -o /dev/null -w "%{http_code}\n" https://xp4tl33vtf.execute-api.us-east-1.amazonaws.com/products` imprime `200`.
- [ ] `cd frontend && python3 -m http.server 8080` corriendo (mismo puerto que Auth0 y CORS).
- [ ] Dos ventanas de navegador separadas (una normal y una de incógnito): **Alice** y **Bob**, sin mezclar sesiones.
- [ ] Terminal con `scripts/`-adyacente lista: `cd campus-cafe`.
- [ ] Cerrados: el archivo `credenciales`, DevTools con el header `Authorization` visible y cualquier captura con tokens.

## 1. Catálogo público y pedido de Alice (EN VIVO, ~1 min)
- **Mostrar:** sin iniciar sesión se ve el menú; el botón de enviar está deshabilitado. Iniciar sesión como Alice, agregar un producto, enviar.
- **Decir:** el catálogo es público por decisión documentada (datos sintéticos). Los pedidos exigen access token con audiencia de la API; el login usa el SDK oficial de Auth0 con Authorization Code + PKCE. Nunca hay client secret en el navegador.
- **Evidencia:** G1, G2, G3 en `evidence/security-live-checks.md`.
- Anotar el `orderId` de Alice (`ALICE_ID`).

## 2. Lectura de Alice y Bob denegado (EN VIVO, ~1.5 min)
- **Mostrar:** Alice lee su pedido (aparece en "Mis pedidos"). En la ventana de Bob, buscar `ALICE_ID` → "Pedido no encontrado".
- **Mostrar (opcional, terminal):** `evidence/security-live-checks.md`, filas G4: Bob lee y cancela el pedido de Alice con 404 uniforme; el pedido de Alice sigue en `CREATED` sin tocar.
- **Decir:** un token válido con `orders:read` no autoriza *este* pedido. Orders compara el dueño guardado `(iss, sub)` con el principal que sacó del contexto verificado por el gateway. Pedido ajeno, inexistente y legacy sin dueño dan el mismo 404, sin payload ni dueño.
- **Evidencia:** G4 (en vivo) y S2, S3 (local).

## 3. Replay del mismo principal y conflicto por payload distinto (LOCAL, ~1 min)
La interfaz genera una clave aleatoria por envío, así que esto se demuestra con pruebas.
```bash
.venv/bin/python -m unittest discover -s tests/orders -k test_s5 -v
```
- **Mostrar:** `test_s5_replay_then_changed_payload_conflicts` y `test_s5_equivalent_json_canonicalization` en `ok`.
- **Decir:** misma clave y mismo request devuelve el pedido original (con su snapshot de precios); mismo principal y clave con otro request devuelve 409 y no crea un segundo pedido. El hash canónico ordena las líneas por `productId` e ignora totales y precios.
- **Etiqueta:** local, DynamoDB simulado (moto); no es evidencia de la nube.

## 4. Pedido independiente de Bob con la misma clave (LOCAL, ~45 s)
```bash
.venv/bin/python -m unittest discover -s tests/orders -k test_s6 -k test_idempotency_key_encoding -v
```
- **Mostrar:** `test_s6_bob_same_client_key_gets_independent_order` y `test_idempotency_key_encoding_has_no_collisions` en `ok`.
- **Decir:** la clave de idempotencia es `sha256([iss, sub, clientKey])`: otro principal con la misma clave tiene un espacio independiente y nunca recibe la respuesta de Alice.

## 5. Una prueba de fiabilidad y una limitación (≈2 min)
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

## Cierre (15 s)
Autoevaluación del handout: *¿puedo señalar evidencia de que Bob, con identidad y permisos válidos, no puede exponer ni alterar el pedido de Alice, ni siquiera mediante un reintento?* Sí: lectura y cancelación cruzadas en vivo (G4), reintento y misma clave con otro principal en pruebas locales (S5, S6, S7). Lo que no se probó en la nube se declaró arriba.

## Plan B si algo falla
- **El lab expiró o `/products` da 500:** renovar el lab y las credenciales; mientras tanto, mostrar `evidence/security-live-checks.md` y ejecutar solo los pasos 3-5 (locales).
- **El login falla:** revisar URLs en Auth0 (`http://localhost:8080` exacto) y el puerto del servidor.
- **No usar** tokens pegados en el chat ni en pantalla; no mostrar el archivo `credenciales`.
