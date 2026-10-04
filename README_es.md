<div align="center">
  <img src="docs/images/prism-insight-logo.jpeg" alt="PRISM-INSIGHT Logo" width="240">
  &nbsp;&nbsp;
  <a href="assets/characters/priso/README.md">
    <img src="assets/characters/priso/v1.0/priso_master_transparent.png" alt="Priso, la mascota de PRISM" width="240">
  </a>
  <br>
  <sub><strong>Priso</strong> · Mascota oficial de PRISM</sub>
  <br><br>
  <img src="https://img.shields.io/badge/License-AGPL%20v3-blue.svg" alt="License">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/OpenAI-GPT--6-green.svg" alt="OpenAI GPT-6">
  <img src="https://img.shields.io/badge/Anthropic-Claude_Sonnet_5.5_(optional)-green.svg" alt="Anthropic Claude Sonnet 5.5 (opcional)">
  <img src="https://img.shields.io/badge/ChatGPT_Plus-Codex_OAuth-ff6b35.svg" alt="ChatGPT Plus">
</div>

[![CI](https://github.com/dragon1086/prism-insight/actions/workflows/ci.yml/badge.svg)](https://github.com/dragon1086/prism-insight/actions/workflows/ci.yml)
[![Codacy Badge](https://app.codacy.com/project/badge/Grade/2f8fd766b0634c068ff9da57ccda00c6)](https://app.codacy.com/gh/dragon1086/prism-insight/dashboard?utm_source=gh&utm_medium=referral&utm_content=&utm_campaign=Badge_grade)

# PRISM-INSIGHT

[![GitHub Sponsors](https://img.shields.io/github/sponsors/dragon1086?style=for-the-badge&logo=github-sponsors&color=ff69b4&label=Sponsors)](https://github.com/sponsors/dragon1086)
[![Stars](https://img.shields.io/github/stars/dragon1086/prism-insight?style=for-the-badge)](https://github.com/dragon1086/prism-insight/stargazers)

> **Sistema de análisis y trading bursátil impulsado por IA**
>
> Más de 13 agentes de IA especializados colaboran para detectar acciones con subidas bruscas, generar informes de nivel analista y ejecutar operaciones de forma automática.

<p align="center">
  <a href="README.md">English</a> |
  <a href="README_ko.md">한국어</a> |
  <a href="README_ja.md">日本語</a> |
  <a href="README_zh.md">中文</a> |
  <a href="README_es.md">Español</a>
</p>

### Patrocinador Platino

<div align="center">
<a href="https://wrks.ai/en">
  <img src="docs/images/wrks_ai_logo.png" alt="AI3 WrksAI" width="50">
</a>

**[AI3](https://www.ai3.kr/) | [WrksAI](https://wrks.ai/en)**

AI3, creador de **WrksAI** — el asistente de IA para profesionales —,<br>
patrocina con orgullo **PRISM-INSIGHT**, el asistente de IA para inversores.
</div>

---

## NUEVO: Stance — ¿Qué estrategia de trading sistemático está ganando ahora?

<p align="center">
  <img src="docs/images/stance-ecosystem-en.png" alt="Clasificación de Stance que compara rentabilidad, peor caída, exposición media invertida y tasa de registro de estrategias de Corea y EE. UU." width="100%">
</p>

**¿Resultados pasados? No los aceptamos.** Cada registro de Stance empieza en el momento de la inscripción: sin historiales subidos ni datos retroactivos. Las decisiones y sus resultados forman un único registro público y continuo que muestra la **habilidad y el riesgo reales** de una estrategia, no una selección de sus mejores momentos. Las clasificaciones se separan entre Corea y EE. UU. y muestran la rentabilidad junto a la peor caída, la exposición media invertida y la tasa de registro.

- **Descubre qué funciona ahora** — compara todas las estrategias con las mismas reglas
- **Mira más allá de la rentabilidad** — revisa el riesgo, la exposición real y los registros que faltan
- **Confía en la cronología** — el servidor sella la hora y el precio de cada decisión y luego calcula lo que ocurre
- **Inscribe tu propia estrategia** — un agente de programación la detecta, registra, conecta y prueba

**[Ver la clasificación en vivo](https://analysis.stocksimulation.kr/?tab=stance)** · **[Inscribir mi estrategia](https://analysis.stocksimulation.kr/?tab=stance)** · **[Guía rápida](stance/QUICKSTART.md)**

<details>
<summary><strong>¿Cómo se une mi estrategia?</strong></summary>

<p align="center">
  <img src="docs/images/stance-integration-en.png" alt="Conectar una estrategia a Stance: abrir el proyecto, pegar una instrucción en un agente de programación, revisar las estrategias y perfiles detectados y aprobar el registro y la integración automáticos" width="100%">
</p>

Abre el proyecto de tu estrategia en **Codex CLI, Cursor, Claude Code u otro agente de programación** y pega en el chat la instrucción copiada del panel de Stance. El agente encuentra las estrategias independientes y las carteras de Corea y EE. UU., pregunta solo por los datos del perfil público que falten, muestra el plan de registro y continúa únicamente tras tu aprobación. También guarda las claves, modifica el código y ejecuta las pruebas.

Tu estrategia aparece en **Construyendo historial** desde su primera decisión. En los mercados de acciones, la clasificación oficial comienza tras **63 sesiones de registro y 20 operaciones cerradas que usaron cada una al menos el 1% de los activos**. El registro empieza el día de la conexión y no se pueden añadir resultados pasados. No se necesita cuenta real de bróker, saldo ni claves del bróker.
</details>

---

## NUEVO: Compatibilidad con suscripciones ChatGPT Plus/Pro

**¿Sin clave de API? No hay problema.** PRISM-INSIGHT ahora permite ejecutar el análisis directamente con tu suscripción a ChatGPT Plus (20 $/mes) o Pro (200 $/mes) mediante el **proxy Codex OAuth**.

```bash
# Inicio de sesión único (se abrirá el navegador para autenticarte en ChatGPT)
python -m cores.chatgpt_proxy.oauth_login

# Volver a autenticarse (cambiar de cuenta o renovar tokens caducados)
python -m cores.chatgpt_proxy.oauth_login --force

# Ejecutar con tu suscripción de ChatGPT
PRISM_OPENAI_AUTH_MODE=chatgpt_oauth python stock_analysis_orchestrator.py --mode morning
```

> Los tokens se renuevan automáticamente en segundo plano, así que solo tendrás que iniciar sesión de nuevo si cambias de cuenta de ChatGPT o de contraseña.

Cero facturas de API. El mismo análisis potente. Tu suscripción actual hace el trabajo.

---

## App móvil

<div align="center">

**Análisis bursátil con IA esté donde esté**

<a href="https://play.google.com/store/apps/details?id=com.prisminsight.prism_mobile">
  <img src="https://img.shields.io/badge/Google_Play-Descargar-green?style=for-the-badge&logo=google-play" alt="Google Play">
</a>
<a href="https://apps.apple.com/us/app/prism-insight-stock-analysis/id6759331074">
  <img src="https://img.shields.io/badge/App_Store-Descargar-blue?style=for-the-badge&logo=apple" alt="App Store">
</a>

</div>

- **Filtrado inteligente** — recibe solo las alertas de Telegram que te interesan
- **Informes PDF** — informes de análisis con IA optimizados para móvil

---

## PRISM-INSIGHT en acción

[![PRISM-INSIGHT Demo](https://img.youtube.com/vi/zAywb1G0wRA/maxresdefault.jpg)](https://www.youtube.com/watch?v=zAywb1G0wRA)

---

## Pruébalo ya (sin instalación)

### 1. Panel en vivo
Consulta el rendimiento del trading con IA en tiempo real:
**[analysis.stocksimulation.kr](https://analysis.stocksimulation.kr/)**

### 2. Canales de Telegram
Recibe a diario alertas de subidas bruscas e informes de análisis con IA:
- **[Canal en inglés](https://t.me/prism_insight_global_en)**
- **[Canal en coreano](https://t.me/stock_ai_agent)**
- **[Canal en japonés](https://t.me/prism_insight_ja)**
- **[Canal en chino](https://t.me/prism_insight_zh)**
- **[Canal en español](https://t.me/prism_insight_es)**

### 3. Informe de ejemplo
Mira un informe de análisis de Apple Inc. generado por IA:

[![Informe de ejemplo - Análisis de Apple Inc.](https://img.youtube.com/vi/LVOAdVCh1QE/maxresdefault.jpg)](https://youtu.be/LVOAdVCh1QE)

---

## Pruébalo en 60 segundos (acciones de EE. UU.)

La forma más rápida de probar PRISM-INSIGHT. Solo necesitas una **clave de API de OpenAI**.

```bash
# Clona el repositorio y ejecuta el script de inicio rápido
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight
./quickstart.sh YOUR_OPENAI_API_KEY
```

Esto genera un informe de análisis con IA de Apple (AAPL). Prueba con otras acciones:
```bash
python3 demo.py MSFT              # Microsoft
python3 demo.py NVDA              # NVIDIA
python3 demo.py TSLA --language ko  # Tesla (informe en coreano)
```

> **Obtén tu clave de API de OpenAI** en [OpenAI Platform](https://platform.openai.com/api-keys)
>
> **Opcional**: añade una [clave de API de Perplexity](https://www.perplexity.ai/) a `mcp_agent.config.yaml` para el análisis de noticias
>
> **Opcional**: añade `ADANOS_API_KEY` para enriquecer el análisis de noticias de acciones de EE. UU. con contexto estructurado de sentimiento social

Tus informes PDF generados por IA se guardarán en `prism-us/pdf_reports/`.

<details>
<summary>O usa Docker (sin configurar Python)</summary>

```bash
# 1. Configura tu clave de API de OpenAI
export OPENAI_API_KEY=sk-your-key-here

# 2. Construye e inicia la imagen local de inicio rápido
docker compose -f docker-compose.quickstart.yml up --build -d

# 3. Ejecuta el análisis
docker exec -it prism-quickstart python3 demo.py NVDA
```

La primera ejecución construye la imagen en local, por lo que puede tardar varios minutos.

Los informes se guardarán en `./quickstart-output/`.

</details>

---

## Instalación completa

### Requisitos previos
- Python 3.10+ o Docker
- Clave de API de OpenAI ([consíguela aquí](https://platform.openai.com/api-keys)) o suscripción a ChatGPT Plus/Pro

### Opción A: instalación con Python

```bash
# 1. Clonar e instalar
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight
pip install -r requirements.txt

# 2. Instalar Playwright para generar PDF
python3 -m playwright install chromium

# 3. Los servidores MCP (Firecrawl, Perplexity, ...) se inician bajo demanda con npx/uv,
#    según mcp_agent.config.yaml — no requieren instalación aparte

# 4. Configuración
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
cp trading/config/kis_devlp.yaml.example trading/config/kis_devlp.yaml
# Edita mcp_agent.secrets.yaml con tu clave de API de OpenAI
# Edita trading/config/kis_devlp.yaml con tus claves de la API de KIS (datos del mercado coreano)

# 5. Ejecutar el análisis (¡no hace falta Telegram!)
python stock_analysis_orchestrator.py --mode morning --no-telegram
```

### Opción B: Docker (recomendado para producción)

```bash
# Con los archivos de configuración del paso 4 listos:
docker compose up -d
docker exec prism-insight-container python3 stock_analysis_orchestrator.py --mode morning --no-telegram
```

**Guía de instalación completa**: [docs/SETUP.md](docs/SETUP.md)

---

## ¿Qué es PRISM-INSIGHT?

PRISM-INSIGHT es un sistema de análisis bursátil con IA **totalmente de código abierto y gratuito** para los mercados de **Corea (KOSPI/KOSDAQ)** y **EE. UU. (NYSE/NASDAQ)**.

### Capacidades principales
- **Detección de subidas bruscas** — detecta automáticamente acciones con movimientos inusuales de volumen o precio
- **Informes de análisis con IA** — informes de nivel analista generados por agentes de IA especializados
- **Simulación de trading** — decisiones de compra y venta con IA y gestión de cartera
- **Trading automático** — ejecución real mediante la API de Korea Investment & Securities
- **Integración con Telegram** — alertas en tiempo real y difusión en varios idiomas
- **Inteligencia macro** — detección del régimen de mercado, análisis de rotación sectorial y seguimiento de eventos de riesgo

### Modelos de IA
Modelos predeterminados en el código (todos se pueden cambiar en `.env`; consulta [.env.example](.env.example)):

| Función | Modelo predeterminado |
|---------|----------------------|
| Secciones del informe, estrategia, resumen e inteligencia macro | OpenAI **GPT-6 Luna** (`REPORT_MODEL`) |
| Decisiones de compra y venta | OpenAI **GPT-6.1 Sol** (`PRISM_BUY_CODEX_MODEL`, `PRISM_SELL_CODEX_MODEL`) |
| Preguntas y respuestas en Telegram | OpenAI **GPT-6.1 Sol** (`TELEGRAM_ANALYSIS_MODEL`) |
| Traducción (EN, JA, ZH, ES) y diario de trading | OpenAI **GPT-6 Luna** |
| Opcional: agente de insights bajo demanda | Anthropic **Claude Sonnet 5.5** (`INSIGHT_MODEL`) |

Todo funciona con una clave de API de OpenAI o con una suscripción a ChatGPT Plus/Pro (Codex OAuth).

---

## Sistema de agentes de IA

Los agentes se agrupan por ruta de ejecución, no por un número fijo:

| Equipo | Agentes | Función |
|--------|---------|---------|
| **Macro** | KR / US | Sectores líderes, riesgos y eventos sobre el régimen de mercado basado en reglas |
| **Análisis de acciones** | 6 secciones base por mercado | Técnico, flujos de negociación, empresa, sector, noticias, mercado |
| **Estrategia y resumen** | Se crean durante la ejecución | Convierten las secciones base en una estrategia de inversión y un resumen clave |
| **Trading** | Compra y venta KR / US | Escenarios de IA combinados con filtros de puntuación, cartera y reentrada |
| **Diario y memoria** | Revisión, compresión, principios | Aportan los resultados de las operaciones cerradas a las siguientes decisiones |
| **Comunicación y consulta** | Evaluación, optimización, traducción, seguimiento | Resúmenes de Telegram y conversaciones con usuarios |

<details>
<summary>Ver diagrama del flujo de agentes</summary>
<br>
<img src="docs/images/aiagent/agent_workflow2.png" alt="Flujo de agentes" width="700">
</details>

**Más detalles**: [Arquitectura del pipeline (coreano)](docs/PIPELINE_ARCHITECTURE_ko.md) | [Sistema de agentes de IA](docs/CLAUDE_AGENTS.md)

---

## Funciones clave

| Función | Descripción |
|---------|-------------|
| **Análisis con IA** | Análisis bursátil de nivel experto mediante un sistema multiagente con modelos de la familia OpenAI GPT-6 |
| **Detección de subidas** | Lista de seguimiento automática a partir del análisis de tendencias de mañana y tarde |
| **Telegram** | Distribución del análisis en tiempo real a los canales |
| **Simulación de trading** | Simulación de estrategias de inversión con IA |
| **Trading automático** | Ejecución mediante la API de Korea Investment & Securities |
| **Panel** | Seguimiento transparente de cartera, operaciones y rendimiento |
| **Automejora** | Bucle de retroalimentación del diario de trading: la tasa de acierto histórica de cada disparador influye automáticamente en las siguientes compras ([detalles](docs/TRADING_JOURNAL.md#performance-tracker-피드백-루프-self-improving-trading)) |
| **Mercados de EE. UU.** | Soporte completo para el análisis de NYSE/NASDAQ |
| **Inteligencia macro** | Detección del régimen de mercado y rotación sectorial para elegir mejor las acciones |
| **App móvil** | App para iOS y Android con filtrado inteligente e informes PDF |

<details>
<summary>Ver capturas del panel</summary>
<br>
<img src="docs/images/dashboard_portfolio.png" alt="Resumen de cartera" width="700">
<br><br>
<img src="docs/images/dashboard_trades.png" alt="Simulador de trading" width="700">
<br><br>
<img src="docs/images/dashboard_performance.png" alt="Escenario de trading con IA" width="700">
</details>

---

## Rendimiento del trading — Temporada 2

![PRISM-INSIGHT Temporada 2: rentabilidad realizada de la cuenta de 10 posiciones frente a KOSPI/KOSDAQ y S&P 500/Nasdaq](docs/images/season2-performance-en.png)

Mostramos dos medidas de las mismas operaciones cerradas:

- **Suma de rentabilidades por operación** — la rentabilidad de cada operación cerrada, sumada. Sin interés compuesto y sin ponderar por tamaño de posición.
- **Rentabilidad de la cuenta de 10 posiciones** — beneficio realizado de una cuenta simulada dividida en 10 posiciones iguales (una compra menor que una posición cuenta según su fracción). Solo operaciones cerradas, sin interés compuesto.

| | Corea (Temporada 2) | EE. UU. |
|---|---|---|
| Periodo | 2025-09-30 ~ 2026-10-02 | 2026-01-28 ~ 2026-10-02 |
| Operaciones cerradas | 211 | 127 |
| Tasa de acierto | 40,3 % (85 ganadoras) | 33,1 % (42 ganadoras) |
| Rentabilidad media por operación | +1,68 % | +0,65 % |
| Suma de rentabilidades por operación | +355,3 % | +82,8 % |
| **Rentabilidad de la cuenta de 10 posiciones** | **+35,2 %** | **+8,3 %** |
| Mayor caída de la curva de la cuenta | −9,3 p. p. | −13,7 p. p. |
| Índice en el mismo periodo | KOSPI +103,5 % (3.431 → 6.982)<br>KOSDAQ +5,3 % (847 → 892) | S&P 500 +10,8 % (6.969 → 7.723)<br>Nasdaq +14,8 % (23.685 → 27.191) |
| Mejores operaciones cerradas | Samsung Electro-Mechanics +86,8 %<br>SK hynix +73,8 %<br>SK Square +57,6 % | Micron +105,7 %, Micron +52,8 %<br>IBM +27,0 % |

**La cuenta quedó por detrás del KOSPI en este periodo, y queremos decirlo con claridad.** El KOSPI casi se duplicó mientras el KOSDAQ subió alrededor de un 5 %, en un rally liderado por grandes valores de semiconductores. La revisión de salidas que hizo PRISM en octubre de 2026 encontró la mayor brecha: en las operaciones coreanas que subieron un 30 % o más en los 60 días hábiles posteriores a la entrada, la ganancia realizada mediana fue de +2 %, mientras que la subida máxima mediana fue de +60 %. El sistema vendía a sus líderes demasiado pronto. Los cambios de la siguiente sección apuntan justo a eso, y seguiremos publicando ambas medidas para que se pueda comprobar el efecto.

> Fuente: datos del panel en vivo ([KR](https://analysis.stocksimulation.kr/dashboard_data.json), [US](https://analysis.stocksimulation.kr/us_dashboard_data.json)), generados el 2026-10-02 (KR) y el 2026-10-03 KST (US). La variación de los índices se mide desde el primer punto de la curva del panel (KR 2025-09-29, US 2026-01-29). Se excluyen las posiciones abiertas. Resultados simulados; no es asesoramiento de inversión.

**[Panel en vivo](https://analysis.stocksimulation.kr/)**

---

## Cómo opera PRISM ahora (oct. 2026)

![Cómo opera PRISM ahora: selección, análisis con IA, decisión de compra, primera compra pequeña, ampliaciones por escenarios, mantener a los líderes, reentrada y revisión semanal](docs/images/how-prism-trades-en.png)

**La dirección.** PRISM sigue la tendencia al estilo O'Neil. La mayoría de las operaciones son pequeñas y se cortan rápido, y la cuenta busca crecer por escalones gracias a unas pocas acciones que se mueven mucho. Operar más aumenta la probabilidad de encontrar esas acciones, pero los stop-loss repetidos pueden ir drenando la cuenta, así que la habilidad central es **seleccionar y comprar las acciones correctas**.

| Paso | Qué ocurre |
|------|-----------|
| **1. Selección** | Los disparadores de la mañana y de la tarde eligen acciones con un impulso inusual de precio y volumen. Cada disparador recibe un peso de calidad (0,7–1,3) basado en los últimos 180 días del propio PRISM: con qué frecuencia sus candidatos llegaron a +20 % y el resultado realizado medio. Los disparadores débiles ya no tienen plaza garantizada en la selección final. |
| **2. Análisis con IA** | Agentes especializados redactan el informe (técnico, flujos de negociación, finanzas, sector, noticias, mercado) y después una estrategia de inversión. |
| **3. Decisión de compra** | El agente de compra puntúa la configuración del 1 al 10 según una rúbrica escrita: fundamentales (rentabilidad, balance, crecimiento, claridad del negocio), señales de impulso y una comprobación de tendencia. Para entrar también hace falta la puntuación mínima del régimen de mercado actual, un ratio riesgo/beneficio mínimo y un stop-loss no más amplio que el límite del régimen (−5 % a −7 %). |
| **4. Primera compra pequeña** | La cuenta se divide en 10 posiciones iguales. Una nueva posición empieza con el 30–80 % de una posición, según la volatilidad de la acción; las configuraciones de alta puntuación de los disparadores más fuertes empiezan un escalón más arriba. |
| **5. Ampliaciones por escenarios** | Al entrar, la IA escribe de 2 a 4 escenarios de ampliación (por ejemplo, una ruptura o un retroceso que se recupera) y los actualiza cada día. El código solo compra más cuando se cumplen las condiciones de un escenario, solo por encima del coste medio, nunca más que la compra anterior, dentro del presupuesto de riesgo inicial y hasta una posición completa. Un movimiento fuerte confirmado (un 8 % por encima de la entrada con 1,5 veces el volumen normal, tras una primera ampliación ese día) puede ampliar por segunda vez en la misma sesión. |
| **6. Mantener a los líderes** | Una acción que cierra un 20 % por encima de su primer precio de compra en 4–15 días hábiles, sin alejarse demasiado de su media de 50 días, se considera líder. Durante un máximo de 40 días hábiles solo se vende si cierra por debajo de la media de 50 días o cae por debajo del primer precio de compra. Las acciones que suben un 20 % en 1–3 días mantienen los stops normales de protección de beneficios. |
| **7. Reentrada** | Una acción que salió por stop-loss, o que se descartó por la ubicación de su precio, se vigila hasta 60 días hábiles. Si recupera su nivel clave poco antes del cierre (KR 14:00, US 13:50), una nueva revisión de la IA debe aprobar la compra. Hasta 3 intentos en cada periodo de vigilancia y como máximo 2 órdenes de reentrada por mercado y día. |
| **8. Ciclo de revisión** | Un informe semanal de líderes sigue la captura de grandes ganadoras, las ganadoras perdidas, el coste de los stop-loss y los resultados por disparador. Una revisión a dos semanas (18 de octubre de 2026) evalúa cada cambio de octubre con los mismos criterios. |

La mayoría de estos cambios entraron en funcionamiento entre el 2 y el 4 de octubre de 2026, y el 6 de octubre es la primera sesión con todos ellos activos, por lo que las cifras de la Temporada 2 corresponden en su mayor parte a un periodo anterior. Notas de diseño (en coreano): [dirección](docs/TRADING_CHANGE_REVIEW_HARNESS.md) · [prioridad de disparadores](docs/TRIGGER_QUALITY_PRIORITY_ko.md) · [primera compra pequeña](docs/micro-split/B3_LIVE_ko.md) · [ampliaciones por escenarios](docs/micro-split/ADD_SCENARIOS_DESIGN_ko.md) · [mantener a los líderes](docs/RUNNER_HOLD_RULE_ko.md) · [reentrada](docs/REENTRY_V3_LIVE_ko.md) · [informe semanal](docs/WEEKLY_RUNNER_REPORT_ko.md) · [revisión a dos semanas](docs/TWO_WEEK_REVIEW_ko.md)

---

## Cómo aprendió el sistema de trading

El historial del mercado coreano muestra dos fallos opuestos: primero, evitar demasiadas
entradas; después, asumir riesgo sin suficiente control del estado. Las versiones de la
v1.16.7 a la v2.18 llevaron el sistema, paso a paso, de corregir sesgos en el prompt a
salvaguardas deterministas del régimen de mercado, del estado de salida y de la reentrada.

![Evolución del trading de PRISM-INSIGHT: del sesgo de observación al control de riesgo basado en el estado](docs/images/trading-evolution-en.png)

> Las cifras son de diagnóstico. La rentabilidad acumulada es la suma de las rentabilidades
> por operación, y los resultados de los candidatos no comprados son observaciones a
> posteriori, no una rentabilidad de cartera ponderada en el tiempo ni un backtest realizable.

### Octubre de 2026: qué probamos, qué adoptamos y qué descartamos

Antes de cambiar una regla, PRISM la reproduce sobre sus propios candidatos y operaciones pasados, y la conclusión se anota en un registro de lecciones para no repetir la misma pregunta.

**Descartado** (no superó a las reglas actuales):
- **Disparadores de pocket pivot y de confirmación por volumen** (2018–2026): la condición de volumen no aportó nada en ninguno de los dos mercados.
- **Comprar cuando una acción en caída recupera a la vez sus medias de 50 y 200 días**: sin ventaja, y peor en Corea.
- **Una regla de 8 semanas de O'Neil sin cambios, manteniendo hasta la media de 50 días**: peores resultados (Corea −43 p. p. y EE. UU. alrededor de −40 p. p. en la suma de rentabilidades por operación), porque las acciones que subieron un 20 % en 1–3 días devolvieron casi toda la ganancia esperando una media de 50 días lejana.
- **Revisar los stops cada hora, stops basados en volatilidad (ATR) y ejecutar los stops elevados solo al cierre**: todos dieron peores resultados que los stops actuales.

**Adoptado**:
- **Mantener a los líderes** solo en acciones que llegan a +20 % en 4–15 días hábiles sin alejarse demasiado de la media de 50 días (Corea +57 p. p. en 7 operaciones afectadas; un resultado pequeño y dentro de la muestra que el informe semanal sigue vigilando).
- **Primeras compras pequeñas con escenarios de ampliación escritos por la IA** en lugar de una escalera fija de +2 % / +4 %, con ampliaciones más rápidas ante una fortaleza confirmada.
- **Hasta 3 intentos de reentrada** en cada periodo de vigilancia. El antiguo límite de un solo intento cortaba reentradas rentables (Corea 4 de 7, EE. UU. 13 de 43).
- **Prioridad de disparadores según el historial del propio PRISM**, y un disparador de aumento de volumen que ahora exige que el precio suba.

Registro completo (en coreano): [docs/RESEARCH_LESSONS_ko.md](docs/RESEARCH_LESSONS_ko.md)

---

## Módulo del mercado de EE. UU.

El mismo flujo con IA para los mercados de EE. UU.:

```bash
# Ejecutar el análisis de EE. UU.
python prism-us/us_stock_analysis_orchestrator.py --mode morning --no-telegram

# Con informes en inglés
python prism-us/us_stock_analysis_orchestrator.py --mode morning --language en
```

**Fuentes de datos**: yahoo-finance-mcp, sec-edgar-mcp (documentos de la SEC, operaciones de insiders)

---

## Documentación

| Documento | Descripción |
|-----------|-------------|
| [docs/SETUP.md](docs/SETUP.md) | Guía de instalación completa |
| [docs/CLAUDE_AGENTS.md](docs/CLAUDE_AGENTS.md) | Detalles del sistema de agentes de IA |
| [docs/PIPELINE_ARCHITECTURE_ko.md](docs/PIPELINE_ARCHITECTURE_ko.md) | Diseño selección → análisis → trading → retroalimentación (coreano) |
| [docs/TRIGGER_BATCH_ALGORITHMS.md](docs/TRIGGER_BATCH_ALGORITHMS.md) | Algoritmos de detección de subidas |
| [docs/TRADING_JOURNAL.md](docs/TRADING_JOURNAL.md) | Sistema de memoria de trading |
| [docs/TRADING_CHANGE_REVIEW_HARNESS.md](docs/TRADING_CHANGE_REVIEW_HARNESS.md) | Dirección de inversión y lista de revisión para cambios de trading (coreano) |
| [docs/RESEARCH_LESSONS_ko.md](docs/RESEARCH_LESSONS_ko.md) | Registro de lecciones: qué se probó, adoptó y descartó (coreano) |
| [docs/TRIGGER_QUALITY_PRIORITY_ko.md](docs/TRIGGER_QUALITY_PRIORITY_ko.md) | Prioridad de disparadores según el historial de PRISM (coreano) |
| [docs/micro-split/B3_LIVE_ko.md](docs/micro-split/B3_LIVE_ko.md) | Primera compra pequeña y construcción de posiciones en real (coreano) |
| [docs/micro-split/ADD_SCENARIOS_DESIGN_ko.md](docs/micro-split/ADD_SCENARIOS_DESIGN_ko.md) | Escenarios de ampliación con IA y ampliaciones rápidas (coreano) |
| [docs/RUNNER_HOLD_RULE_ko.md](docs/RUNNER_HOLD_RULE_ko.md) | Regla para mantener a los líderes (coreano) |
| [docs/REENTRY_V3_LIVE_ko.md](docs/REENTRY_V3_LIVE_ko.md) | Reglas de reentrada (coreano) |
| [docs/WEEKLY_RUNNER_REPORT_ko.md](docs/WEEKLY_RUNNER_REPORT_ko.md) | Informe semanal de líderes (coreano) |
| [docs/TWO_WEEK_REVIEW_ko.md](docs/TWO_WEEK_REVIEW_ko.md) | Revisión a dos semanas de los cambios de octubre (coreano) |

---

## Ejemplos de frontend

### Panel
Panel de seguimiento de cartera y rendimiento en tiempo real.

**[Demo en vivo](https://analysis.stocksimulation.kr/)**

```bash
cd examples/dashboard
npm install
npm run dev
# Visita http://localhost:3000
```

**Funciones**: resumen de cartera, historial de operaciones, métricas de rendimiento, selector de mercado (KR/US), comparación de rentabilidad frente a KOSPI/KOSDAQ

**Guía de configuración del panel**: [examples/dashboard/DASHBOARD_README.md](examples/dashboard/DASHBOARD_README.md)

---

## Servidores MCP

### Mercado coreano
- **kospi_kosdaq** — servidor integrado de datos del mercado coreano basado en la API de KIS (`cores/market_data`)
- **[firecrawl](https://github.com/mendableai/firecrawl-mcp-server)** — rastreo web
- **[perplexity](https://github.com/perplexityai/modelcontextprotocol)** — búsqueda web
- **[sqlite](https://github.com/modelcontextprotocol/servers-archived)** — base de datos de la simulación de trading

### Mercado de EE. UU.
- **[yahoo-finance-mcp](https://pypi.org/project/yahoo-finance-mcp/)** — OHLCV, estados financieros
- **[sec-edgar-mcp](https://pypi.org/project/sec-edgar-mcp/)** — documentos de la SEC, operaciones de insiders

---

## Contribuir

1. Haz un fork del proyecto
2. Crea una rama de funcionalidad (`git checkout -b feature/amazing-feature`)
3. Haz commit de tus cambios (`git commit -m 'Add amazing feature'`)
4. Sube la rama (`git push origin feature/amazing-feature`)
5. Abre un Pull Request

### Colaboradores y patrocinadores

**Colaboradores de código** — gracias a todas las personas que han mejorado PRISM-INSIGHT:

[@dragon1086](https://github.com/dragon1086) · [@rocky-mun](https://github.com/rocky-mun) · [@tkgo11](https://github.com/tkgo11) · [@alexander-schneider](https://github.com/alexander-schneider) · [@bonggu-kang](https://github.com/bonggu-kang) · [@willagio](https://github.com/willagio) · [@lifrary](https://github.com/lifrary) · [@cjinzy](https://github.com/cjinzy) · [@don9x2E](https://github.com/don9x2E) · [@jk5745](https://github.com/jk5745) · [@sungwoowi](https://github.com/sungwoowi)

**Gold Supporter** — [@tkgo11](https://github.com/tkgo11)

Gracias por apoyar el proyecto.

---

## Licencia

**Doble licencia:**

### Uso individual y de código abierto
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

Gratuito bajo AGPL-3.0 para uso personal, proyectos no comerciales y desarrollo de código abierto.

### Uso comercial en SaaS
Las empresas SaaS necesitan una licencia comercial aparte.

**Contacto**: dragon1086@naver.com
**Detalles**: [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md)

Los componentes de código abierto de terceros siguen sujetos a sus propios términos.
Consulta [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) para ver los avisos, los enlaces al código fuente y los textos de las licencias.

---

## Aviso legal

La información de análisis es solo de referencia y no constituye asesoramiento de inversión. Todas las decisiones de inversión y las pérdidas o ganancias resultantes son responsabilidad del inversor.

---

## Patrocinio

### Apoya el proyecto

Costes operativos mensuales (a enero de 2026, ~313 $/mes):
- API de OpenAI: ~234 $/mes
- API de Anthropic: ~11 $/mes
- Firecrawl + Perplexity: ~36 $/mes
- Infraestructura de servidores: ~32 $/mes

Actualmente da servicio gratuito a más de 450 usuarios.

<div align="center">
  <a href="https://github.com/sponsors/dragon1086">
    <img src="https://img.shields.io/badge/Sponsor_on_GitHub-❤️-ff69b4?style=for-the-badge&logo=github-sponsors" alt="Patrocinar en GitHub">
  </a>
</div>

---

## Crecimiento del proyecto

[![Star History Chart](https://api.star-history.com/svg?repos=dragon1086/prism-insight&type=Date)](https://star-history.com/#dragon1086/prism-insight&Date)

---

**Si este proyecto te ha sido útil, ¡danos una estrella!**

**Contacto**: [GitHub Issues](https://github.com/dragon1086/prism-insight/issues) | [Telegram](https://t.me/stock_ai_agent) | [Discussions](https://github.com/dragon1086/prism-insight/discussions)
