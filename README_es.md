<div align="center">
  <img src="docs/images/prism-insight-logo.jpeg" alt="PRISM-INSIGHT Logo" width="300">
  <br><br>
  <img src="https://img.shields.io/badge/License-AGPL%20v3-blue.svg" alt="Licencia">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/OpenAI-GPT--5-green.svg" alt="OpenAI">
  <img src="https://img.shields.io/badge/Anthropic-Claude--Sonnet--4.5-green.svg" alt="Anthropic">
</div>

# PRISM-INSIGHT

[![GitHub Sponsors](https://img.shields.io/github/sponsors/dragon1086?style=for-the-badge&logo=github-sponsors&color=ff69b4&label=Sponsors)](https://github.com/sponsors/dragon1086)
[![Stars](https://img.shields.io/github/stars/dragon1086/prism-insight?style=for-the-badge)](https://github.com/dragon1086/prism-insight/stargazers)

> **Sistema de Analisis Bursatil y Trading Impulsado por IA**
>
> Mas de 13 agentes de IA especializados colaboran para detectar acciones con movimientos inusuales, generar informes de nivel profesional y ejecutar operaciones automaticamente.

<p align="center">
  <a href="README.md">English</a> |
  <a href="README_ko.md">한국어</a> |
  <a href="README_ja.md">日本語</a> |
  <a href="README_zh.md">中文</a> |
  <a href="README_es.md">Español</a>
</p>

---

### 🏆 Patrocinador Platino

<div align="center">
<a href="https://wrks.ai/en">
  <img src="docs/images/wrks_ai_logo.png" alt="AI3 WrksAI" width="50">
</a>

**[AI3](https://www.ai3.kr/) | [WrksAI](https://wrks.ai/en)**

AI3, creador de **WrksAI** — el asistente de IA para profesionales,<br>
patrocina con orgullo **PRISM-INSIGHT** — el asistente de IA para inversionistas.
</div>

---

## ⚡ Pruebalo Ahora (Sin Instalacion)

### 1. Dashboard en Vivo
Observa el rendimiento del trading con IA en tiempo real:
👉 **[analysis.stocksimulation.kr](https://analysis.stocksimulation.kr/)**

### 2. Canales de Telegram
Recibe alertas diarias de acciones con movimientos inusuales e informes de analisis con IA:
- 🇺🇸 **[Canal en Ingles](https://t.me/prism_insight_global_en)**
- 🇰🇷 **[Canal en Coreano](https://t.me/stock_ai_agent)**
- 🇯🇵 **[Canal en Japones](https://t.me/prism_insight_ja)**
- 🇨🇳 **[Canal en Chino](https://t.me/prism_insight_zh)**
- 🇪🇸 **[Canal en Español](https://t.me/prism_insight_es)**

### 3. Informe de Ejemplo
Mira un informe de analisis de Apple Inc. generado por IA:

[![Informe de Ejemplo - Analisis de Apple Inc.](https://img.youtube.com/vi/LVOAdVCh1QE/maxresdefault.jpg)](https://youtu.be/LVOAdVCh1QE)

---

## ⚡ Pruebalo en 60 Segundos (Acciones de EE.UU.)

La forma mas rapida de probar PRISM-INSIGHT. Solo requiere una **clave de API de OpenAI**.

```bash
# Clone and run the quickstart script
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight
./quickstart.sh YOUR_OPENAI_API_KEY
```

Esto genera un informe de analisis con IA para Apple (AAPL). Prueba con otras acciones:
```bash
python3 demo.py MSFT              # Microsoft
python3 demo.py NVDA              # NVIDIA
python3 demo.py TSLA --language ko  # Tesla (informe en coreano)
```

> 💡 **Obtiene tu clave de API de OpenAI** en [OpenAI Platform](https://platform.openai.com/api-keys)
>
> 📰 **Opcional**: Agrega una [clave de API de Perplexity](https://www.perplexity.ai/) en `mcp_agent.config.yaml` para el analisis de noticias

Tus informes PDF generados por IA se guardaran en `prism-us/pdf_reports/`.

<details>
<summary>🐳 O usa Docker (sin necesidad de configurar Python)</summary>

```bash
# 1. Set your OpenAI API key
export OPENAI_API_KEY=sk-your-key-here

# 2. Start container
docker-compose -f docker-compose.quickstart.yml up -d

# 3. Run analysis
docker exec -it prism-quickstart python3 demo.py NVDA
```

Los informes se guardaran en `./quickstart-output/`.

</details>

---

## 🚀 Instalacion Completa

### Requisitos Previos
- Python 3.10+ o Docker
- Clave de API de OpenAI ([obtenla aqui](https://platform.openai.com/api-keys))

### Opcion A: Instalacion con Python

```bash
# 1. Clone & Install
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight
pip install -r requirements.txt

# 2. Install Playwright for PDF generation
python3 -m playwright install chromium

# 3. Install perplexity-ask MCP server
cd perplexity-ask && npm install && npm run build && cd ..

# 4. Setup config
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
# Edit mcp_agent.secrets.yaml with your OpenAI API key
# Edit mcp_agent.config.yaml with KRX credentials (Kakao account)

# 5. Run analysis (no Telegram required!)
python stock_analysis_orchestrator.py --mode morning --no-telegram
```

### Opcion B: Docker (Recomendado para Produccion)

```bash
# 1. Clone & Configure
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
# Edit config files with your API keys

# 2. Build & Run
docker-compose up -d

# 3. Run analysis manually (optional)
docker exec prism-insight-container python3 stock_analysis_orchestrator.py --mode morning --no-telegram
```

📖 **Guia de Instalacion Completa**: [docs/SETUP.md](docs/SETUP.md)

---

## 📖 ¿Que es PRISM-INSIGHT?

PRISM-INSIGHT es un sistema de analisis bursatil impulsado por IA, **completamente de codigo abierto y gratuito**, para los mercados de **Corea del Sur (KOSPI/KOSDAQ)** y **Estados Unidos (NYSE/NASDAQ)**.

### Capacidades Principales
- **Deteccion de Movimientos Inusuales** — Deteccion automatica de acciones con volumen o movimientos de precio inusuales
- **Informes de Analisis con IA** — Informes de nivel profesional generados por 13 agentes de IA especializados
- **Simulacion de Trading** — Decisiones de compra/venta impulsadas por IA con gestion de portafolio
- **Trading Automatizado** — Ejecucion real a traves de la API de Korea Investment & Securities
- **Integracion con Telegram** — Alertas en tiempo real y difusion en multiples idiomas

### Modelos de IA
- **Analisis y Trading**: OpenAI GPT-5
- **Bot de Telegram**: Anthropic Claude Sonnet 4.5
- **Traduccion**: OpenAI GPT-5 (soporte para EN, JA, ZH)

---

## 🤖 Sistema de Agentes de IA

Mas de 13 agentes especializados colaboran en equipos:

| Equipo | Agentes | Proposito |
|--------|---------|-----------|
| **Analisis** | 6 agentes | Analisis tecnico, financiero, sectorial, de noticias y de mercado |
| **Estrategia** | 1 agente | Sintesis de estrategia de inversion |
| **Comunicacion** | 3 agentes | Resumen, evaluacion de calidad, traduccion |
| **Trading** | 3 agentes | Decisiones de compra/venta, bitacora |
| **Consulta** | 2 agentes | Interaccion con usuarios via Telegram |

<details>
<summary>📊 Ver Diagrama de Flujo de Agentes</summary>
<br>
<img src="docs/images/aiagent/agent_workflow2.png" alt="Flujo de Trabajo de Agentes" width="700">
</details>

📖 **Documentacion Detallada de Agentes**: [docs/CLAUDE_AGENTS.md](docs/CLAUDE_AGENTS.md)

---

## ✨ Caracteristicas Principales

| Caracteristica | Descripcion |
|----------------|-------------|
| **🤖 Analisis con IA** | Analisis bursatil de nivel experto mediante el sistema multi-agente de GPT-5 |
| **📊 Deteccion de Movimientos** | Lista de seguimiento automatica a traves del analisis de tendencias del mercado matutino/vespertino |
| **📱 Telegram** | Distribucion de analisis en tiempo real a canales |
| **📈 Simulacion de Trading** | Simulacion de estrategia de inversion impulsada por IA |
| **💱 Trading Automatizado** | Ejecucion a traves de la API de Korea Investment & Securities |
| **🎨 Dashboard** | Seguimiento transparente de portafolio, operaciones y rendimiento |
| **🧠 Auto-mejora** | Ciclo de retroalimentacion con bitacora de trading — las tasas de exito historicas de cada tipo de alerta informan automaticamente las decisiones futuras de compra ([detalles](docs/TRADING_JOURNAL.md#performance-tracker-피드백-루프-self-improving-trading)) |
| **🇺🇸 Mercados de EE.UU.** | Soporte completo para analisis de NYSE/NASDAQ |

<details>
<summary>🖼️ Ver Capturas de Pantalla</summary>
<br>
<img src="docs/images/trigger-en.png" alt="Deteccion de Movimientos" width="500">
<img src="docs/images/summary-en.png" alt="Resumen" width="500">
<img src="docs/images/dashboard1-en.png" alt="Dashboard" width="500">
</details>

---

## 📈 Rendimiento del Trading

### Temporada 2 (En Curso)
| Metrica | Valor |
|---------|-------|
| Fecha de Inicio | 2025.09.29 |
| Total de Operaciones | 50 |
| Tasa de Exito | 42.00% |
| **Retorno Acumulado** | **127.34%** |
| Retorno en Cuenta Real | +8.50% |

👉 **[Dashboard en Vivo](https://analysis.stocksimulation.kr/)**

---

## 🇺🇸 Modulo de Mercado Bursatil de EE.UU.

El mismo flujo de trabajo impulsado por IA para los mercados estadounidenses:

```bash
# Run US analysis
python prism-us/us_stock_analysis_orchestrator.py --mode morning --no-telegram

# With English reports
python prism-us/us_stock_analysis_orchestrator.py --mode morning --language en
```

**Fuentes de Datos**: yahoo-finance-mcp, sec-edgar-mcp (presentaciones ante la SEC, operaciones de insiders)

---

## 📚 Documentacion

| Documento | Descripcion |
|-----------|-------------|
| [docs/SETUP.md](docs/SETUP.md) | Guia de instalacion completa |
| [docs/CLAUDE_AGENTS.md](docs/CLAUDE_AGENTS.md) | Detalles del sistema de agentes de IA |
| [docs/TRIGGER_BATCH_ALGORITHMS.md](docs/TRIGGER_BATCH_ALGORITHMS.md) | Algoritmos de deteccion de movimientos inusuales |
| [docs/TRADING_JOURNAL.md](docs/TRADING_JOURNAL.md) | Sistema de memoria de trading |

---

## 🎨 Ejemplos de Frontend

### Pagina de Inicio
Una pagina de inicio moderna y responsiva construida con Next.js y Tailwind CSS.

👉 **[Demo en Vivo](https://prism-insight-landing.vercel.app/)**

```bash
cd examples/landing
npm install
npm run dev
# Visit http://localhost:3000
```

**Caracteristicas**: Animacion de lluvia de matriz, efectos de maquina de escribir, contador de estrellas de GitHub, diseno responsivo

### Dashboard
Seguimiento de portafolio en tiempo real y panel de rendimiento.

```bash
cd examples/dashboard
npm install
npm run dev
# Visit http://localhost:3000
```

**Caracteristicas**: Vista general del portafolio, historial de operaciones, metricas de rendimiento, selector de mercado (KR/US)

📖 **Guia de Configuracion del Dashboard**: [examples/dashboard/DASHBOARD_README.md](examples/dashboard/DASHBOARD_README.md)

---

## 💡 Servidores MCP

### Mercado Coreano
- **[kospi_kosdaq](https://github.com/dragon1086/kospi-kosdaq-stock-server)** — Datos bursatiles de KRX
- **[firecrawl](https://github.com/mendableai/firecrawl-mcp-server)** — Rastreo web
- **[perplexity](https://github.com/perplexityai/modelcontextprotocol)** — Busqueda web
- **[sqlite](https://github.com/modelcontextprotocol/servers-archived)** — Base de datos de simulacion de trading

### Mercado Estadounidense
- **[yahoo-finance-mcp](https://pypi.org/project/yahoo-finance-mcp/)** — OHLCV, datos financieros
- **[sec-edgar-mcp](https://pypi.org/project/sec-edgar-mcp/)** — Presentaciones ante la SEC, operaciones de insiders

---

## 🤝 Contribuciones

1. Haz un fork del proyecto
2. Crea una rama de funcionalidad (`git checkout -b feature/funcionalidad-increible`)
3. Realiza tus cambios (`git commit -m 'Add amazing feature'`)
4. Sube la rama (`git push origin feature/funcionalidad-increible`)
5. Crea un Pull Request

---

## 📄 Licencia

**Doble Licencia:**

### Para Uso Individual y de Codigo Abierto
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

Gratuito bajo AGPL-3.0 para uso personal, proyectos no comerciales y desarrollo de codigo abierto.

### Para Uso Comercial SaaS
Se requiere una licencia comercial separada para empresas SaaS.

📧 **Contacto**: dragon1086@naver.com
📄 **Detalles**: [LICENSE-COMMERCIAL.md](LICENSE-COMMERCIAL.md)

---

## ⚠️ Aviso Legal

La informacion de analisis es solo para referencia y no constituye asesoramiento de inversion. Todas las decisiones de inversion y las ganancias o perdidas resultantes son responsabilidad del inversionista.

---

## 💝 Patrocinio

### Apoya el Proyecto

Costos operativos mensuales (~$310/mes):
- API de OpenAI: ~$235/mes
- API de Anthropic: ~$11/mes
- Firecrawl + Perplexity: ~$35/mes
- Infraestructura de servidor: ~$30/mes

Actualmente sirviendo a mas de 450 usuarios de forma gratuita.

<div align="center">
  <a href="https://github.com/sponsors/dragon1086">
    <img src="https://img.shields.io/badge/Patrocinar_en_GitHub-❤️-ff69b4?style=for-the-badge&logo=github-sponsors" alt="Patrocinar en GitHub">
  </a>
</div>

### Patrocinadores Individuales
<!-- sponsors -->
- [@jk5745](https://github.com/jk5745) 💙
<!-- sponsors -->

---

## ⭐ Crecimiento del Proyecto

¡Alcanzamos **mas de 250 estrellas en 10 semanas** desde el lanzamiento!

[![Star History Chart](https://api.star-history.com/svg?repos=dragon1086/prism-insight&type=Date)](https://star-history.com/#dragon1086/prism-insight&Date)

---

**⭐ Si este proyecto te fue util, por favor regalanos una estrella!**

📞 **Contacto**: [GitHub Issues](https://github.com/dragon1086/prism-insight/issues) | [Telegram](https://t.me/stock_ai_agent) | [Discussions](https://github.com/dragon1086/prism-insight/discussions)
