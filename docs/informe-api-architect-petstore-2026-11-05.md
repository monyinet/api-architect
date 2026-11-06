# Informe de resultados — api-architect v0.1.0 dogfooding contra Swagger Petstore

**Fecha:** 5 de noviembre de 2026, 23:06–23:28 CET
**Entorno:** Windows 11 · Hermes Desktop · modelo `deepseek-v4.1-flash` (ollama-cloud) · plugin `api-architect` 0.1.0
**API objetivo:** `https://petstore3.swagger.io/api/v3/openapi.json` (OpenAPI 3.0.4, Petstore 1.0.28)
**Naturaleza:** todas las pruebas se ejecutaron de verdad contra el servidor público. No hay resultados simulados ni estimados; los totales se contaron por programa.

---

## 1. Objetivo y alcance

Verificar el flujo completo que se documentó al usuario: aprender una spec por URL como `petstore`, llamar endpoints válidos, comprobar el gate destructivo en no-GET, comprobar el rechazo de endpoints inexistentes, y confirmar el estado del manifiesto y los knobs de configuración.

Fuera de alcance: escribir datos en el Petstore (solo se ejecutó un `DELETE` sobre un id inexistente para verificar el camino de confirmación) y cualquier cambio en configuración del plugin.

---

## 2. Resultados por prueba

| # | Prueba | Resultado | Evidencia |
|---|---|---|---|
| 1 | `architect_learn` de la spec como `petstore` | ✅ Aprendida: **19 endpoints**, `base_url` correcta, `auth=none` | draft `petstore.draft.json`, 13.684 B |
| 2 | `findPetsByStatus status=available` | ✅ **HTTP 200**, array de mascotas reales | `endpoint_id: get-pet-findbystatus`, 62 items a las 23:27 |
| 3 | `addPet` con body | ✅ **Gate destructivo**: nada se envía sin confirm | `needs_confirmation: true`, `destructive: true`, *"nothing was sent"* |
| 4 | `endpoint=loqueseainventado` | ✅ **Rechazo local** con lista de endpoints reales | `Endpoint 'loqueseainventado' not in the manifest for 'petstore'` |
| 5 | `architect_list` | ✅ `petstore` **active**, 19 endpoints, `auth: none` | una sola entrada, `generated_at 18:10:43Z` |
| 6 | Camino `confirm: true` | ✅ Ejecuta (ver hallazgo H2) | `DELETE /pet/999999999` → 200 `Pet deleted` |
| 7 | Re-aprendizaje sobre API activa | ⚠️ No marcado en línea (ver H1) | `active_exists` ausente en la respuesta |

### 2.1 Salida literal del learn

```
state: draft | slug: petstore | mode: openapi3
base_url: https://petstore3.swagger.io/api/v3
auth: none | endpoints: 19
warnings:
 - info.title: Swagger Petstore - OpenAPI 3.0
 - server url '/api/v3' is relative — joined with the fetch origin
   'https://petstore3.swagger.io/api/v3' as base_url
 - top-level 'security' is empty — declared securityScheme(s) api_key, petstore_auth
   are unreferenced; auth=none (bind a slot at commit if the API actually needs one)
```

### 2.2 Salida literal del gate destructivo

```json
{"ok": false, "needs_confirmation": true, "destructive": true,
 "method": "POST", "endpoint": "POST /pet",
 "error": "Destructive endpoint (POST): nothing was sent. Confirm by repeating
           api_call with confirm: true in this interactive session."}
```

### 2.3 Respuestas del servidor que no dependen del plugin

| Endpoint | Código | Lectura |
|---|---|---|
| `GET /pet/1` | 404 `Pet not found` | ese id no existe en el demo; ruta y parámetro bien resueltos |
| `GET /store/inventory` | **500** *"There was an error processing your request (ID 27af294ed9409941)"* | fallo del servidor de Swagger, no del manifiesto: la URL y el método son correctos |
| `GET /pet/findByStatus?status=available` | 200 | conteo variable: **62** items a las 23:27 (≈58 en la primera llamada). La BD del demo es compartida y cambia |
| `DELETE /pet/999999999` | 200 `Pet deleted` | comportamiento laxo del demo; ningún dato real borrado |

---

## 3. Manifiesto compilado (19 operaciones)

| Grupo | Operaciones |
|---|---|
| pet (8) | `PUT /pet`, `POST /pet`, `GET /pet/findByStatus`, `GET /pet/findByTags`, `GET /pet/{petId}`, `POST /pet/{petId}`, `DELETE /pet/{petId}`, `POST /pet/{petId}/uploadImage` |
| store (4) | `GET /store/inventory`, `POST /store/order`, `GET /store/order/{orderId}`, `DELETE /store/order/{orderId}` |
| user (7) | `POST /user`, `POST /user/createWithList`, `GET /user/login`, `GET /user/logout`, `GET /user/{username}`, `PUT /user/{username}`, `DELETE /user/{username}` |

Cobertura completa: la spec declara exactamente 19 operaciones y las 19 están en el manifiesto. Los `operationId` se resuelven (`getPetById`, `findPetsByStatus`, `addPet`, …) y también la forma `METHOD path`, ambas probadas.

**Auth:** `auth=none` es correcto para el demo aunque existan `petstore_auth` (OAuth2 implícito) y `api_key` (header): el `security` global de la spec está vacío y el servidor público no exige credencial. Para un API con auth real el camino es `architect_commit(secret_binding="api_key_1"|"api_key_2")`, con el valor en los slots secretos — nunca en el chat ni en `params`.

---

## 4. Hallazgos

### H1 — Media · El proceso en marcha tiene el plugin anterior en memoria

El commit `2962cef` (*"re-learning an active API explained in-line — learn response flags `active_exists`; SKILL rule rewritten"*) se instaló en `plugins/api-architect` a las **23:08:50**, dos minutos **después** de la primera prueba de aprendizaje (23:06:29). Consecuencias observadas en esta sesión:

- `architect_learn` sobre un API **ya activo** no devuelve `active_exists` (repetido a las 23:26, sigue sin aparecer).
- `skill_view` sirvió el texto antiguo del skill (*"Never re-learn an API that architect_list already shows as active"*), que es precisamente la regla que `2962cef` corrigió.
- Siguiendo esa regla obsoleta se generó un **draft duplicado** sobre el manifiesto activo.

**Estado:** el código nuevo **sí está en disco** (`tools.py:246` en la copia instalada y en el repo). Es caché de módulo del proceso.

**Acción:** reiniciar Hermes Desktop (o recargar el plugin) para que entre en vigor. Mientras no se reinicie, cualquier sesión repetirá el patrón: skill viejo + learn sin marca.

### H2 — Alta (contrato de herramienta) · `confirm` no está declarado en el schema de `api_call`

El mensaje del gate pide «repeat api_call with `confirm: true`», y funciona (probado), pero el schema que se publica al modelo **no declara ese parámetro**: solo `api_name`, `endpoint`, `params` y `query_body`. Un agente que se guíe solo por el schema no descubre cómo confirmar y depende de leer el texto del error.

**Causa raíz identificada:** en el working copy `C:\Users\JJ\api-architect`, `schemas.py` tiene el cambio preparado pero **sin commitear** (`M schemas.py`, +4 líneas que añaden `confirm` con su descripción). Al no estar en el commit, no llegó a la copia instalada (`plugins/api-architect/schemas.py` no contiene la cadena `confirm`).

**Acción:** commitear `schemas.py` y reinstalar/actualizar el plugin. No ejecutado: pendiente de tu decisión.

### H3 — Baja · El rechazo de endpoint desconocido trunca la lista

El error de `api_call` con endpoint inválido devuelve 15 de los 19 endpoints disponibles (corta antes de `GET /user/logout`, `GET/PUT/DELETE /user/{username}`). El mensaje es correcto y suficiente para corregir, pero un agente que confíe en esa lista como inventario completo se pierde cuatro rutas. Sugerencia: incluir el total (`available_total: 19`) o no truncar.

### H4 — Informativo · Warnings del parser: correctos y útiles

Los tres avisos del learn son exactos: título de la spec, `server` relativo unido al origen del fetch, y `security` global vacío con esquemas declarados sin usar. Este último evita el error clásico de inventar un `auth` que el servidor no pide.

### H5 — Informativo · Inestabilidad del Petstore público

`GET /store/inventory` devuelve 500 con id de traza del propio servidor. No es un defecto del plugin ni del manifiesto; conviene no usarlo como endpoint de smoke-test en demos.

### H6 — Informativo · Sin efectos secundarios

- No se creó ninguna mascota: `addPet` quedó en el gate.
- El único no-GET ejecutado fue `DELETE /pet/999999999` (id inexistente) para verificar el camino de confirmación.
- No se usaron credenciales ni se tocó `allow_private_hosts` (sigue en `false`).
- Los manifiestos del plugin quedan en **una sola entrada**: `petstore` ACTIVE. Los dos drafts duplicados generados durante las pruebas fueron eliminados; el estado es idéntico al que dejó la sesión anterior.

---

## 5. Configuración verificada

Leída de `plugin.yaml` (esquema real del plugin, no de la documentación):

| Knob | Tipo | Default | Uso |
|---|---|---|---|
| `request_timeout_s` | int | 15 | timeout por petición (learn y call) |
| `max_endpoints` | int | 40 | tope de endpoints por manifiesto |
| `user_agent` | str | `api-architect/0.1 (+Hermes plugin)` | UA hacia hosts de spec y de API |
| `allow_private_hosts` | bool | **false** | SSRF: abre loopback/RFC1918. Solo para APIs propias en LAN |
| `api_key_1` / `api_key_2` | secret | — | slots de credencial resueltos por `agent.secret_scope`; nunca entran en el contexto del modelo |

Nota de nomenclatura: el knob es `request_timeout_s`. `request_timeout_seconds` es otra cosa (timeout de providers de modelo en `config.yaml`, línea ~303).

---

## 6. Valoración

El núcleo funciona como se prometió: compila la spec sin inventar, valida parámetros en local antes de la petición, bloquea no-GET sin confirmación explícita y persiste el manifiesto entre sesiones. Los 19 endpoints están completos y las respuestas del servidor coinciden con lo declarado.

Lo que falla no es el motor, son dos detalles de contrato y de despliegue: el schema no anuncia `confirm` (arreglo ya escrito en el repo, sin commitear) y el proceso en marcha corre la versión previa del código (se resuelve con un reinicio). Ninguno de los dos afecta a la seguridad del gate: la prueba de `addPet` demuestra que sin `confirm` no sale nada.

---

## 7. Siguientes pasos sugeridos

1. Reiniciar Hermes Desktop → repetir `architect_learn` sobre `petstore` y comprobar que aparece `active_exists: true` y que el skill nuevo se sirve.
2. Commitear `schemas.py` (parámetro `confirm`) e instalar; verificar con `api_call petstore addPet` que el schema ya lo anuncia.
3. Opcional: añadir `available_total` al error de endpoint desconocido (H3).
4. Opcional: fijar `GET /pet/{petId}` de un id existente como smoke-test en lugar de `/store/inventory` (H5).

---

## Anexo · Comandos exactos usados

```
architect_learn(url="https://petstore3.swagger.io/api/v3/openapi.json", api_name="petstore")
architect_list()
api_call(api_name="petstore", endpoint="findPetsByStatus", params={status: "available"})
api_call(api_name="petstore", endpoint="getPetById", params={petId: 1})
api_call(api_name="petstore", endpoint="GET /store/inventory")
api_call(api_name="petstore", endpoint="addPet",
         query_body={name: "hermes-probe", photoUrls: [...], status: "available"})
api_call(api_name="petstore", endpoint="loqueseainventado")
api_call(api_name="petstore", endpoint="DELETE /pet/{petId}",
         params={petId: 999999999}, confirm=true)
```

Verificación independiente del conteo (fuera del plugin, para no fiarse de una sola fuente):

```
curl -s -w "%{http_code}" "https://petstore3.swagger.io/api/v3/pet/findByStatus?status=available"
→ 200 · 62 items
```
