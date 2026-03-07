{{ main_headers.title }}

**{{ main_headers.pub_date }}:** {{ formatted_date }}

---

{{ executive_summary }}

{% if 'price_volume_analysis' in section_reports or 'investor_trading_analysis' in section_reports %}
{{ main_headers.tech_analysis }}
{% if 'price_volume_analysis' in section_reports %}
{{ section_reports.price_volume_analysis }}

{% if price_chart_html or volume_chart_html %}
{{ "### 가격 및 거래량 차트" if language == "ko" else "### Price and Volume Charts" }}

{% if price_chart_html %}
{{ "#### 가격 차트" if language == "ko" else "#### Price Chart" }}

{{ price_chart_html }}
{% endif %}

{% if volume_chart_html %}
{{ "#### 거래량 차트" if language == "ko" else "#### Trading Volume Chart" }}

{{ volume_chart_html }}
{% endif %}
{% endif %}
{% endif %}

{% if 'investor_trading_analysis' in section_reports %}
{{ section_reports.investor_trading_analysis }}
{% endif %}
{% endif %}


{% if 'company_status' in section_reports or 'company_overview' in section_reports %}
{{ main_headers.fundamental }}
{% if 'company_status' in section_reports %}
{{ section_reports.company_status }}

{% if market_cap_chart_html or fundamentals_chart_html %}
{{ "### 시가총액 및 펀더멘털 차트" if language == "ko" else "### Market Cap and Fundamental Charts" }}

{% if market_cap_chart_html %}
{{ "#### 시가총액 추이" if language == "ko" else "#### Market Cap Trend" }}

{{ market_cap_chart_html }}
{% endif %}

{% if fundamentals_chart_html %}
{{ "#### 펀더멘털 지표 분석" if language == "ko" else "#### Fundamental Indicator Analysis" }}

{{ fundamentals_chart_html }}
{% endif %}
{% endif %}
{% endif %}

{% if 'company_overview' in section_reports %}
{{ section_reports.company_overview }}
{% endif %}
{% endif %}


{% if 'news_analysis' in section_reports %}
{{ main_headers.news }}
{{ section_reports.news_analysis }}
{% endif %}


{% if 'market_index_analysis' in section_reports %}
{{ main_headers.market }}
{{ section_reports.market_index_analysis }}
{% endif %}


{% if 'investment_strategy' in section_reports %}
{{ main_headers.strategy }}
{{ section_reports.investment_strategy }}
{% endif %}

---

{{ disclaimer }}
