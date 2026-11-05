# Informe BLUE TEAM — viabilidad, coste y orden de ejecución
## API-Architect para el Hermes Agent Plugin Hackathon (submisión deadline 11-nov-2026, 23:00 CET)

Redactado: jueves 5 de noviembre de 2026, ~01:55 CET. Método: solo medidas read-only del entorno local (date, hermes CLI, grep/lecturas del checkout git local de hermes-agent v0.22.5, un curl GET público a fxtwitter). Nada instalado, nada commiteado, el candidato no existe todavía.

**Veredicto en una línea: Candidato A (repo único de plugin `api-architect`, con la modularidad interna que C proponía como disciplina de código, no como segundo artefacto), con el corte MVP de la sección 4. Candidato B es inadmisible para este hackathon y Candidato C colapsa estructuralmente en A por la mecánica del pin del catálogo.**

---

## 0. Estado del entorno, medido esta sesión

| # | Hecho | Etiqueta |
|---|---|---|
| H1 | Fecha/hora local al medir: 2026-11-05 01:52:41 CET (UTC+1); epoch `1793839901` | [medido] |
| H2 | Deadline de submisión: **2026-11-11 23:00:00 +01:00 en Madrid** = 17:00 −05:00 en New York = 22:00 UTC (epoch `1794434400`). El 11-nov ambos husos ya están en hora estándar: el DST de EE. UU. termina el 1-nov-2026 y el de la UE el 25-oct-2026 | [medido] (zoneinfo de Python 3.14.9 + `date -u -d`) |
| H3 | Tiempo restante hasta el deadline al medir: **165,13 h ≈ 6 días 21 horas** | [medido] |
| H4 | Nota de método: `TZ=America/New_York` de MSYS/git-bash no resolvió la zona (usaba UTC), así que la conversión se hizo con epoch UTC explícito + `zoneinfo` (Python). El resultado cruza con el valor del contexto compartido (23:00 Madrid) | [medido] |
| H5 | Hermes Agent v0.22.5 (2026.9.24), canal stable, instalación git, commit local `bf0203a794a0`; Python 3.14.9 (`MSC v.1944`); OpenAI SDK 2.24.0; `Update available` (la instalación va por detrás de la rama) | [medido] |
| H6 | `hermes plugins validate` existe con `--install-deps` (documentado como «the catalog CI gate») y `--json`; `hermes plugins doctor` existe con `--ci` | [medido] |
| H7 | `hermes plugins info unbrowse`: tier community, v12.1.0, SHA pinneado `a82c59df…`, subida `plugins/hermes`, **24 tools, 1 hook (`pre_tool_call`), 1 env (`UNBROWSE_API_KEY`)**. Confirma las cifras del contexto compartido | [medido] |
| H8 | Catálogo local (`plugin-catalog/` en el checkout): **763 ficheros yaml** + README. Grep por contenido `openapi`: 4 yaml (`agentplaybooks-tools`, `govee`, `hermes-tripo`, `switchbot-control` — menciones en descripciones, no plugins de aprendizaje de APIs). Sin ninguna entrada llamada `api-architect`, `spec_to_tool` ni `api_call` | [medido] |
| H9 | PyPI del core (`pyproject.toml` del checkout): `requires-python ">=3.11,<3.15"`, 46 dependencias exactas: `pydantic==2.13.4`, `ruamel.yaml==0.18.16` (no PyYAML), `httpx==0.28.1`, `requests==2.33.0`, `openai==2.24.0`, `fastapi>=0.104,<1`, etc. | [medido] |
| H10 | Tweet oficial (vía API pública de fxtwitter, texto completo 2.068 caracteres): hackathon «runs for 7 days», creado Wed Nov 04 21:33 UTC 2026 (791 likes, 59 replies); premios 1º $10k, 2º $5k, 3º $2.5k, 4º $1.5k, 5º $1k, y 5 × runners-up con merch + $250 creds; «Submissions are due 5PM EST on Nov 11th»; «Your entry is the plugin itself»; jueces valoran usefulness, reliability, polish, creativity y «favor plugins that do more than a prompt or a skill could»; «Keep the demo under 90 seconds and show the plugin working»; «You can submit the form as soon as your pull request is open»; «The catalog checks run once a maintainer starts them, and we review entries every few days» | [repo] |
| H11 | Superficies del manifest (`plugin.yaml`) confirmadas en los docs del checkout (developer-guide/plugins/index.md): `name`, `version`, `manifest_version` (2), `api_version`, `description`, `provides_tools`, `provides_hooks`, `requires_env` (formato simple: *plugin disabled si falta la clave*; formato rich con description/url/secret), `config_schema` (JSON-schema-ish con tipos `str/int/float/bool/list/dict/secret`, validado al cargar — mismatch = warning accionable, **nunca** fallo de carga; alimenta el form de settings del Desktop), `python_dependencies`, `capabilities` (`tools.override`, `llm.model_override` — consentimiento explícito, fail-closed si no se declara), `oauth`, `requires_auth` | [repo] |
| H12 | Registro: `register(ctx)` con `ctx.register_tool`, `ctx.register_skill` (skills de plugin **read-only, namespaced `plugin:skill`, opt-in — NO aparecen en el índice `<available_skills>` del system prompt**), `plugin_data_dir(name)` y `plugin_db(name)` (estado que sobrevive a `update`/`remove`, por perfil), slash commands y CLI subcommands registrables. Los handlers de tools devuelven **strings JSON** (devolver un dict es el error nº1 documentado) | [repo] |
| H13 | `ctx.llm` (developer-guide/plugin-llm-access.md): `complete()` y `complete_structured(instructions, input, json_schema, purpose, temperature, max_tokens)` (+ variantes async); credenciales del host (el plugin nunca ve el token), trust gate fail-closed («un plugin sin configurar no puede elegir provider, modelo ni credencial»), llamada única acotada sin bucles de tools, auditoría en `result.audit`; `result.parsed` es `None` si el JSON no valida | [repo] |
| H14 | Reglas 1–17 de admisión del catálogo leídas verbatim en el README local, incluyendo la regla 10 (los reviewers piden floor con upper bound; floors desnudos generan petición de cambio) y el hechogate exacto: «`hermes plugins validate --install-deps` is what CI runs» | [repo] |

---

## 1. Tabla de esfuerzo por candidato y destino de las 4 fases

Todas las horas de esta sección son juicio de un dev solo sobre Hermes 0.22.5 con las superficies verificadas arriba: **[estimado]**. Lo estructural (qué se admite y qué no) es [repo].

### 1.1 Candidato A — plugin completo en repo propio (el de la recomendación)

| Bloque | Horas | Etiqueta | Nota |
|---|---|---|---|
| A1. Scaffold: repo público, `plugin.yaml` v2, `__init__.py` con `register(ctx)` vacío, `validate --install-deps` verde el día 1 | 3 | [estimado] | Probar el pipeline de admisión desde la primera hora |
| A2. `spec_sniff`: sonda en orden (openapi.json, swagger.json, /v3/api-docs, colección Postman) + descubridor de link de spec en HTML acotado (≤1 fetch, regex de href) | 3 | [estimado] | HTML **acotado**: solo descubre el enlace a la spec, no extrae endpoints |
| A3. `parse_spec`: OpenAPI 3.x JSON → Digest (paths, métodos, params, securitySchemes, provenance por endpoint) | 9 | [estimado] | El bloque más caro; sin headers/responses detallados |
| A4. OpenAPI 2 (swagger) + Postman v2.1 → mismo Digest | 4 | [estimado] | Marginal: comparten el target Digest; **primero en cortar si falta tiempo** |
| A5. Paso `ctx.llm`: `complete_structured` Digest→manifest + validación contra schema + retry con error | 5 | [estimado] | `result.parsed is None` → error accionable (H13) |
| A6. Tool canónica `architect_learn`: orquesta A2–A5, persiste manifest en `plugin_data_dir()` | 4 | [estimado] | El "dinamismo" vive como dato, no como registro (ver 1.4) |
| A7. Tool canónica `api_call`: executor GET/POST/PUT/DELETE con params, auth (bearer/header/query-key), timeout, salida JSON | 6 | [estimado] | Sin multipart/binario en v0 |
| A8. Skill empaquetada (`skills/api-architect-usage/SKILL.md` + `ctx.register_skill`) | 3 | [estimado] | Enseña el flujo; read-only y opt-in (H12) |
| A9. `requires_env` + `config_schema` + fail-clean sin claves | 1.5 | [estimado] | Decisión de diseño en §4.1: la key va por `config_schema` tipo `secret`, no por `requires_env` |
| A10. Tests: fixtures de specs (Petstore, Postman echo) offline + 2 APIs reales (Petstore, OpenWeather) | 5 | [estimado] | La tercera API real es backup |
| A11. Bucle `validate --install-deps` + `doctor --ci` en Windows + fixes | 3 | [estimado] | Gate exacto de CI (H6, H14) |
| A12. README con disclosure (regla 13) + yaml de entrada del catálogo (schema H11/H14) | 2.5 | [estimado] | |
| A13. Vídeo <90 s (3 escenas) + post en X con @NousResearch | 3 | [estimado] | Requisito literal del tweet (H10) |
| A14. Typeform + vigilar checks del PR | 1 | [estimado] | Form enviabel en cuanto el PR está abierto (H10) |
| **Total bruto** | **53,5** | [estimado] | Con buffer 20% → ~64 h |
| **Total tras cortes de emergencia (§4.2)** | **~45** | [estimado] | A4 fuera (−4), A10 a 2 APIs (−2), A8 reducido (−1,5), A7 sin auth query-key (−1) |

### 1.2 Candidato B — meta-skill (sin plugin, sin catálogo)

| Bloque | Horas | Etiqueta |
|---|---|---|
| Repo simple + `SKILL.md` principal | 2 | [estimado] |
| A2–A4 como scripts Python llamables por el agente vía terminal | 16 | [estimado] |
| Paso LLM: **0 h de código** — el host ya es el LLM; solo 2 h diseñando el prompt dentro del SKILL.md | 2 | [estimado] |
| Docs del flujo + tests de scripts | 6 | [estimado] |
| Manifest/validate/doctor/PR del catálogo/vídeo/Typeform | 0 — **no aplican: sin plugin PR, la entrada no existe** | [repo H10] |
| **Total** | **~26–34 h** | [estimado] |

Conclusión B: es el candidato más barato **y no se puede presentar**. El tweet es explícito: «Your entry is the plugin itself» y la submisión es un PR al catálogo (H10). Hasta 34 h invertidas aquí son 100% coste hundido para el hackathon.

### 1.3 Candidato C — skill reutilizable aparte + plugin fino

Coste = A (53,5 h) + overhead de doble artefacto (empaquetado cruzado, dos superficies que testear, sincronización de cambios): **+6 a +10 h → 60–63 h [estimado]**.

Pero hay un hecho estructural que lo disuelve: **el PR del catálogo pinnea un único SHA de un único repo** (regla 2: «exact SHA pins are mandatory»; la instalación clona ese commit exacto), y ninguna pieza puede descargarse en runtime (regla 3: sin self-updating). Si la skill vive en otro repo, el clon del SHA no la contiene y el plugin del reviewer queda roto; si se *vende* (vendoriza) dentro del repo del plugin, ya no es un artefacto separado — **eso es literalmente el Candidato A**. C solo añade coste de coordinación sin añadir ningún valor medible sobre A. La versión honesta de C es: *modularidad interna de A* — módulo de ingesta, módulo de manifest, módulo de executor, skill empaquetada — todo en un repo.

### 1.4 Qué sobrevive de las 4 fases del plan actual

| Fase del plan original | A (plugin) | B (skill) | Transformación y por qué |
|---|---|---|---|
| **Fase 1 — Motor de ingesta determinista** (`spec_sniff`, `parse_spec`, `digest_schema`, `fetch_docs`) | Sobrevive ~90% como módulos del plugin | Sobrevive como scripts de skill | `fetch_docs.py` (HTML→endpoints completo) **se corta** a "descubridor de link de spec". Justificación: el contrato Digest exige provenance por endpoint y esa procedencia no existe de forma fiable en HTML; coste de extraerla fiable ~8–10 h [estimado] contra valor demo bajo y riesgo impredecible en vídeo. El anti-alucinado del MVP es literalmente "si no hay spec, endpoints=[] y provenance lo dice". |
| **Fase 2 — Cerebro del arquitecto (LLM)** | Sobrevive; se reencarna en `ctx.llm.complete_structured` | Sobrevive; el prompt vive en el SKILL.md y lo ejecuta el host | En A/C esto es el distintivo "más que una skill" del jurado (H10, H13): credenciales del host, gate fail-closed, `json_schema` de salida tipada `result.parsed`. |
| **Fase 3 — Registro dinámico** (skill aprendida y/o servidor MCP generado) | **No sobrevive como estaba.** Se convierte en: manifest aprendido persistido + 2 tools canónicas estáticas + skill empaquetada | Sobrevive parcialmente (el host puede escribir skills a demandar, fuera del gate) | Hecho estructural: las reglas 3 (sin self-updating), 6 (capabilities declaradas == registradas, «undeclared capability creep is treated as a security issue») y 9 (extender solo por superficies públicas `register_*`) hacen que un plugin que registra tools nuevas después de aprender una API sea declive de PR garantizado: `hermes plugins validate` falla el check «declared tools» cuando lo registrado no coincide con lo declarado, y el doctor reporta «drift between declared and registered» (H14, H6, H12). Además el cacheo de prompt del core prohibe cambiar tools mid-conversation. El dinamismo válido **se mueve del registro (código) al dato (manifest JSON con provenance en `plugin_data_dir()`)**, que es exactamente lo que el gate admite. |
| **Fase 4 — Testing con 3 APIs + vídeo** | Sobrevive íntegra | Sobrevive | Demo: Petstore (spec JSON pura), Postman echo (colección), OpenWeather (API key real). La API HTML-only entra como escena de honestidad ("descubre el link, no alucina endpoints"), no como feature. |

El servidor MCP generado (rincón de la Fase 3 original) es **deuda técnica declarada, no demo**: Hermes ya trae MCP config-driven, un server generado exige un proceso largo disclosado (regla 13) que falle limpio bajo cron (regla 12), y no entrega nada que `api_call` no entregue. Mitigación barata: persistir el manifest ya en forma exportable (JSON con `base_url`/auth/endpoints) para que un export MCP posterior sea trivial.

---

## 2. Plan día a día desde hoy (5-nov) hasta el deadline

Base [medido]: H2–H3. El plazo se cierra el **miércoles 11-nov a las 23:00 CET**. Hay 165,13 h de reloj ≈ 6 jornadas plenas (8–9 h) + parcial de hoy [estimado de capacidad].

Un hecho del calendario que ordena todo (H10, [repo]): **«The catalog checks run once a maintainer starts them, and we review entries every few days»** y **el Typeform puede enviarse en cuanto el PR está abierto**. Los checks del PR NO corren automáticamente al abrir: dependen de un mantenedor con cadencia de días. Por tanto: abrir el PR lo antes posible con `validate --install-deps` corrido localmente (es el gate exacto que correrá el CI, H14), y no esperar a los checks para grabar vídeo ni para enviar el form.

| Día | Bloques (DoD = definition of done) | Horas |
|---|---|---|
| **Jue 5 (hoy)** | Congelar Digest v1 + elegir las 3 APIs demo + A1 completo: repo, `plugin.yaml`, `register(ctx)` vacío, **validate verde hoy mismo**. Preparar fixtures de specs offline. | 4–5 |
| **Vie 6** | A3 `parse_spec` OpenAPI 3 JSON → Digest con provenance + tests offline Petstore. DoD: `python -m pytest` verde. | 7–8 |
| **Sáb 7** | A5 paso `ctx.llm` (schema del manifest + retry) + A6 `architect_learn` + persistencia en `plugin_data_dir()`. DoD: una URL de Petstore produce un manifest persistido con params y provenance. Si hay holgura: A4 (Postman/OpenAPI2). | 7–8 |
| **Dom 8** | A7 `api_call` (auth bearer/header/query-key, timeouts) + A9 requires/env-config + **primera llamada real OpenWeather con clave propia de JJ**. DoD: `architect_learn` → `api_call` end-to-end sin errores. | 7–8 |
| **Lun 9** | A8 skill + A12 README+disclosure + A10 tests restantes + **A11 dos pases limpios de `validate --install-deps --json` y `doctor --ci`** (uno simulando máquina virgen sin claves) + **ABRIR EL PR DEL CATÁLIGO por la tarde-noche CET** con el yaml pinneando el SHA completo de 40 hex. Opcional: 1 h corriendo `doctor --ci` sobre un Linux por SSH si está disponible. | 6–8 |
| **Mar 10** | Vigilar el PR + responder cualquier review temprano. **A13: grabar vídeo <90 s, post en X con tag @NousResearch, A14 Typeform con ambos enlaces.** El vídeo no depende del PR: puede adelantarse a lun-9 si end-to-end ya funciona (recomendado — deja día completo de buffer para re-grabación). | 4–6 |
| **Mié 11 (buffer)** | Fixes de review (si el mantenedor arrancó checks), SHA-bump en el mismo PR si tocó código, re-grabación si el demo mejoró, última pasada de lectura del Typeform. **Cierre: 23:00 CET**. Nada crítico debe quedar programado para este día. | buffer ~8 |

**Paralelización posible (un solo dev = intercalado):**
- El script del vídeo y el guion de las 3 escenas se escriben cualquiera de los días 1–4 (no dependen de código).
- Skill (A8), README y disclosure (A12) se escriben mientras el PR espera checks.
- Los fixtures de specs de A10 se preparan el día 1 junto al scaffold.
- **Secuencial obligatorio:** scaffold → parser → tools; PR abierto → checks de mantenedor → fixes; vídeo → post X → Typeform. El post X depende del vídeo; el Typeform depende del PR abierto («submit the form as soon as your pull request is open», H10).

**Regla de aborto del calendario:** si el sábado 7 al final del día no hay round-trip `ctx.llm` funcionando, se ejecutan los cortes de emergencia (A4 fuera, A8 reducido, A10 a 2 APIs): dejan el plan en ~45 h y el deadline sigue alcanzable.

---

## 3. Diseño de MVP: superficies del plugin y cortes

### 3.1 Superficies — qué usar y qué no

| Superficie | ¿MVP? | Justificación (hechos) |
|---|---|---|
| **2 tools estáticas** (`architect_learn`, `api_call`) | SÍ | Son las únicas que el gate admite estáticamente (H14 regla 6). Todo el dinamismo vive como dato en `plugin_data_dir()` (H12). Handlers devuelven strings JSON (H12). |
| **`ctx.llm` (structured)** | SÍ | Es EL diferenciador frente a una skill: credenciales del host, gate fail-closed, salida tipada `json_schema`/`result.parsed` (H13). Pregunta de jurado directa: «¿hace más que un prompt o una skill?» (H10) — esta superficie es la respuesta. No requiere capability declarada en su postura por defecto (H13: fail-closed sobre provider/modelo, no sobre el uso). |
| **Skill empaquetada** (1 SKILL.md via `ctx.register_skill`) | SÍ, reducida | Es barata (3 h) y muestra la superficie empaquetada del plugin. Caveat operativo para el demo (H12): las skills de plugin son **read-only, namespaced y opt-in** — NO aparecen en el índice del system prompt; el demo debe llamar `skill_view("api-architect:api-architect-usage")` explícitamente. |
| **`requires_env`** | NO declarar claves en v0 | Dato duro (H11): el formato simple **deshabilita el plugin si la env falta** → en la máquina del reviewer sin la key de OpenWeather, el plugin ni cargaría. En su lugar: `config_schema` con **tipo `secret`** para `OPENWEATHER_API_KEY` (H11: mismatch = warning, jamás fallo de carga), y `api_call`/`architect_learn` devuelven error accionable si la clave no está. Claves ausentes no rompen el arranque: requisito de la sección 5 cumplido por diseño. Trade-off asumido: se pierde el prompt de instalación de la key; se compensa en README con una captura del form de settings del Desktop. |
| **`config_schema`** | SÍ, 3 claves | `request_timeout_s` (int, default 15), `max_endpoints` (int, default 40), `user_agent` (str). [estimado los defaults] Todos con default y nada `required` → cero fricción de arranque. Alimenta el form del Desktop (H11): detalle de polish gratis. |
| **`capabilities:`** | NO declarar ninguna | `tools.override`/`llm.model_override` exigen consent extra, fail-closed si no se declaran (H11) y no hacen falta para el demo. Cada surface declarada = una cosa más que validate/scan/reviewer pueden chocar. |
| **Hooks, middleware, slash commands, CLI subcommands** | NO | Ningún evento, ninguna UI y ningún subcomando es necesario para el flujo del demo; cada superficie extra añade checks de validate y disclosure sin valor de jurado. |
| **Servidor MCP generado** | NO (deuda) | Razonamiento en 1.4: reglas 12/13 y el hecho de que Hermes ya trae MCP config-driven; `api_call` cubre el valor. Declararlo como roadmap en el README. |
| `manifest_version: 2`, `requires_hermes` | SÍ | Manifest v2 es additive y los campos desconocidos se ignoran (H11). `requires_hermes: ">=0.22.0"` [estimado]: la regla 14 exige floor SemVer nunca más nuevo que la release actual, y la release estable con la que todo el mundo (incluidos reviewers) corre es la 0.22.x (H5: JJ está en 0.22.5 aunque con update pendiente). **Constraint derivada grave:** no usar ninguna API que exista solo en main — los docs leídos esta sesión (checkout 0.22.5) son exactamente el contrato seguro (H5). |

### 3.2 Cortes MVP vs stretch (cada uno con justificación)

| Corte | Decisión | Justificación |
|---|---|---|
| OpenAPI 3.x JSON | **MVP núcleo** | Es el 60% de las REST APIs documentadas y el caso demo más limpio (Petstore). |
| OpenAPI 2 / Postman v2.1 | **Motor sí, demo solo si sobra escena** | Mismo target Digest → coste marginal 4 h [estimado]; robustez ante el reviewer; primero en cortar bajo presión. |
| HTML profundo (extraer endpoints de páginas) | **FUERA — solo descubridor de link de spec** | Ver 1.4: provenance imposible de fiar; ~8–10 h [estimado] con riesgo impredecible; el valor del anti-alucinado honesto (`endpoints=[]` + nota) es mayor en el vídeo que una extracción frágil. |
| GraphQL | **FUERA (stretch v0.2+)** | La introspection es otro modelo de schema (queries/mutations/tipos), no paths/params: duplicaría el contract Digest v1 por cero valor demo. |
| Servidor MCP generado | **FUERA del demo — deuda técnica documentada** | Ver 1.4. El manifest persistido queda en formato exportable para futuro barato. |
| Multipart/binary bodies, streaming, webhooks de la spec | **FUERA v0** | `api_call` JSON-only: menos superficie de errores, el demo no los enseña. |
| Auth: bearer + header custom + query-key | **MVP**; OAuth flows | **FUERA** — OAuth de terceros exige declararlo bajo `oauth:` en `plugin.yaml` (H11) con `authorize_url`/`token_url`/`hosts` https y firma `hermes auth add <plugin>:<id>`: coste alto y demo frágil. Bearer/query cubre OpenWeather y Petstore. |

El corte que define el mensaje del demo: **fiabilidad visible**. El jurado declara reliability como criterio (H10); un «no hay spec, no invento endpoints» en pantalla vale más que un scraper que a veces acierta.

---

## 4. Política de dependencias para pasar la regla 10

Facts base [medido]: Python del host 3.14.9 (H5); core soporta `>=3.11,<3.15` con 46 pins exactos y congelados (H9); PyYAML **no** está en el core; `ruamel.yaml` sí (`==0.18.16`).

Política recomendada, en orden de preferencia:

1. **Cero dependencias nuevas en v0.** El Digest JSON y el executor HTTP salen con stdlib: `json`, `urllib.request` (con `timeout=` en cada llamada), `pathlib`, `dataclasses`, `re`, `base64` (basic auth), `typing`. Un `pyproject.toml` con `dependencies = []` (o `python_dependencies` vacío) **cuenta como sin declaración: cero consentimiento de dependencias, cero riesgo de union-conflict** (H14, [repo]: el conflicto clásico «conflicts with the dependencies pinned by Hermes core» deja el plugin `installed but disabled` — el peor modo de falla del hackathon). En NixOS, cero wheels nativas es la diferencia entre instalar y no instalar.
2. **Si una API demo obliga a YAML de OpenAPI:** una única dep, `ruamel.yaml>=0.18.10,<0.19` [estimado el rango]. El core ya pinnea `==0.18.16` (H9), así que floor+bound queda dentro del pin y **no mueve nada del core** (regla 10: «Hermes's exact pins cannot move»). PyYAML está prohibido de facto: no está en el core (H9) → wheel nueva en cada SO → riesgo de ruinas en Python 3.14/NixOS.
3. **Forma de todo declar:** `>=floor,<next_major` (o `<next_minor` si es pre-1.0), floor en la versión más vieja API-compatible, nunca sobre una release de menos de ~3 días. Esto es literal lo que los reviewers piden (H14 regla 10) y lo que la guía recomienda («Strongly recommended: upper bounds on every dependency»).
4. **Prohibidos:** floors desnudos (`>=X`), requirements por URL directa («Direct-URL requirements are not managed»), y cualquier import de módulos internos del core (regla 9 — el validate lo rechaza en admission).
5. **Compatibilidad Python:** escribir código 3.11-compatible (el core también corre 3.11–3.13 máquinas de terceros, H9) aunque aquí se testee con 3.14.9. Prohibido `tomllib`-fuera-de-3.11-style mistakes… (tomllib existe desde 3.11: OK), y toda sintaxis/stdlib de 3.13+.
6. **Un sitio de declaración:** la guía dice que `pyproject.toml` gana sobre `python_dependencies` ([repo]); elegir uno (recomendado: `python_dependencies` vacío en el manifest, o directamente no meter pyproject si no hay deps).

---

## 5. Riesgos prácticos de instalación en otras máquinas y cómo se chequean antes del PR

El entorno objetivo del plugin son máquinas ajenas (Windows, macOS, Linux, NixOS) sin claves de JJ. Herramientas ya confirmadas: `hermes plugins doctor` **corre con `HERMES_HOME` temporal de serie** y bloquea sockets directos durante el registro (H12), y `hermes plugins validate --install-deps` es exactamente el gate de CI (H6, H14).

| Riesgo | Chequeo pre-PR concreto |
|---|---|
| Ruedas Python que no existen para 3.14/NixOS en el SO del reviewer | Política de §4: cero deps v0 (stdlib). Es la mitigación estructural — sin ruedas no hay fallo de ruedas. |
| Rutas hardcodeadas (Windows vs POSIX) | Regla de código: solo `Path(__file__).parent` (para leer `skills/` de dentro del repo) y `plugin_data_dir("api-architect")` (H12) para estado. Chequeo: `grep -rn "C:\\\\\|/Users/\|/home/\|~/" src/` → debe dar cero hits. |
| Claves ausentes rompen arranque | Chequeo doble en Windows: `validate` y `doctor --ci` (1) con `OPENWEATHER_API_KEY` presente y (2) **sin ninguna clave** del plugin en `.env` → el registro debe pasar y `architect_learn`/`api_call` devolver error JSON accionable, nunca un traceback de import (H11: config_schema es warning-never-fail; requires_env vacío no gatea). Recordar el modo del doctor: sockets bloqueados durante el registro → no hacer ningún fetch dentro de `register(ctx)`. |
| Encoding Windows (cp1252 por defecto de `open()` en texto) | Regla de código: `encoding="utf-8"` explícito en toda lectura/escritura de specs/manifests. [estimado como best-practice del host ES-Windows; coste cero] |
| Syntax de Python posterior a 3.11 | `python -m py_compile` con el Python del venv de Hermes (3.14.9, H5) + revisión mental contra 3.11; si hay un 3.12 local disponible, un `py_compile` extra. |
| macOS/Linux sin poder probar aquí | Mismo code path stdlib; el diferencial real es rutas (arriba) y wheels (§4). Si hay SSH a un Linux de JJ: clonar y `doctor --ci` alli (1 h, opcional). NixOS: no testeable hoy — mitigado por stdlib-first; marcarlo en `platforms: []` (todas) del yaml del catálogo y ser honesto en el README. |
| Colgar bajo unattended/cron | Regla 12 (H14): toda llamada de red con `timeout=`, sin prompts interactivos en ningún camino de código, fallos como JSON de error. Chequeo: ejecutar la tool con un host inalcanzable y cronómetro (< timeout + margen). |
| Security scan del install (regla 7) | Auto-scan leyendo el propio repo con ojos de reviewer: sin `eval`/`os.system`/`subprocess` innecesarios, sin binarios ni `.pyc` (regla 17), disclosure de red-a-terceros en README y descripción del PR (regla 13: `api_call` pega a los hosts que el usuario aprendió — decirlo). `dangerous` = declive; `caution` lo lee el reviewer (H14): mejor que no haya ninguno. |

---

## 6. Tabla comparativa de candidatos

Horas [estimado]; estructura y mecánica [repo] (H10, H14, H11); resto juicio.

| Dimensión | **A — plugin completo** | **B — meta-skill** | **C — híbrido 2 artefactos** |
|---|---|---|---|
| Coste en horas | **53,5 brutas / ~45 con cortes de emergencia** [estimado] | **~26–34** [estimado] | **60–63** (A + 6–10 de doble artefacto) [estimado] |
| Riesgo de no llegar a tiempo | Medio-bajo: cabe en ~6 jornadas con cortes explícitos y día 11 de buffer | Bajo (pero irrelevante: no se puede presentar) | Medio-alto: el overhead extra consume exactamente el buffer |
| Riesgo de declive en catálogo | **Bajo**: estático y declarativo (capabilities == registro), sin deps → nada que scanner/reviewer puedan chocar | N/A (no entra en el catálogo; 100% de no-presentación) | **Medio-alto de diseño**: skill fuera del repo = plugin roto en las máquinas del reviewer bajo el pin de un solo SHA (regla 2 + regla 3); si se vendoriza, es A con coste extra |
| Wow-factor del demo | **Alto**: URL viva → tool aprendida → llamada real en <90 s, con `ctx.llm` usando credenciales del host (el jurado pide «more than a prompt or a skill») | Medio-bajo: literalmente es una skill; el criterio declarado juega en su contra | Alto (el plugin es el mismo que A) |
| Fiabilidad post-install | **Alta**: arranca sin claves (config_schema secret), sin ruedas, fail-clean con timeouts | N/A (no hay install) | Media: dos fuentes de verdad que pueden divergir entre repos |
| Reusabilidad para JJ después | **Alta**: plugin instalable por catálogo + skill empaquetada legible + motor de parser reutilizable en el mismo repo | Alta solo como skill personal de JJ | Máxima en teoría — pero A ya la entrega empaquetada en el mismo artefacto pinneado |

---

## 7. Ejecución exacta de 6 días para JJ solo, y el candidato más honesto

Bloque a bloque (lo que haría, sin más ayuda, desde ya):

1. **Hoy (día 1, 4–5 h):** crear `C:/Users/JJ/api-architect` como repo git con `plugin.yaml` (manifest v2, sin capabilities, config_schema con 3 claves + secret), `__init__.py` con `register(ctx)` que registra `architect_learn`/`api_call` como stubs que devuelven JSON de error, skill vacía. Correr `hermes plugins validate c:/Users/JJ/api-architect --install-deps` y `hermes plugins doctor C:/Users/JJ/api-architect --ci` hasta que ambos pasen. Congelar Digest v1 (dataclasses + `to_json`, provenance obligatoria) y crear `tests/fixtures/` con Petstore y Postman echo. Publicar el repo público vacío pero con validate verde.
2. **Día 2 (7–8 h):** módulo `parse_spec` (OpenAPI 3 JSON) + tests offline. DoD: el Digest de Petstore tiene paths/params/auth con provenance por endpoint.
3. **Día 3 (7–8 h):** paso `ctx.llm.complete_structured` con el `json_schema` del manifest + retry con error si `result.parsed is None`; tool `architect_learn` completa con persistencia en `plugin_data_dir()`. DoD: URL de Petstore → manifest persistido. Si hay holgura: parser Postman/swagger2.
4. **Día 4 (7–8 h):** tool `api_call` completa (auth bearer/header/query-key, timeouts, JSON only). Clave propia de OpenWeather de JJ vía `config_schema` secret en su instalación local. DoD: end-to-end real. Cortar aquí cualquier exceso.
5. **Día 5 (6–8 h):** skill empaquetada + README con disclosure (red a hosts aprendidos, sin telemetría) + tests + doble pase limpio `validate --install-deps --json` y `doctor --ci` (2ª pasada sin claves). **Abrir el PR del catálogo** (yaml en `plugin-catalog/api-architect.yaml`, repo `https://github.com/<owner_monyinet>/api-architect`, SHA completo de 40 hex, description con disclosure). Opcional: doctor por SSH en Linux.
6. **Día 6 (4–6 h):** grabar vídeo <90 s — 3 escenas: (a) `architect_learn` sobre Petstore aprendiendo la API, (b) `api_call` ejecutando una llamada real, (c) caso honesto de HTML: descubre el link, `endpoints=[]`, sin alucinar; mención de 5 s al `ctx.llm` con credenciales del host. Post en X con @NousResearch. **Typeform con ambos enlaces** (es válido desde que el PR está abierto, H10).
7. **Día 7 (buffer hasta 23:00 CET):** fixes de review, SHA-bump si tocó código (dentro del mismo PR mientras no esté mergeado — [estimado el modo seguro de hacerlo dado que los SHA-bumps post-merge son PRs nuevos, H14 regla 4]), y nada nuevo.

**El candidato más honesto es A.** Ni sobreingeniería ni infruso, con facts:

- ** Contra la infrusión (B):** la entrada ES el plugin (H10) — B no puede presentarse, y el criterio explícito del jurado (favorecer lo que un prompt o una skill no logran) penaliza exactamente su forma. Las superficies que A usa y una skill no puede: `ctx.llm` con credenciales del host y gate fail-closed (H13), persistencia profile-scoped que sobrevive a updates (H12), secrets por rutas Hermes con consentimiento y form en Desktop (H11), y la página de catálogo con el README renderizado al SHA pinneado ([repo], README del catálogo).
- ** Contra la sobreingeniería (C tal cual, MCP generado, HTML profundo, GraphQL):** el pin de un solo SHA/único repo hace que C sea o A o un plugin roto (H14 reglas 2–3); las reglas 3/6/9 prohíben literalmente el registro dinámico de la Fase 3 original; y cada corte de §3.2 elimina exactamente lo que el gate castiga o lo que el demo no puede mostrar de forma fiable. El MVP resultante tiene cero dependencies, cero capabilities declaradas, dos tools, un paso LLM acotado, una skill y persistencia de datos — nada más.
- La modularidad que C quería (motor / cerebro / executor como capas) se conserva **dentro del repo de A**: es práctica de código gratis, no un artefacto extra.

---

## Anexo A — Listado de cifras del informe

- [medido] 5-nov-2026 01:52:41 CET, epoch `1793839901` (H1)
- [medido] deadline: 2026-11-11 23:00:00 +01:00 Madrid / 17:00 −05:00 NY / 22:00 UTC / epoch `1794434400` (H2)
- [medido] 165,13 h restantes ≈ 6 días 21 h al medir (H3)
- [medido] Hermes 0.22.5 (2026.9.24) stable, git, commit `bf0203a794a0`; Python 3.14.9; OpenAI SDK 2.24.0; update available (H5)
- [medido] flags de `hermes plugins validate --install-deps/--json` y `doctor --ci` (H6)
- [medido] unbrowse: community, v12.1.0, SHA `a82c59df…`, 24 tools, 1 hook, 1 env (H7)
- [medido] catálogo local: 763 yaml; `openapi` solo en 4; cero entradas tipo api-architect/spec_to_tool/api_call (H8)
- [medido] core: requires-python `>=3.11,<3.15`, 46 deps, `pydantic==2.13.4`, `ruamel.yaml==0.18.16`, sin PyYAML (H9)
- [repo] tweet: 21:33 UTC 4-nov, 791 likes / 59 replies, texto 2.068 chars; premios tal cual; «under 90 seconds»; «form as soon as PR open»; checks «once a maintainer starts them»; review «every few days» (H10)
- [repo] plugin.yaml v2: campos y semántica de `requires_env` (disabled si falta), `config_schema` (warning-never-fail, type `secret`), `capabilities` (consent, fail-closed) (H11)
- [repo] herramientas ctx: register_tool/register_skill (read-only, namespaced, opt-in, no index), plugin_data_dir/plugin_db, handlers → JSON string (H12)
- [repo] ctx.llm: complete/complete_structured, credenciales del host, fail-closed, `result.parsed` None-si-JSON-inválido (H13)
- [repo] reglas 1–17 + «validate --install-deps is what CI runs» + pin one-SHA-one-repo (H14)
- [estimado] A: 53,5 h brutas; ~45 h con cortes de emergencia; buffer 20% → ~64 h (§1.1)
- [estimado] B: 26–34 h (§1.2)
- [estimado] C: 60–63 h (§1.3)
- [estimado] extracción fiable de HTML→endpoints: 8–10 h (si se hiciera, nunca — cortada)
- [estimado] defaults de config_schema (timeout 15 s, max_endpoints 40) y floor `requires_hermes: ">=0.22.0"`
- [estimado] capacidad de trabajo: 6 jornadas ~8–9 h + parcial de hoy dentro de las 165 h de reloj [medido]
- [no medible sin ejecutar el candidato — prohibido] horas reales de A/B/C ejecutadas; comportamiento real de `ctx.llm.complete_structured` en runtime; resultado real del security scan sobre el repo; duración real del vídeo. Hecho estructural que los sustituye: las superficies y reglas del gate están textualmente verificadas (H11–H14), y validate/doctor locales son el gate exacto de CI (H6, H14).

## Anexo B — Afirmaciones NO verificadas (no pudieron confirmarse en esta sesión)

1. **Typeform vivo y campos**: `form.typeform.com/to/FwJLvOln` no fue accedido (página JS); existe solo como texto del tweet [repo]. Qué pide el form (campos) es desconocido hasta enviar.
2. **El workflow real del CI del catálogo** (fichero Actions en el repo remoto): no leído. La creencia «clona al SHA y corre validate» viene del README («`--install-deps` … the catalog CI gate» + «CI runs») [repo H14], no del fichero del workflow.
3. **Cadencia exacta de revisión** («cada pocos días»): verificada como frase del tweet [repo H10], no como dato observado de merges reales recientes.
4. **Comportamiento real `ctx.llm` con el trust gate en una sesión real**: doc leída pero no ejecutable sin el plugin (no medible; prohibido ejecutar el candidato).
5. **Comportamiento en macOS/Linux/NixOS**: no probado — no hay máquina de esos SO en esta sesión; las mitigaciones de §5 son juicio [estimado].
6. **Contenido profundo de los 4 yaml con mención `openapi`** (`agentplaybooks-tools`, `govee`, `hermes-tripo`, `switchbot-control`) y de adyacentes por nombre (`mcp-plugin-creator`, `hermes-plugin-gen`, `openspec`): solo grep de nombres/términos [medido H8]. **CORRECCIÓN del consolidador (5-nov): los tres nombres NO existen en el catálogo** — grep sobre el log completo del escaneo del catálogo de esta sesión (todas las entradas visibles) devuelve cero coincidencias; los adyacentes reales siguen siendo los del H8 (unbrowse, monid, lib-docs, priors, familia jp-, switchbot-control).
7. **Versión de release estable vigente a nivel global** (¿es 0.22.5 la última stable de todos?): solo se midió que la instalación de JJ está en 0.22.5 con update disponible [medido H5]; el floor `>=0.22.0` es [estimado] contra esa señal.
8. **Que el SHA del PR pueda actualizarse dentro del mismo PR antes del merge**: inferencia prudente [estimado] — la regla 4 define SHA-bumps como PRs nuevos para entradas ya listadas; el caso pre-merge no está especificado en lo leído.
