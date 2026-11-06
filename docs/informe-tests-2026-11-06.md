# Informe de tests — api-architect · 6-nov-2026

Ejecutado en este PC (Windows 11, Python 3.14.9 del entorno de Hermes), sobre el repo
`C:/Users/JJ/api-architect`, HEAD `82b054f`, rama `main` sincronizada con `origin/main`.

## 1. Resultado

| | |
|---|---|
| Comando | `python -m unittest discover tests -v` |
| Tests | **248** |
| Fallos / errores / skips | **0 / 0 / 0** |
| Resultado | **OK** |
| Tiempo | **0,501 s** (reloj 0,73 s) |

Ningún test se ha saltado en esta máquina. Los tests son de librería estándar
(`unittest`), sin `pytest` y sin dependencias Python — coherente con la regla
`python_dependencies: none` del plugin.

## 2. Desglose por módulo

| Módulo | Tests | Qué cubre |
|---|---:|---|
| `test_parse_spec.py` | 45 | OpenAPI 3: paths, parámetros, `$ref`, bodies, operation_id, warnings, procedencia |
| `test_netguard.py` | 38 | Guard de red: loopback/RFC1918/link-local/ULA, formas numéricas, decimales, hex, IPv6 mapeadas, 6to4, NAT64, redirects, caps de bytes y timeout |
| `test_redlens_fixes.py` | 38 | Camino real de los handlers: draft→commit→call, gate de `confirm`, SSRF, secretos, swagger2/postman por URL |
| `test_parse_swagger2.py` | 36 | Swagger 2.0: parámetros heredados, overrides, enums, formdata/cookie con aviso agrupado |
| `test_redactor.py` | 31 | Redacción de credenciales en URLs/cabeceras/cuerpos (patrones, userinfo, query, fragmentos) |
| `test_parse_postman.py` | 28 | Postman v2.1: colecciones, variables, auth de colección, baseUrl |
| `test_secrets_bridge.py` | 20 | Puente de secretos: slots, `secret_scope`, nunca el valor en el manifiesto |
| `test_digest.py` | 10 | Contrato del digest y del manifiesto (validación, roundtrip, procedencia obligatoria) |
| `test_handlers.py` | 2 | Humo del stack de handlers |
| **Total** | **248** | |

## 3. Gates del plugin

- `hermes plugins validate` → **15 checks verdes**, incluidos manifest, schema de config,
  `requires_env`, loadable, sondeo de capacidad (`register()` en aislamiento), colisiones de
  tools, escaneo de seguridad y «no core override».
- `hermes plugins doctor --ci` → **OK**: descubrimiento, parseo, import y registro; **4 tools,
  0 hooks**.

## 4. Estado de publicación

- PR **#149624** abierto en `NousResearch/hermes-agent` (rama `plugin-catalog-entry`,
  actualizado 2026-11-05T23:54Z): un solo fichero, `plugin-catalog/api-architect.yaml`.
- El **pin del catálogo es `82b054f`**, el mismo HEAD que se ha testeado aquí, y su texto declara
  «248 stdlib tests». Pin, repo y suite coinciden.
- El clon que carga Hermes (`AppData/Local/hermes/plugins/api-architect`) está en `82b054f`, sin
  cambios locales: no hay deriva entre lo desarrollado y lo que ejecuta el agente.

## 5. Qué NO prueba esta suite (honestidad sobre el alcance)

1. **No hay tests de red reales.** El guard y los fetches van con mocks; el E2E en vivo
   (petstore3 `200`, 49 mascotas, gate de `confirm`) se verificó el **5-nov** y **no se ha
   re-ejecutado hoy**. Este informe es de regresión offline, no de integración.
2. **8 de los 248 tests dependen de que el núcleo de Hermes sea importable.** Medido: bloqueando
   `hermes_cli`, la suite da `Ran 248 · OK (skipped=8)`. Los otros **240 son herméticos**. Aquí
   pasan los 248 porque el host los cubre; en una máquina sin Hermes se saltarían 8 sin avisar más
   allá del `skipped`.
3. **Rutas de LLM y de aprobación interactiva** se ejercitan con dobles; el comportamiento real
   del gateway (aprobación no interactiva, `confirm` string vs booleano) está cubierto por lógica,
   no por sesión real.

## 6. Observaciones

- `docs/` e `IDEA.md` siguen **sin seguimiento en git**: el informe del dogfooding del 5-nov y
  este informe no están versionados. Si el repo va a revisarse en el hackathon, conviene
  commitearlos (o dejarlos fuera a propósito y decirlo).
- `test_handlers.py` tiene solo 2 tests porque los caminos de handler se movieron a
  `test_redlens_fixes.py` (38). No es un hueco, pero el nombre del módulo engaña a quien lea solo
  el recuento.
- Único cambio entre el pin anterior (`fe3a195`) y el actual (`82b054f`): `assets/hero.png`
  (binario) + docs. Código y tests idénticos, así que la re-pin no cambia comportamiento.
