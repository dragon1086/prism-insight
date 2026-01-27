"""
US Stock Chart Generation Module

Generates charts for US stock analysis reports using yfinance data.
Chart types:
1. Price Chart (Candlestick + MA) - Technical analysis
2. Institutional Holdings Chart - Ownership breakdown
3. Technical Indicators Chart - RSI + MACD

All charts are returned as matplotlib figures or base64 HTML img tags.
"""

import logging
from io import BytesIO
import base64
from datetime import datetime, timedelta
from typing import Optional, Tuple

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

logger = logging.getLogger(__name__)

# =============================================================================
# Color Palettes
# =============================================================================

PRIMARY_COLORS = ["#0066cc", "#ff9500", "#00cc99", "#cc3300", "#6600cc"]
SECONDARY_COLORS = ["#e6f2ff", "#fff4e6", "#e6fff7", "#ffe6e6", "#f2e6ff"]

# Chart-specific colors
UP_COLOR = "#26a69a"       # Green for up
DOWN_COLOR = "#ef5350"     # Red for down
NEUTRAL_COLOR = "#757575"  # Gray for neutral

# Institutional chart colors
INST_COLORS = {
    "institutions": "#2196F3",    # Blue
    "insiders": "#FF9800",        # Orange
    "retail": "#4CAF50",          # Green
    "other": "#9E9E9E"            # Gray
}

# Technical indicator colors
RSI_COLOR = "#9C27B0"       # Purple
MACD_COLOR = "#2196F3"      # Blue
SIGNAL_COLOR = "#FF9800"    # Orange
HIST_UP_COLOR = "#26a69a"   # Green
HIST_DOWN_COLOR = "#ef5350" # Red


# =============================================================================
# Utility Functions
# =============================================================================

def figure_to_base64_html(
    fig,
    chart_name: str = "chart",
    width: int = 900,
    dpi: int = 80,
    image_format: str = 'jpg'
) -> Optional[str]:
    """
    Convert a matplotlib figure to base64 HTML img tag.

    Args:
        fig: matplotlib figure object
        chart_name: name for the chart (used in alt text)
        width: image width in pixels
        dpi: image resolution
        image_format: 'jpg' or 'png'

    Returns:
        HTML img tag with embedded base64 image, or None if failed
    """
    try:
        buffer = BytesIO()

        save_kwargs = {
            'format': image_format,
            'bbox_inches': 'tight',
            'dpi': dpi
        }

        if image_format.lower() == 'png':
            save_kwargs['transparent'] = False
            save_kwargs['facecolor'] = 'white'

        fig.savefig(buffer, **save_kwargs)
        plt.close(fig)
        buffer.seek(0)

        # JPEG compression
        if image_format.lower() in ['jpg', 'jpeg']:
            try:
                from PIL import Image
                img = Image.open(buffer)
                new_buffer = BytesIO()
                img.save(new_buffer, format='JPEG', quality=85, optimize=True)
                buffer = new_buffer
                buffer.seek(0)
            except ImportError:
                pass

        img_str = base64.b64encode(buffer.getvalue()).decode('utf-8')

        content_type = f"image/{image_format.lower()}"
        if image_format.lower() == 'jpg':
            content_type = 'image/jpeg'

        return f'<img src="data:{content_type};base64,{img_str}" alt="{chart_name}" width="{width}" />'

    except Exception as e:
        logger.warning(f"Failed to convert figure to base64: {e}")
        return None


def calculate_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    """
    Calculate Relative Strength Index (RSI).

    Args:
        prices: Series of closing prices
        period: RSI period (default 14)

    Returns:
        Series of RSI values
    """
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_macd(
    prices: pd.Series,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Calculate MACD (Moving Average Convergence Divergence).

    Args:
        prices: Series of closing prices
        fast_period: Fast EMA period (default 12)
        slow_period: Slow EMA period (default 26)
        signal_period: Signal line period (default 9)

    Returns:
        Tuple of (MACD line, Signal line, Histogram)
    """
    exp1 = prices.ewm(span=fast_period, adjust=False).mean()
    exp2 = prices.ewm(span=slow_period, adjust=False).mean()

    macd = exp1 - exp2
    signal = macd.ewm(span=signal_period, adjust=False).mean()
    histogram = macd - signal

    return macd, signal, histogram


# =============================================================================
# Chart Creation Functions
# =============================================================================

def create_us_price_chart(
    ticker: str,
    company_name: str,
    hist_df: pd.DataFrame
) -> Optional[plt.Figure]:
    """
    Create candlestick price chart with volume and moving averages.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL")
        company_name: Company name for chart title
        hist_df: pandas DataFrame with OHLCV data from yfinance

    Returns:
        matplotlib figure object or None if failed
    """
    try:
        import mplfinance as mpf

        if hist_df is None or hist_df.empty:
            return None

        # Verify required columns
        required_cols = ['Open', 'High', 'Low', 'Close', 'Volume']
        for col in required_cols:
            if col not in hist_df.columns:
                return None

        # Prepare data
        df = hist_df[required_cols].copy()
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)
        df = df.sort_index()

        # Calculate moving averages
        df['MA20'] = df['Close'].rolling(window=20).mean()
        df['MA60'] = df['Close'].rolling(window=60).mean()
        df['MA120'] = df['Close'].rolling(window=120).mean()

        # Create OHLCV DataFrame
        ohlc_df = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()

        # Chart style
        mc = mpf.make_marketcolors(
            up=UP_COLOR,
            down=DOWN_COLOR,
            edge='inherit',
            wick='inherit',
            volume={'up': UP_COLOR, 'down': DOWN_COLOR}
        )
        style = mpf.make_mpf_style(
            marketcolors=mc,
            gridstyle=':',
            gridcolor='#e0e0e0'
        )

        # Moving average plots
        additional_plots = []
        if not df['MA20'].isna().all():
            additional_plots.append(
                mpf.make_addplot(df['MA20'], color='#ff9500', width=1)
            )
        if not df['MA60'].isna().all():
            additional_plots.append(
                mpf.make_addplot(df['MA60'], color='#0066cc', width=1.5)
            )
        if not df['MA120'].isna().all():
            additional_plots.append(
                mpf.make_addplot(df['MA120'], color='#cc3300', width=1.5, linestyle='--')
            )

        # Create chart
        fig, axes = mpf.plot(
            ohlc_df,
            type='candle',
            style=style,
            title=f"{company_name} ({ticker}) - Price Chart",
            ylabel='Price ($)',
            volume=True,
            figsize=(12, 8),
            tight_layout=True,
            addplot=additional_plots if additional_plots else None,
            panel_ratios=(4, 1),
            returnfig=True
        )

        # Add price annotations
        max_idx = df['Close'].idxmax()
        min_idx = df['Close'].idxmin()
        last_idx = df.index[-1]

        ax1 = axes[0]
        bbox_props = dict(boxstyle="round,pad=0.3", fc="#f8f9fa", ec="none", alpha=0.9)

        # High point
        ax1.annotate(
            f"High: ${df.loc[max_idx, 'Close']:,.2f}",
            xy=(max_idx, df.loc[max_idx, 'Close']),
            xytext=(0, 15),
            textcoords='offset points',
            ha='center',
            va='bottom',
            bbox=bbox_props,
            fontsize=9
        )

        # Low point
        ax1.annotate(
            f"Low: ${df.loc[min_idx, 'Close']:,.2f}",
            xy=(min_idx, df.loc[min_idx, 'Close']),
            xytext=(0, -15),
            textcoords='offset points',
            ha='center',
            va='top',
            bbox=bbox_props,
            fontsize=9
        )

        # Current price
        ax1.annotate(
            f"Current: ${df.loc[last_idx, 'Close']:,.2f}",
            xy=(last_idx, df.loc[last_idx, 'Close']),
            xytext=(15, 0),
            textcoords='offset points',
            ha='left',
            va='center',
            bbox=bbox_props,
            fontsize=9
        )

        # Legend
        if additional_plots:
            legend_labels = []
            if not df['MA20'].isna().all():
                legend_labels.append('MA20')
            if not df['MA60'].isna().all():
                legend_labels.append('MA60')
            if not df['MA120'].isna().all():
                legend_labels.append('MA120')
            if legend_labels:
                ax1.legend(legend_labels, loc='upper left', fontsize=8)

        return fig

    except Exception as e:
        logger.warning(f"Failed to create US price chart: {e}")
        return None


def create_us_institutional_chart(
    ticker: str,
    company_name: str,
    major_holders: Optional[pd.DataFrame] = None,
    institutional_holders: Optional[pd.DataFrame] = None
) -> Optional[plt.Figure]:
    """
    Create institutional holdings visualization chart.

    Shows a combination of:
    1. Pie chart: Ownership breakdown (Institutions, Insiders, Retail/Other)
    2. Bar chart: Top 10 institutional holders

    Args:
        ticker: Stock ticker symbol
        company_name: Company name for chart title
        major_holders: DataFrame from yfinance major_holders
        institutional_holders: DataFrame from yfinance institutional_holders

    Returns:
        matplotlib figure object or None if failed
    """
    try:
        # Try to get data from yfinance if not provided
        if major_holders is None or institutional_holders is None:
            import yfinance as yf
            stock = yf.Ticker(ticker)
            if major_holders is None:
                major_holders = stock.major_holders
            if institutional_holders is None:
                institutional_holders = stock.institutional_holders

        # Check if we have data
        if major_holders is None or major_holders.empty:
            logger.warning(f"No major holders data for {ticker}")
            return None

        # Create figure with 2 subplots (12 inches width to match other charts)
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 6))
        fig.suptitle(f"{company_name} ({ticker}) - Institutional Ownership", fontsize=14, fontweight='bold')

        # Parse major_holders data
        # yfinance returns DataFrame with columns: [0, 1] where 0=value, 1=description
        ownership_data = {}

        for _, row in major_holders.iterrows():
            value = row.iloc[0]
            desc = row.iloc[1] if len(row) > 1 else ""

            # Parse percentage
            if isinstance(value, str) and '%' in value:
                try:
                    pct = float(value.replace('%', '').strip())
                except ValueError:
                    continue
            elif isinstance(value, (int, float)):
                pct = float(value) * 100 if value <= 1 else float(value)
            else:
                continue

            desc_lower = str(desc).lower()
            if 'institution' in desc_lower:
                ownership_data['Institutions'] = pct
            elif 'insider' in desc_lower:
                ownership_data['Insiders'] = pct

        # Calculate retail/other
        total_inst_insider = ownership_data.get('Institutions', 0) + ownership_data.get('Insiders', 0)
        if total_inst_insider < 100:
            ownership_data['Retail & Other'] = 100 - total_inst_insider

        # If no meaningful data, return None
        if not ownership_data or all(v == 0 for v in ownership_data.values()):
            logger.warning(f"No meaningful ownership data for {ticker}")
            return None

        # === Subplot 1: Pie Chart ===
        labels = list(ownership_data.keys())
        sizes = list(ownership_data.values())
        colors = [INST_COLORS.get(k.lower().split()[0], INST_COLORS['other']) for k in labels]

        # Custom autopct to skip very small values
        def autopct_func(pct):
            return f'{pct:.1f}%' if pct >= 2 else ''

        wedges, texts, autotexts = ax1.pie(
            sizes,
            labels=labels,
            colors=colors,
            autopct=autopct_func,
            startangle=90,
            explode=[0.02] * len(sizes),
            shadow=False
        )

        # Style the text
        for autotext in autotexts:
            autotext.set_color('white')
            autotext.set_fontweight('bold')
            autotext.set_fontsize(10)

        ax1.set_title("Ownership Breakdown", fontsize=12, pad=10)

        # Add center text with institution percentage
        if 'Institutions' in ownership_data:
            center_text = f"{ownership_data['Institutions']:.1f}%\nInstitutional"
            ax1.text(0, 0, center_text, ha='center', va='center', fontsize=11, fontweight='bold')

        # === Subplot 2: Top Institutional Holders Bar Chart ===
        if institutional_holders is not None and not institutional_holders.empty:
            # Get top 10 holders
            top_holders = institutional_holders.head(10).copy()

            # Extract holder names and percentages
            if 'Holder' in top_holders.columns:
                holder_names = top_holders['Holder'].tolist()
            else:
                holder_names = top_holders.index.tolist()

            # Get percentage held
            if '% Out' in top_holders.columns:
                pct_held = top_holders['% Out'].tolist()
            elif 'pctHeld' in top_holders.columns:
                pct_held = top_holders['pctHeld'].tolist()
            else:
                # Try to calculate from shares if available
                pct_held = [0] * len(holder_names)

            # Convert to percentages if needed
            pct_held = [float(p) * 100 if isinstance(p, (int, float)) and p <= 1 else float(p) if isinstance(p, (int, float)) else 0 for p in pct_held]

            # Truncate long holder names
            holder_names = [name[:25] + '...' if len(str(name)) > 28 else name for name in holder_names]

            # Create horizontal bar chart
            y_pos = range(len(holder_names))
            bars = ax2.barh(y_pos, pct_held, color=INST_COLORS['institutions'], alpha=0.8)

            ax2.set_yticks(y_pos)
            ax2.set_yticklabels(holder_names, fontsize=9)
            ax2.invert_yaxis()  # Top holder at top
            ax2.set_xlabel('% of Outstanding Shares', fontsize=10)
            ax2.set_title('Top 10 Institutional Holders', fontsize=12, pad=10)

            # Add value labels
            for bar, pct in zip(bars, pct_held):
                if pct > 0:
                    ax2.text(
                        bar.get_width() + 0.1,
                        bar.get_y() + bar.get_height() / 2,
                        f'{pct:.2f}%',
                        va='center',
                        fontsize=8
                    )

            ax2.set_xlim(0, max(pct_held) * 1.15 if pct_held else 10)
            ax2.grid(axis='x', linestyle='--', alpha=0.3)
        else:
            ax2.text(0.5, 0.5, 'No institutional holder data available',
                    ha='center', va='center', transform=ax2.transAxes, fontsize=12)
            ax2.set_axis_off()

        plt.tight_layout()
        return fig

    except Exception as e:
        logger.warning(f"Failed to create institutional chart: {e}")
        return None


def create_us_technical_indicators_chart(
    ticker: str,
    company_name: str,
    hist_df: pd.DataFrame,
    rsi_period: int = 14,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9
) -> Optional[plt.Figure]:
    """
    Create technical indicators chart with RSI and MACD.

    Args:
        ticker: Stock ticker symbol
        company_name: Company name for chart title
        hist_df: pandas DataFrame with OHLCV data from yfinance
        rsi_period: RSI calculation period (default 14)
        macd_fast: MACD fast EMA period (default 12)
        macd_slow: MACD slow EMA period (default 26)
        macd_signal: MACD signal line period (default 9)

    Returns:
        matplotlib figure object or None if failed
    """
    try:
        if hist_df is None or hist_df.empty:
            return None

        if 'Close' not in hist_df.columns:
            return None

        # Use last 6 months of data for cleaner visualization
        df = hist_df.copy()
        if len(df) > 126:  # ~6 months of trading days
            df = df.tail(126)

        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)

        # Calculate indicators
        df['RSI'] = calculate_rsi(df['Close'], rsi_period)
        df['MACD'], df['Signal'], df['Histogram'] = calculate_macd(
            df['Close'], macd_fast, macd_slow, macd_signal
        )

        # Create figure with 3 subplots
        fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(12, 10),
                                            gridspec_kw={'height_ratios': [2, 1, 1]})
        fig.suptitle(f"{company_name} ({ticker}) - Technical Indicators", fontsize=14, fontweight='bold')

        # === Subplot 1: Price with Close ===
        ax1.plot(df.index, df['Close'], color=PRIMARY_COLORS[0], linewidth=1.5, label='Close Price')
        ax1.fill_between(df.index, df['Close'], alpha=0.1, color=PRIMARY_COLORS[0])

        # Add moving averages
        if len(df) >= 20:
            ma20 = df['Close'].rolling(window=20).mean()
            ax1.plot(df.index, ma20, color='#ff9500', linewidth=1, linestyle='--', label='MA20', alpha=0.7)

        ax1.set_ylabel('Price ($)', fontsize=10)
        ax1.set_title('Price', fontsize=11, loc='left')
        ax1.legend(loc='upper left', fontsize=8)
        ax1.grid(True, linestyle='--', alpha=0.3)

        # Format x-axis
        ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
        ax1.xaxis.set_major_locator(mdates.MonthLocator())

        # Current price annotation
        last_price = df['Close'].iloc[-1]
        ax1.annotate(
            f'${last_price:,.2f}',
            xy=(df.index[-1], last_price),
            xytext=(10, 0),
            textcoords='offset points',
            fontsize=9,
            fontweight='bold',
            color=PRIMARY_COLORS[0]
        )

        # === Subplot 2: RSI ===
        ax2.plot(df.index, df['RSI'], color=RSI_COLOR, linewidth=1.5)
        ax2.fill_between(df.index, df['RSI'], alpha=0.1, color=RSI_COLOR)

        # RSI levels
        ax2.axhline(y=70, color='red', linestyle='--', linewidth=0.8, alpha=0.7)
        ax2.axhline(y=30, color='green', linestyle='--', linewidth=0.8, alpha=0.7)
        ax2.axhline(y=50, color='gray', linestyle=':', linewidth=0.5, alpha=0.5)

        # Fill overbought/oversold zones
        ax2.fill_between(df.index, 70, 100, alpha=0.1, color='red')
        ax2.fill_between(df.index, 0, 30, alpha=0.1, color='green')

        ax2.set_ylabel('RSI', fontsize=10)
        ax2.set_ylim(0, 100)
        ax2.set_title(f'RSI ({rsi_period})', fontsize=11, loc='left')
        ax2.grid(True, linestyle='--', alpha=0.3)

        # Current RSI value
        current_rsi = df['RSI'].iloc[-1]
        rsi_status = "Overbought" if current_rsi >= 70 else "Oversold" if current_rsi <= 30 else "Neutral"
        rsi_color = 'red' if current_rsi >= 70 else 'green' if current_rsi <= 30 else 'gray'
        ax2.text(0.98, 0.95, f'RSI: {current_rsi:.1f} ({rsi_status})',
                transform=ax2.transAxes, ha='right', va='top', fontsize=9,
                bbox=dict(boxstyle='round', facecolor='white', alpha=0.8),
                color=rsi_color, fontweight='bold')

        # Format x-axis
        ax2.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
        ax2.xaxis.set_major_locator(mdates.MonthLocator())

        # === Subplot 3: MACD ===
        ax3.plot(df.index, df['MACD'], color=MACD_COLOR, linewidth=1.5, label='MACD')
        ax3.plot(df.index, df['Signal'], color=SIGNAL_COLOR, linewidth=1.5, label='Signal')

        # Histogram
        hist_colors = [HIST_UP_COLOR if h >= 0 else HIST_DOWN_COLOR for h in df['Histogram']]
        ax3.bar(df.index, df['Histogram'], color=hist_colors, alpha=0.6, width=0.8)

        # Zero line
        ax3.axhline(y=0, color='gray', linestyle='-', linewidth=0.5)

        ax3.set_ylabel('MACD', fontsize=10)
        ax3.set_xlabel('Date', fontsize=10)
        ax3.set_title(f'MACD ({macd_fast}/{macd_slow}/{macd_signal})', fontsize=11, loc='left')
        ax3.legend(loc='upper left', fontsize=8)
        ax3.grid(True, linestyle='--', alpha=0.3)

        # Current MACD status
        current_macd = df['MACD'].iloc[-1]
        current_signal = df['Signal'].iloc[-1]
        macd_status = "Bullish" if current_macd > current_signal else "Bearish"
        macd_color = 'green' if current_macd > current_signal else 'red'
        ax3.text(0.98, 0.95, f'MACD: {macd_status}',
                transform=ax3.transAxes, ha='right', va='top', fontsize=9,
                bbox=dict(boxstyle='round', facecolor='white', alpha=0.8),
                color=macd_color, fontweight='bold')

        # Format x-axis
        ax3.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
        ax3.xaxis.set_major_locator(mdates.MonthLocator())

        plt.tight_layout()
        return fig

    except Exception as e:
        logger.warning(f"Failed to create technical indicators chart: {e}")
        return None


# =============================================================================
# Wrapper Functions for Easy Integration
# =============================================================================

def get_us_price_chart_html(
    ticker: str,
    company_name: str,
    hist_df: pd.DataFrame,
    width: int = 900,
    dpi: int = 80
) -> Optional[str]:
    """
    Generate price chart and return as base64 HTML.

    Args:
        ticker: Stock ticker symbol
        company_name: Company name
        hist_df: Historical data DataFrame from yfinance
        width: Image width
        dpi: Image resolution

    Returns:
        HTML img tag or None
    """
    fig = create_us_price_chart(ticker, company_name, hist_df)
    if fig is None:
        return None
    return figure_to_base64_html(fig, f"{ticker} Price Chart", width, dpi, 'jpg')


def get_us_institutional_chart_html(
    ticker: str,
    company_name: str,
    major_holders: Optional[pd.DataFrame] = None,
    institutional_holders: Optional[pd.DataFrame] = None,
    width: int = 900,
    dpi: int = 80
) -> Optional[str]:
    """
    Generate institutional holdings chart and return as base64 HTML.

    Args:
        ticker: Stock ticker symbol
        company_name: Company name
        major_holders: Major holders DataFrame from yfinance (optional)
        institutional_holders: Institutional holders DataFrame from yfinance (optional)
        width: Image width
        dpi: Image resolution

    Returns:
        HTML img tag or None
    """
    fig = create_us_institutional_chart(ticker, company_name, major_holders, institutional_holders)
    if fig is None:
        return None
    return figure_to_base64_html(fig, f"{ticker} Institutional Holdings", width, dpi, 'jpg')


def get_us_technical_chart_html(
    ticker: str,
    company_name: str,
    hist_df: pd.DataFrame,
    width: int = 900,
    dpi: int = 80
) -> Optional[str]:
    """
    Generate technical indicators chart and return as base64 HTML.

    Args:
        ticker: Stock ticker symbol
        company_name: Company name
        hist_df: Historical data DataFrame from yfinance
        width: Image width
        dpi: Image resolution

    Returns:
        HTML img tag or None
    """
    fig = create_us_technical_indicators_chart(ticker, company_name, hist_df)
    if fig is None:
        return None
    return figure_to_base64_html(fig, f"{ticker} Technical Indicators", width, dpi, 'jpg')


# =============================================================================
# Test Function
# =============================================================================

if __name__ == "__main__":
    import yfinance as yf

    # Test with AAPL
    ticker = "AAPL"
    company_name = "Apple Inc."

    print(f"Testing US stock charts for {ticker}...")

    stock = yf.Ticker(ticker)
    hist = stock.history(period="1y")

    # Test 1: Price chart
    print("\n1. Testing price chart...")
    price_fig = create_us_price_chart(ticker, company_name, hist)
    if price_fig:
        price_fig.savefig(f"test_{ticker}_price.png", dpi=80, bbox_inches='tight')
        print(f"   Saved: test_{ticker}_price.png")
    else:
        print("   Failed to create price chart")

    # Test 2: Institutional chart
    print("\n2. Testing institutional chart...")
    inst_fig = create_us_institutional_chart(ticker, company_name)
    if inst_fig:
        inst_fig.savefig(f"test_{ticker}_institutional.png", dpi=80, bbox_inches='tight')
        print(f"   Saved: test_{ticker}_institutional.png")
    else:
        print("   Failed to create institutional chart")

    # Test 3: Technical indicators chart
    print("\n3. Testing technical indicators chart...")
    tech_fig = create_us_technical_indicators_chart(ticker, company_name, hist)
    if tech_fig:
        tech_fig.savefig(f"test_{ticker}_technical.png", dpi=80, bbox_inches='tight')
        print(f"   Saved: test_{ticker}_technical.png")
    else:
        print("   Failed to create technical chart")

    print("\nAll tests completed!")
