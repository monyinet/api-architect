# Decisión Red/Blue: pivote del API-Architect a plugin de catálogo (Hermes Plugin Hackathon)

**Fecha:** 5-nov-2026. **Evaluador:** consolidador (coordinador del proyecto).
**Evidence:** [red-report.md](evidence/red-report.md) (8.246 palabras, con corrección incorporada) · [blue-report.md](evidence/blue-report.md) (5.769 palabras, con corrección incorporada).

---

## Decisión enmarcada

Cómo replantear (o no) el plan del API-Architect a la luz del reglamento confirmado del hackathon. Candidatos:

- **A — plugin completo** en un único repo público de JJ (`plugin.yaml` v2 + `register(ctx)`, tools canónicas `architect_learn`/`api_call`, ctx.llm interno, skill empaquetada, submit al catálogo).
- **B — meta-skill** (plan original, sin plugin).
- **C — híbrido** (motor en skill separada + plugin fino).

## TL;DR

1. **El plan se pivota a PLUGIN (candidato A).** La entrada ES el plugin (texto literal del tweet); el jurado favorece explícitamente plugins que hacen más que un prompt o una skill. B es inelegible (premio 0) y C colapsa en A: el PR del catálogo pinnea **un** SHA de **un** repo, ninguna pieza puede descargarse en runtime (regla 3), y vendorizar la skill dentro del repo… es literalmente A.
2. **La Fase 3 original (codegen: skill aprendida / servidor MCP generado) muere tal cual y por diseño:** la regla 7 (`dangerous` en el scan falla la entrada) y la regla 9 (declive de PR, no fix) la declaran declinable. Sustituto: **intérprete declarativo** — el manifest aprendido es un documento de datos con provenance; `api_call` valida params y ejecuta; el dinamismo se mueve del registro de tools al **dato persistente** (`plugin_data_dir()`). El plugin registra 2 tools estáticas para siempre.
3. **La Fase 2 se reencarna en `ctx.llm.complete_structured`** (credenciales del host, trust gate fail-closed, salida tipada con json_schema + retry si `parsed is None`): esta superficie — imposible de replicar con un prompt o una skill — es la respuesta demostrable al criterio del jurado.
4. **El riesgo #1 que reportó Red (pin-maturity ≥2 semanas) era una cita fabricada** — regla derogada el 15-sep-2026 según el comentario del propio catálogo (`hermes-anilist.yaml`). Hoy **no existe pin-age mínimo**. El riesgo de admisión vigente es la mecánica de revisión (checks los inicia un mantenedor a mano; revisión "every few days"), lo que empuja el PR lo antes posible.
5. **Reloj medido: 165,13 h hasta el 11-nov 23:00 CET** (desde 01:52 del 5-nov). Plan: PR del catálogo abierto el lunes 9 (antes si `validate` verde), form en cuanto el PR esté abierto, vídeo adelantado en cuanto E2E funcione.

## Verificación de afirmaciones entre equipos (consolidador)

| Afirmación | Red | Blue | Verificación propia | Resolución |
|---|---|---|---|---|
| Pin pydantic del core | 2.14.1 (main, py≥3.14) | 2.13.4 (local 0.22.5) | Leído pyproject main hoy [medido]: `pydantic==2.14.1; py≥3.14`, `httpx[socks]==0.28.1`, `requests==2.34.2`, `ruamel.yaml==0.18.16`, exclude-newer 3d | Ambos medidos de snapshots distintos; rangos que intersecuten ambos: `pydantic>=2.13,<3.0`, `httpx>=0.27,<0.29`; no depender de `requests` |
| Regla pin-maturity ≥2 semanas | Afirmada (cita) | — | 3 greps independientes sin coincidencia + `hermes-anilist.yaml`: «There is no minimum pin age… replaced upstream… (2026-09-15)» [medido] | **Fabricada/regla derogada. No existe hoy.** Riesgo invalidado |
| Adyacentes mcp-plugin-creator / hermes-plugin-gen / openspec | — | Nombres listados | Grep del log completo del escaneo del catálogo: 0 coincidencias [medido] | **No existen.** Adyacentes reales: unbrowse, monid, lib-docs, priors, familia jp- |
| Nº de entradas del catálogo | ~1,17K [estimado] | 763 ficheros yaml [medido, checkout 0.22.5] | Listing API no parseable limpio [estimado] | Cosmético: ≥760; no bloquea nada |
| Release actual upstream | v0.22.7 (3-nov) | — | GitHub API `/releases/latest` → v0.22.7 (2026-11-03T21:53Z) [medido] | Local 0.22.5 con update pendiente → **decidir día 1: actualizar local antes de desarrollar** |
| Horas hasta deadline | 6 d 21 h | 165,13 h | Coinciden [medido, zoneinfo] | 11-nov 23:00 CET |
| unbrowse como adyacente | Verificado su yaml | Verificado `plugins info` | — | Adjacency real; diferencia estructural: `api-architect` compila specs tipadas y llama local vs unbrowse en su nube + hook que bloquea browser/web_extract del core |

## Top riesgos (re-ranqueados tras la corrección)

1. **Review humano no llega antes del deadline** (checks los inicia manualmente un mantenedor; revisión cada pocos días) — Alta × Media → PR+form lo antes posible; vídeo adelantado; nunca force-push tras el form.
2. **Codegen (Fase 3 tal cual) → declive R7/R9** — Alta × Media → cortar por diseño: intérprete declarativo, cero `exec`/`eval`/`importlib` de código generado.
3. **Objeción del jurado «prompt con curl» / «unbrowse redux»** — Alta × Media → pitch *"compiles, not reads"* + demo de manifest persistente entre sesiones + transcript sin la clave.
4. **SSRF / exfiltración de secrets** (learn y call aceptan hosts arbitrarios) — Alta × Media → choke-point único con deny-list de rangos privados + resolve-then-pin + redirects no seguidos por defecto + `allow_private_hosts:false` por defecto + secret_ref-only + redactor único de salidas + destructive decidido por método HTTP (no por flags del spec) + commit-gate para drafts + unattended = refuse limpio.
5. **Deps vs pins del core / `requires_hermes`** — Media × Alta → 0 dependencias nuevas (stdlib `urllib.request`), o como máximo httpx+pydantic con floor+bound intersecutando 0.22.5 y main; `requires_hermes` idéntico en plugin.yaml y en el yaml del catálogo; floor `>=0.22.0` [estimado], techo absoluto 0.22.7 [medido].
6. **Multiplataforma** (Windows encodings, Python 3.11–3.13, NixOS sin wheels) — Media × Media → pathlib + `encoding="utf-8"` explícito, código 3.11-compatible, `plugin_data_dir()` para todo estado, doctor con HERMES_HOME temporal.
7. **Scope creep** — Media × Alta (histórico) → freeze list el día 1; HTML profundo, GraphQL, OAuth, MCP-generado y multipart: FUERA del MVP.

## Matriz de decisión

| Dimensión | A — plugin | B — meta-skill | C — híbrido |
|---|---|---|---|
| Coste [estimado] | 53,5 h brutas / ~45 h recortado | 26–34 h | 60–63 h |
| Llegar a tiempo | Medio-bajo (cabe con cortes + buffer día 11) | Bajo (irrelevante) | Medio-alto (el overhead consume el buffer) |
| Declive en catálogo | Bajo (estático, declarativo, sin deps) | N/A | Medio-alto si 2 repos (pin único → plugin roto en la máquina del reviewer) |
| Wow del demo | Alto: URL viva → tool aprendida → llamada real + persistencia + ctx.llm | Bajo (es una skill: criterio del jurado en su contra) | Alto (mismo plugin) |
| Fiabilidad post-install | Alta (arranca sin claves, sin wheels, fail-clean) | N/A | Media (dos fuentes de verdad) |
| Reusabilidad post-hackathon | Alta (catálogo + motor propio) | Solo skill personal | ≈A |
| **Veredicto** | **RECOMENDADO** | **INELEGIBLE** (la entrada es el plugin → premio 0) | **COLAPSA EN A** |

## Plan v2 — sustituciones concretas (delta sobre el plan original)

| # | Plan original | Plan v2 | Por qué (evidencia) |
|---|---|---|---|
| 1 | Meta-Skill | Plugin `api-architect`, repo público único (github.com/monyinet/…) | La entrada es el plugin; pin único; regla 5 |
| 2 | Fase 2: LLM improvisa el manifiesto en chat | `ctx.llm.complete_structured` con json_schema del manifest + retry | Doc plugin-llm-access; credenciales host; fail-closed |
| 3 | Fase 3: skill aprendida / MCP generado | Manifest JSON persistido con provenance en `plugin_data_dir()` + 2 tools canónicas estáticas + guidance skill empaquetada (`ctx.register_skill`) | Reglas 3/6/9: registra una vez, el dinamismo vive en el dato |
| 4 | Secreto: env vars directas | `config_schema` tipo `secret` + `agent.secret_scope.get_secret` (jamás `requires_env` para claves: el formato simple deshabilita el plugin si falta) | Regla 11 + doc plugin.yaml v2 |
| 5 | Fetch/call sin guard | Choke-point único: deny-list privado + resolve-then-pin + sin redirects + timeout + size cap + opt-in `allow_private_hosts:false` | Reglas 11/12/13; reviewer humano |
| 6 | Sin clasificación de destructive | Destructive **por método HTTP** (no-GET exige approval; unattended refuse); flags del spec informativos, nunca autorizantes | El spec puede mentir (vector V6) |
| 7 | HTML: extracción completa de endpoints | Descubridor de enlace de spec (≤1 fetch); sin spec → `endpoints=[]` + mensaje honesto | Provenance no fiable en HTML; 8–10 h de coste por riesgo impredecible |
| 8 | Cortes: — | FUERA: GraphQL, OAuth, multipart/streaming, MCP-server-generado (deuda documentada), HTML profundo | Cada corte elimina lo que el gate castiga o el demo no muestra fiable |
| 9 | — | `capabilities:` ninguna declarada; hooks/slash/CLI: no | Cada surface declarada = un check más que chocar |
| 10 | — | Disclosure regla 13 arriba en README y PR (red a hosts aprendidos, sin telemetría); jamás force-push tras el form | Regla 13; sha congelado |

**KPIs re-basados:** los «>80% éxito / <30 s» originales dejan de ser KPI del juez y se vuelven la narrativa del demo; lo que el juez premia es usefulness/reliability/polish/creativity → el corte que define el mensaje del demo es la **fiabilidad visible** (un «no hay spec, no invento endpoints» vale más que un scraper que a veces acierta).

## Calendario (consenso de ambos equipos)

| Día | Bloques | DoD |
|---|---|---|
| **Jue 5 (hoy)** | Repo + scaffold `plugin.yaml`/`register(ctx)` + `validate --install-deps` verde; Digest v1 congelado;/fixtures Petstore+Postman echo; decidir update local a 0.22.7 | Validate local verde |
| **Vie 6** | `parse_spec` OpenAPI 3 JSON → Digest con provenance + tests offline | Petstore → Digest completo |
| **Sáb 7** | `ctx.llm` → manifest + `architect_learn` + persistencia. **Regla de aborto:** sin round-trip al cierre → cortes de emergencia (plan ~45 h) | URL Petstore → manifest persistido |
| **Dom 8** | `api_call` + guard de red + secretos + redactor; E2E real con OpenWeather (key propia) | learn → call sin errores |
| **Lun 9** | Skill + README disclosure + doble pase `validate --install-deps --json` y `doctor --ci` (2º sin claves, simulando máquina virgen) + **ABRIR PR del catálogo** (yaml con SHA 40-hex + disclosure) | PR abierto, form listo |
| **Mar 10** | Vídeo <90 s si no se adelantó + post en X + Typeform | 3 enlaces fuera |
| **Mié 11** | Buffer; fixes de review; SHA-bump en el mismo PR; **cierre 23:00 CET** | Nada crítico hoy |

**Guion vídeo (75 s, Red):** (1) `hermes plugins validate` verde 10 s → (2) `architect_learn` de OpenWeather con key dummy vía secret_scope 25 s → (3) param inválido rechazado en local + llamada válida 20 s → (4) transcript sin la clave 10 s → (5) sesión nueva, llamada sin re-aprender 10 s. Ese último plano responde «no es un prompt, no es una skill, no es unbrowse».

## Recomendación con condiciones y fallbacks

**Candidato A con el corte MVP (~45 h).** Fallbacks ya decididos:

- `ctx.llm` falla el sáb 7 → autoría manual del manifest (el jurado no ve cómo se generó); no bloquea demo.
- PR sin checks antes del deadline → la entrada sigue válida (form válido desde PR abierto); riesgo residuo aceptado y comunicado.
- Reviewer pide cambios → SHA-bump en el mismo PR mientras no esté mergeado; jamás re-pinnear a un commit sin revisar.

## Lo que sigue sin verificar (deduplicado de ambos equipos)

- Mecánica exacta de juzgado si el merge llega tras el deadline (form válido desde PR abierto; el resto no es textual).
- Comportamiento runtime real de `ctx.llm` (no ejecutable sin el candidato).
- macOS/Linux/NixOS sin probar — mitigado por stdlib-first; `doctor --ci` por SSH opcional.
- Campos exactos del Typeform (página JS; respuesta HTTP 200 [medido]).
- Si un SHA-bump pre-merge cabe en el mismo PR (la regla 4 solo especifica el post-merge).
- Pins del core para Python 3.11–3.13 (solo leídos los de 3.14) → contrastar antes del PR.
- Grep local de enforcement de `requires_hermes` inconcluso (layout del paquete).

## Decisión que queda en manos de JJ (única pregunta abierta)

¿Apruebas el **pivote a plugin (candidato A)** con el corte MVP declarado?

- **Aprobado con cortes** (recomendado): ~45 h, máxima probabilidad de salir; sacrificio: HTML profundo, GraphQL, OAuth y MCP-demo fuera de la entrada.
- **Aprobado sin cortar:** 53,5–64 h [estimado]; riesgo de calendario medio; ninguna pieza del corte vuelve al plan.
- **No pivotar:** seguir como skill = entrada inelegible, premio 0; el motor seguiría siendo útil como proyecto personal fuera del hackathon.
