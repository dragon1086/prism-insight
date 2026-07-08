"""
elegant_stock_charts.py - Professional Stock Visualization Tool for AI Stock Reporting

Generates professional-quality stock charts.
Provides investment expert-level visualization and emphasizes data insights.
"""
from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env file

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.font_manager as fm
import seaborn as sns
from matplotlib.ticker import FuncFormatter
import mplfinance as mpf
from datetime import datetime, timedelta
import warnings
import os
import platform
import matplotlib as mpl
import base64
from io import BytesIO
import logging

import matplotlib
matplotlib.use('Agg')  # Explicitly set graphics backend to Agg (non-interactive)

# Logger setup
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)

def configure_korean_font():
    """
    Robust function for Korean font configuration
    Supports Rocky Linux 8, Ubuntu 22.04, macOS, and Windows
    """
    global KOREAN_FONT_PATH, KOREAN_FONT_PROP
    import glob  # For wildcard path processing

    system = platform.system()

    # Basic setup
    plt.rcParams['axes.unicode_minus'] = False

    if system == 'Darwin':  # macOS
        # macOS system and user font paths
        font_paths = [
            '/System/Library/Fonts/AppleSDGothicNeo.ttc',
            '/Library/Fonts/AppleSDGothicNeo.ttc',
            '/System/Library/Fonts/Supplemental/AppleGothic.ttf',
            # Additional paths for manually installed Nanum fonts
            '/Library/Fonts/NanumGothic.ttf',
            # User-specific font directories
            os.path.expanduser('~/Library/Fonts/AppleSDGothicNeo.ttc'),
            os.path.expanduser('~/Library/Fonts/NanumGothic.ttf'),
        ]

        # Check if font files exist and try to load them
        for path in font_paths:
            if os.path.exists(path):
                try:
                    # Register font with matplotlib font manager
                    fm.fontManager.addfont(path)
                    KOREAN_FONT_PATH = path
                    KOREAN_FONT_PROP = fm.FontProperties(fname=path)

                    # Set matplotlib global font configuration
                    plt.rcParams['font.family'] = 'AppleSDGothicNeo'
                    mpl.rcParams['font.family'] = 'AppleSDGothicNeo'

                    logger.info(f"Korean font configured: {path}")
                    return path
                except (OSError, IOError) as e:
                    logger.debug(f"macOS font file access failed: {path} -> {e}")
                    continue
                except (ValueError, TypeError) as e:
                    logger.debug(f"macOS font format error: {path} -> {e}")
                    continue

        # If file path search fails, try searching by font name
        korean_font_list = ['AppleSDGothicNeo-Regular', 'Apple SD Gothic Neo', 'AppleGothic', 'Malgun Gothic', 'NanumGothic']
        for font_name in korean_font_list:
            try:
                # Search installed system fonts by name
                font_path = fm.findfont(fm.FontProperties(family=font_name))
                if font_path and not font_path.endswith('afm'):
                    # Set matplotlib global font configuration
                    plt.rcParams['font.family'] = font_name
                    mpl.rcParams['font.family'] = font_name
                    KOREAN_FONT_PATH = font_path
                    KOREAN_FONT_PROP = fm.FontProperties(family=font_name)

                    logger.info(f"Korean font configured (by name): {font_name}, path: {font_path}")
                    return font_path
            except (AttributeError, KeyError) as e:
                logger.debug(f"macOS font property error: {font_name} -> {e}")
                continue
            except (OSError, IOError) as e:
                logger.debug(f"macOS font file error: {font_name} -> {e}")
                continue

    elif system == 'Windows':
        # Windows default Korean font (Malgun Gothic)
        try:
            plt.rcParams['font.family'] = 'Malgun Gothic'
            mpl.rcParams['font.family'] = 'Malgun Gothic'
            KOREAN_FONT_PROP = fm.FontProperties(family='Malgun Gothic')
            logger.info("Korean font configured: Malgun Gothic (Windows)")
            return "Malgun Gothic"
        except (AttributeError, KeyError) as e:
            logger.debug(f"Windows font configuration failed: {e}")
        except (OSError, RuntimeError) as e:
            logger.debug(f"Windows font system error: {e}")

    else:  # Linux (Rocky Linux 8, Ubuntu 22.04+, and other distributions)
        # Linux distribution-specific font paths
        font_paths = []

        # Rocky Linux 8 / CentOS 8 / RHEL 8 font paths
        rocky_paths = [
            '/usr/share/fonts/google-nanum/NanumGothic.ttf',
            '/usr/share/fonts/nanum/NanumGothicCoding.ttf',
            '/usr/share/fonts/korean/NanumGothic.ttf',
        ]

        # Ubuntu 22.04 / Debian-based distribution font paths
        ubuntu_paths = [
            '/usr/share/fonts/truetype/nanum/NanumGothic.ttf',
            '/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf',
            '/usr/share/fonts/truetype/nanum/NanumMyeongjo.ttf',
            '/usr/share/fonts/opentype/nanum/NanumGothic.ttf',
            '/usr/share/fonts/nanum/NanumGothic.ttf',
        ]

        # Common font paths (for manual installations)
        common_paths = [
            '/usr/share/fonts/NanumGothic.ttf',
            '/usr/local/share/fonts/NanumGothic.ttf',
            '/home/*/fonts/NanumGothic.ttf',
            '/home/*/.fonts/NanumGothic.ttf',
            '/home/*/.local/share/fonts/NanumGothic.ttf',
        ]

        # Combine all paths (priority: Rocky → Ubuntu → Common)
        font_paths = rocky_paths + ubuntu_paths + common_paths

        # Search for font files (supports wildcard patterns like /home/*)
        for path in font_paths:
            try:
                # Handle wildcard paths (e.g., /home/*/fonts)
                if '*' in path:
                    matching_paths = glob.glob(path)
                    for match_path in matching_paths:
                        if os.path.exists(match_path):
                            try:
                                # Register font with matplotlib font manager
                                fm.fontManager.addfont(match_path)
                                KOREAN_FONT_PATH = match_path
                                KOREAN_FONT_PROP = fm.FontProperties(fname=match_path)

                                # Set matplotlib global font configuration
                                plt.rcParams['font.family'] = 'NanumGothic'
                                mpl.rcParams['font.family'] = 'NanumGothic'

                                logger.info(f"Korean font configured: {match_path}")
                                return match_path
                            except (OSError, IOError) as e:
                                logger.debug(f"Linux font file access failed: {match_path} -> {e}")
                                continue
                            except (ValueError, TypeError) as e:
                                logger.debug(f"Linux font format error: {match_path} -> {e}")
                                continue
                            except PermissionError as e:
                                logger.debug(f"Linux font permission error: {match_path} -> {e}")
                                continue
                else:
                    # Handle regular file paths (no wildcards)
                    if os.path.exists(path):
                        try:
                            # Register font with matplotlib font manager
                            fm.fontManager.addfont(path)
                            KOREAN_FONT_PATH = path
                            KOREAN_FONT_PROP = fm.FontProperties(fname=path)

                            # Set matplotlib global font configuration
                            plt.rcParams['font.family'] = 'NanumGothic'
                            mpl.rcParams['font.family'] = 'NanumGothic'

                            logger.info(f"Korean font configured: {path}")
                            return path
                        except (OSError, IOError) as e:
                            logger.debug(f"Linux font file access failed: {path} -> {e}")
                            continue
                        except (ValueError, TypeError) as e:
                            logger.debug(f"Linux font format error: {path} -> {e}")
                            continue
                        except PermissionError as e:
                            logger.debug(f"Linux font permission error: {path} -> {e}")
                            continue
            except (OSError, RuntimeError) as e:
                logger.debug(f"glob pattern processing error: {path} -> {e}")
                continue

        # If file path search fails, try searching with matplotlib font manager
        logger.info("Path search failed, searching with matplotlib font manager...")
        korean_font_names = [
            'NanumGothic', 'Nanum Gothic', 'NanumBarunGothic', 'Nanum Barun Gothic',
            'NanumMyeongjo', 'Nanum Myeongjo', 'UnDotum', 'UnBatang'
        ]

        for font_name in korean_font_names:
            try:
                font_path = fm.findfont(fm.FontProperties(family=font_name))
                if font_path and not font_path.endswith('.afm') and os.path.exists(font_path):
                    # Set matplotlib global font configuration
                    plt.rcParams['font.family'] = font_name
                    mpl.rcParams['font.family'] = font_name
                    KOREAN_FONT_PATH = font_path
                    KOREAN_FONT_PROP = fm.FontProperties(family=font_name)

                    logger.info(f"Korean font configured (via font manager): {font_name} -> {font_path}")
                    return font_path
            except (AttributeError, KeyError) as e:
                logger.debug(f"Linux font manager attribute error: {font_name} -> {e}")
                continue
            except (OSError, IOError) as e:
                logger.debug(f"Linux font manager file error: {font_name} -> {e}")
                continue
            except (ValueError, TypeError) as e:
                logger.debug(f"Linux font manager value error: {font_name} -> {e}")
                continue

    # Display installation instructions when all font detection attempts fail
    logger.info("⚠️ Korean font configuration failed: Korean text may not display properly.")

    # Provide distribution-specific installation instructions
    try:
        if system == 'Linux':
            # Detect Rocky Linux / CentOS / RHEL distributions
            if (os.path.exists('/etc/rocky-release') or
                    os.path.exists('/etc/centos-release') or
                    os.path.exists('/etc/redhat-release')):
                logger.info("Rocky Linux/CentOS/RHEL Korean font installation:")
                logger.info("sudo dnf install google-nanum-fonts")

            # Detect Ubuntu / Debian distributions
            elif (os.path.exists('/etc/debian_version') or
                  os.path.exists('/etc/lsb-release')):
                logger.info("Ubuntu/Debian Korean font installation:")
                logger.info("sudo apt update && sudo apt install fonts-nanum fonts-nanum-coding")

            else:
                logger.info("Linux Korean font installation:")
                logger.info("Install nanum fonts using your package manager.")
        else:
            logger.info("For font installation instructions, refer to README.md.")

    except (OSError, PermissionError) as e:
        logger.debug(f"System information check failed: {e}")
        logger.info("For font installation instructions, refer to project documentation.")
    except FileNotFoundError as e:
        logger.debug(f"System file search failed: {e}")
        logger.info("For font installation instructions, refer to project documentation.")

    return None

# Initialize global font variables
KOREAN_FONT_PATH = None
KOREAN_FONT_PROP = None

# Configure Korean font immediately on module import
KOREAN_FONT_PATH = configure_korean_font()

def get_chart_as_base64_html(ticker, company_name, chart_function, chart_name, width=900,
                             dpi=80, image_format='jpg', compress=True, **kwargs):
    """
    Generate chart, compress it, and return as Base64-encoded HTML image tag

    Args:
        ticker: Stock ticker code
        company_name: Company name
        chart_function: Chart generation function
        chart_name: Chart name
        width: Image width (pixels)
        dpi: Resolution (dots per inch), lower values reduce file size
        image_format: Image format ('png', 'jpg', 'jpeg')
        compress: Whether to apply compression
        **kwargs: Additional parameters to pass to chart function

    Returns:
        String containing HTML image tag
    """
    try:
        # Configure chart generation parameters
        chart_kwargs = {
            'ticker': ticker,
            'company_name': company_name,
            'save_path': None
        }
        chart_kwargs.update(kwargs)

        # Generate chart
        fig = chart_function(**chart_kwargs)

        if fig is None:
            return None

        # Save image to in-memory buffer (apply compression settings)
        buffer = BytesIO()

        # Configure save options based on image format
        save_kwargs = {
            'format': image_format,
            'bbox_inches': 'tight',
            'dpi': dpi
        }

        if image_format.lower() == 'png' and compress:
            save_kwargs['transparent'] = False
            save_kwargs['facecolor'] = 'white'
            save_kwargs['compress_level'] = 9  # Maximum PNG compression (0-9 scale)

        # Save figure to buffer (no quality parameter for PNG)
        fig.savefig(buffer, **save_kwargs)

        plt.close(fig)  # Close figure to prevent memory leak
        buffer.seek(0)

        # Apply additional JPEG compression using PIL if available
        if compress and image_format.lower() in ['jpg', 'jpeg']:
            try:
                from PIL import Image
                # Load image from buffer
                img = Image.open(buffer)
                # Compress and save to new buffer with quality=85
                new_buffer = BytesIO()
                img.save(new_buffer, format='JPEG', quality=85, optimize=True)
                buffer = new_buffer
                buffer.seek(0)
            except ImportError:
                # Continue without PIL if not available
                pass

        # Encode image to Base64 string
        img_str = base64.b64encode(buffer.getvalue()).decode('utf-8')

        # Optional: Log file size for debugging
        # size_kb = len(buffer.getvalue()) / 1024
        # logger.info(f"Chart '{chart_name}' size: {size_kb:.1f} KB")

        # Determine MIME type based on image format
        content_type = f"image/{image_format.lower()}"
        if image_format.lower() == 'jpg':
            content_type = 'image/jpeg'

        # Return HTML image tag with embedded Base64 data
        return f'<img src="data:{content_type};base64,{img_str}" alt="{company_name} {chart_name}" width="{width}" />'

    except Exception as e:
        logger.error(f"Error occurred during chart generation: {str(e)}")
        return  None

# Custom mplfinance style for Korean text display
def create_mpf_style(base_mpl_style='seaborn-v0_8-whitegrid'):
    """
    Generate Korean-compatible chart style for mplfinance library
    Applies Korean font settings and custom color scheme
    """
    # Apply base matplotlib style
    plt.style.use(base_mpl_style)

    # Configure RC parameters with Korean font support
    rc_font = {
        'font.family': plt.rcParams['font.family'],
        'font.size': 10,
        'axes.unicode_minus': False  # Prevent minus sign display issues
    }

    # Define candlestick chart colors
    mc = mpf.make_marketcolors(
        up='#089981', down='#F23645',  # Green for up, red for down
        edge='inherit',
        wick='inherit',
        volume={'up': '#a3f7b5', 'down': '#ffa5a5'},
    )

    # Create mplfinance style with custom settings
    s = mpf.make_mpf_style(
        marketcolors=mc,
        gridstyle='-',
        gridcolor='#e6e6e6',
        gridaxis='both',
        rc=rc_font,
        facecolor='white'
    )

    return s

# Import functions from krx_data_client (pykrx compatible)
from krx_data_client import (
    get_market_ohlcv_by_date,
    get_market_cap_by_date,
    get_market_fundamental_by_date,
    get_market_trading_volume_by_investor,
    get_market_trading_volume_by_date,
    get_market_ticker_name,
    get_index_ohlcv_by_date,
)

# Professional chart style configuration
sns.set_context("paper", font_scale=1.2)
warnings.filterwarnings('ignore')

# Color palette - Professional financial report style
PRIMARY_COLORS = ["#0066cc", "#ff9500", "#00cc99", "#cc3300", "#6600cc"]
SECONDARY_COLORS = ["#e6f2ff", "#fff4e6", "#e6fff7", "#ffe6e6", "#f2e6ff"]
DIVERGING_COLORS = ["#d73027", "#fc8d59", "#fee090", "#e0f3f8", "#91bfdb", "#4575b4"]

def format_thousands(x, pos):
    """Format number with thousands separator"""
    return f'{int(x):,}'

def format_millions(x, pos):
    """Format number in millions"""
    return f'{x/1000000:.1f}M'

def format_billions(x, pos):
    """Format number in billions"""
    return f'{x/1000000000:.1f}B'

def format_trillions(x, pos):
    """Format number in trillions"""
    return f'{x/1000000000000:.1f}T'

def format_percentage(x, pos):
    """Format number as percentage"""
    return f'{x:.1f}%'

def select_number_formatter(max_value):
    """Select appropriate formatter based on data size"""
    if max_value < 1000000:
        return FuncFormatter(format_thousands)
    elif max_value < 1000000000:
        return FuncFormatter(format_millions)
    elif max_value < 1000000000000:
        return FuncFormatter(format_billions)
    else:
        return FuncFormatter(format_trillions)

def create_price_chart(ticker, company_name=None, days=730, save_path=None, adjusted=True):
    """
    Generate elegant OHLCV price chart (including candlestick, volume, moving averages)

    Parameters:
    -----------
    ticker : str
        Stock ticker symbol
    company_name : str, optional
        Company name (for title)
    days : int, optional
        Query period (days)
    save_path : str, optional
        Chart save path (display on screen if None)
    adjusted : bool, optional
        Whether to use adjusted price

    Returns:
    --------
    fig : matplotlib figure
        Figure object containing the chart
    """
    # Calculate date range
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')

    # Fetch company name if not provided
    if company_name is None:
        try:
            company_name = get_market_ticker_name(ticker)
        except Exception:
            company_name = ticker

    # Fetch stock data
    df = get_market_ohlcv_by_date(start_date, end_date, ticker, adjusted=adjusted)

    if df is None or len(df) == 0:
        logger.info(f"No data available for {ticker}.")
        return None

    # Filter out rows with all-zero OHLC values (e.g., newly listed/delisted stocks)
    ohlc_cols = [c for c in ['Open', 'High', 'Low', 'Close'] if c in df.columns]
    if ohlc_cols:
        df = df[df[ohlc_cols].sum(axis=1) > 0]
        if len(df) == 0:
            logger.info(f"No valid OHLCV data for {ticker} (all zeros).")
            return None

    # Verify index is datetime
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)

    # Sort by date ascending
    df = df.sort_index()

    # Calculate moving averages (krx_data_client returns English column names)
    df['MA5'] = df['Close'].rolling(window=5).mean()  # 5-day moving average
    df['MA20'] = df['Close'].rolling(window=20).mean()  # 20-day moving average
    df['MA60'] = df['Close'].rolling(window=60).mean()  # 60-day moving average
    df['MA120'] = df['Close'].rolling(window=120).mean()  # 120-day moving average

    # Prepare OHLCV DataFrame for mplfinance (columns already in English format)
    ohlc_df = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()

    # Create Korean-compatible mplfinance chart style
    s = create_mpf_style()

    # Set chart title
    title = f"{company_name} ({ticker}) - Price Chart"

    # Configure moving average overlay plots
    additional_plots = [
        mpf.make_addplot(df['MA5'], color='#00aa44', width=1),  # 5-day MA (green)
        mpf.make_addplot(df['MA20'], color='#ff9500', width=1),  # 20-day MA (orange)
        mpf.make_addplot(df['MA60'], color='#0066cc', width=1.5),  # 60-day MA (blue)
        mpf.make_addplot(df['MA120'], color='#cc3300', width=1.5, linestyle='--'),  # 120-day MA (red, dashed)
    ]

    if KOREAN_FONT_PATH:
        # Apply Korean font to mplfinance charts
        font_prop = fm.FontProperties(fname=KOREAN_FONT_PATH)
        plt.rcParams['font.family'] = font_prop.get_name()
        mpl.rcParams['font.family'] = font_prop.get_name()

    # Generate candlestick chart with volume
    fig, axes = mpf.plot(
        ohlc_df,
        type='candle',  # Candlestick chart type
        style=s,
        title=title,
        ylabel='Price',
        volume=True,  # Include volume subplot
        figsize=(12, 8),
        tight_layout=True,
        addplot=additional_plots,  # Add moving averages
        panel_ratios=(4, 1),  # Price:Volume height ratio
        returnfig=True
    )

    if KOREAN_FONT_PROP:
        # Apply Korean font to main title
        fig.suptitle(
            f"{company_name} ({ticker}) - Price Chart",
            fontproperties=KOREAN_FONT_PROP,
            fontsize=16,
            fontweight='bold'
        )

    # Extract price and volume subplot axes
    ax1, ax2 = axes[0], axes[2]

    # Add legend for moving averages
    ax1.legend(['MA5', 'MA20', 'MA60', 'MA120'], loc='upper left')

    # Identify key price points for annotation
    max_point = df['Close'].idxmax()  # Highest closing price date
    min_point = df['Close'].idxmin()  # Lowest closing price date
    last_point = df.index[-1]  # Most recent date

    # Define annotation box style
    bbox_props = dict(boxstyle="round,pad=0.3", fc="#f8f9fa", ec="none", alpha=0.9)

    ax1.annotate(
        f"High: {df.loc[max_point, 'Close']:,.0f}",
        xy=(max_point, df.loc[max_point, 'Close']),
        xytext=(0, 15),
        textcoords='offset points',
        ha='center',
        va='bottom',
        bbox=bbox_props
    )

    ax1.annotate(
        f"Low: {df.loc[min_point, 'Close']:,.0f}",
        xy=(min_point, df.loc[min_point, 'Close']),
        xytext=(0, -15),
        textcoords='offset points',
        ha='center',
        va='top',
        bbox=bbox_props
    )

    ax1.annotate(
        f"Current: {df.loc[last_point, 'Close']:,.0f}",
        xy=(last_point, df.loc[last_point, 'Close']),
        xytext=(15, 0),
        textcoords='offset points',
        ha='left',
        va='center',
        bbox=bbox_props
    )

    # Format Y-axis numbers based on price range
    max_price = df['High'].max()
    formatter = select_number_formatter(max_price)
    ax1.yaxis.set_major_formatter(formatter)

    # Add watermark to chart
    fig.text(
        0.99, 0.01,
        "AI Stock Analysis",
        ha='right', va='bottom',
        color='#cccccc',
        fontsize=8
    )

    if save_path:
        # Ensure Korean font is applied before saving
        if KOREAN_FONT_PROP:
            # Apply Korean font to all text elements
            for ax in fig.axes:
                for text in ax.texts:
                    text.set_fontproperties(KOREAN_FONT_PROP)
                for label in ax.get_xticklabels() + ax.get_yticklabels():
                    label.set_fontproperties(KOREAN_FONT_PROP)

                if hasattr(ax, 'title') and ax.title is not None:
                    ax.title.set_fontproperties(KOREAN_FONT_PROP)

                # Apply font to legend text
                if ax.legend_ is not None:
                    for text in ax.legend_.get_texts():
                        text.set_fontproperties(KOREAN_FONT_PROP)

        # Save chart with high resolution
        plt.savefig(save_path, dpi=300, bbox_inches='tight', backend='agg')
        plt.close()
        return save_path
    else:
        plt.tight_layout()
        return fig

def create_market_cap_chart(ticker, company_name=None, days=730, save_path=None):
    """
    Generate market capitalization chart

    Parameters:
    -----------
    ticker : str
        Stock ticker symbol
    company_name : str, optional
        Company name (for title)
    days : int, optional
        Query period (days)
    save_path : str, optional
        Chart save path (display on screen if None)

    Returns:
    --------
    fig : matplotlib figure
        Figure object containing the chart
    """
    # Calculate date range
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')

    # Fetch company name if not provided
    if company_name is None:
        try:
            company_name = get_market_ticker_name(ticker)
        except Exception:
            company_name = ticker

    # Fetch stock data
    df = get_market_cap_by_date(start_date, end_date, ticker)

    if df is None or len(df) == 0:
        logger.info(f"No market cap data available for {ticker}.")
        return None

    # Filter out rows with zero market cap (e.g., newly listed/delisted stocks)
    if 'MarketCap' in df.columns:
        df = df[df['MarketCap'] > 0]
        if len(df) == 0:
            logger.info(f"No valid market cap data for {ticker} (all zeros).")
            return None

    # Verify index is datetime
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)

    # Sort by date ascending
    df = df.sort_index()

    # Create chart
    fig, ax = plt.subplots(figsize=(12, 6))

    # Plot market cap (krx_data_client returns English column name 'MarketCap')
    ax.fill_between(
        df.index,
        0,
        df['MarketCap'],
        color=PRIMARY_COLORS[0],
        alpha=0.2
    )
    ax.plot(
        df.index,
        df['MarketCap'],
        color=PRIMARY_COLORS[0],
        linewidth=2.5
    )

    # Title and label settings
    title = f"{company_name} ({ticker}) - Market Cap Trend"
    if KOREAN_FONT_PROP:
        ax.set_title(title, fontsize=16, fontweight='bold', pad=15, fontproperties=KOREAN_FONT_PROP)
    else:
        ax.set_title(title, fontsize=16, fontweight='bold', pad=15)
    ax.set_ylabel('Market Cap', fontsize=12, labelpad=10)

    ax.set_xlabel('', fontsize=12)

    # Set Y-axis formatter
    max_cap = df['MarketCap'].max()
    formatter = select_number_formatter(max_cap)
    ax.yaxis.set_major_formatter(formatter)

    # Format X-axis dates
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    plt.xticks(rotation=45)

    # Add grid
    ax.grid(axis='y', linestyle='--', alpha=0.7)

    # Add annotations to key points
    latest_point = df.iloc[-1]
    max_point_idx = df['MarketCap'].idxmax()
    max_point = df.loc[max_point_idx]
    min_point_idx = df['MarketCap'].idxmin()
    min_point = df.loc[min_point_idx]

    # Latest point
    bbox_props = dict(boxstyle="round,pad=0.3", fc="#f8f9fa", ec="none", alpha=0.9)

    # Add annotations to key points
    ax.annotate(
        f"{latest_point['MarketCap']/1000000000000:.2f}T KRW",
        xy=(latest_point.name, latest_point['MarketCap']),
        xytext=(10, 0),
        textcoords='offset points',
        ha='left',
        va='center',
        fontsize=10,
        bbox=bbox_props
    )

    # Highest point
    if max_point_idx != df.index[-1]:
        ax.annotate(
            f"High: {max_point['MarketCap']/1000000000000:.2f}T KRW",
            xy=(max_point.name, max_point['MarketCap']),
            xytext=(0, 15),
            textcoords='offset points',
            ha='center',
            va='bottom',
            fontsize=9,
            bbox=bbox_props
        )

    # Lowest point
    if min_point_idx != df.index[0]:
        ax.annotate(
            f"Low: {min_point['MarketCap']/1000000000000:.2f}T KRW",
            xy=(min_point.name, min_point['MarketCap']),
            xytext=(0, -15),
            textcoords='offset points',
            ha='center',
            va='top',
            fontsize=9,
            bbox=bbox_props
        )

    # Calculate and display YTD or 1-year change rate
    first_point = df.iloc[0]
    pct_change = (latest_point['MarketCap'] - first_point['MarketCap']) / first_point['MarketCap'] * 100
    period = "YTD" if df.index[0].year == df.index[-1].year else "1Y"
    change_text = f"{period} Change: {pct_change:.1f}%"

    # Add period change text
    ax.text(
        0.02, 0.95,
        change_text,
        transform=ax.transAxes,
        ha='left',
        va='top',
        fontsize=10,
        bbox=bbox_props
    )

    # Add watermark
    fig.text(
        0.99, 0.01,
        "AI Stock Analysis",
        ha='right', va='bottom',
        color='#cccccc',
        fontsize=8
    )

    plt.tight_layout()

    if save_path:
        # Re-verify Korean font before saving
        if KOREAN_FONT_PROP:
            # Reapply Korean font to important elements
            for text in ax.texts:
                text.set_fontproperties(KOREAN_FONT_PROP)

            for label in ax.get_xticklabels() + ax.get_yticklabels():
                label.set_fontproperties(KOREAN_FONT_PROP)

            if ax.title is not None:
                ax.title.set_fontproperties(KOREAN_FONT_PROP)

            ax.yaxis.label.set_fontproperties(KOREAN_FONT_PROP)

        # Increase resolution and specify backend
        plt.savefig(save_path, dpi=300, bbox_inches='tight', backend='agg')
        plt.close()
        return save_path
    else:
        return fig

def create_fundamentals_chart(ticker, company_name=None, days=730, save_path=None):
    """
    Generate fundamental indicators chart (PER, PBR, Dividend Yield)

    Parameters:
    -----------
    ticker : str
        Stock ticker symbol
    company_name : str, optional
        Company name (for title)
    days : int, optional
        Query period (days)
    save_path : str, optional
        Chart save path (display on screen if None)

    Returns:
    --------
    fig : matplotlib figure
        Figure object containing the chart
    """
    # Calculate date range
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')

    # Get company name if not provided
    if company_name is None:
        try:
            company_name = get_market_ticker_name(ticker)
        except Exception:
            company_name = ticker

    # Fetch stock data
    df = get_market_fundamental_by_date(start_date, end_date, ticker)

    if df is None or len(df) == 0:
        logger.info(f"No fundamental indicator data available for {ticker}.")
        return None

    # Filter out rows where all fundamental values are zero or NaN
    fundamental_cols = [c for c in ['PER', 'PBR', 'DIV', 'BPS', 'EPS', 'DPS'] if c in df.columns]
    if fundamental_cols:
        df = df.dropna(subset=fundamental_cols, how='all')
        df = df[df[fundamental_cols].sum(axis=1) != 0]
        if len(df) == 0:
            logger.info(f"No valid fundamental data for {ticker} (all zeros/NaN).")
            return None

    # Check if index is datetime
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)

    # Sort by date in ascending order
    df = df.sort_index()

    # Create subplots
    fig, axes = plt.subplots(3, 1, figsize=(12, 12), sharex=True)

    # Format X-axis dates
    for ax in axes:
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))

    # 1. PER (Price-to-Earnings Ratio) plot
    axes[0].plot(df.index, df['PER'], color=PRIMARY_COLORS[0], linewidth=2.5)

    # Title and label settings
    axes[0].set_title("Price-to-Earnings Ratio (PER)", fontsize=12, loc='left')
    axes[0].set_ylabel('PER', fontsize=11)

    axes[0].grid(linestyle='--', alpha=0.7)

    # Add PER annotations
    latest_per = df['PER'].iloc[-1]
    min_per_idx = df['PER'].idxmin()
    max_per_idx = df['PER'].idxmax()

    bbox_props = dict(boxstyle="round,pad=0.3", fc="#f8f9fa", ec="none", alpha=0.9)

    axes[0].annotate(
        f"Current: {latest_per:.2f}",
        xy=(df.index[-1], latest_per),
        xytext=(10, 0),
        textcoords='offset points',
        ha='left',
        va='center',
        fontsize=9,
        bbox=bbox_props
    )

    # Add industry average comparison (example - actual data should be fetched from API)
    avg_per = df['PER'].mean()  # Use average value
    axes[0].axhline(y=avg_per, color='gray', linestyle='--', alpha=0.7)

    axes[0].annotate(
        f"Avg: {avg_per:.2f}",
        xy=(df.index[0], avg_per),
        xytext=(5, 5),
        textcoords='offset points',
        ha='left',
        va='bottom',
        fontsize=8,
        bbox=bbox_props
    )

    # 2. PBR (Price-to-Book Ratio) plot
    axes[1].plot(df.index, df['PBR'], color=PRIMARY_COLORS[1], linewidth=2.5)

    axes[1].set_title("Price-to-Book Ratio (PBR)", fontsize=12, loc='left')
    axes[1].set_ylabel('PBR', fontsize=11)

    axes[1].grid(linestyle='--', alpha=0.7)

    # Add PBR annotations
    latest_pbr = df['PBR'].iloc[-1]

    axes[1].annotate(
        f"Current: {latest_pbr:.2f}",
        xy=(df.index[-1], latest_pbr),
        xytext=(10, 0),
        textcoords='offset points',
        ha='left',
        va='center',
        fontsize=9,
        bbox=bbox_props
    )

    # Add industry average comparison
    avg_pbr = df['PBR'].mean()  # Use average value
    axes[1].axhline(y=avg_pbr, color='gray', linestyle='--', alpha=0.7)

    axes[1].annotate(
        f"Avg: {avg_pbr:.2f}",
        xy=(df.index[0], avg_pbr),
        xytext=(5, 5),
        textcoords='offset points',
        ha='left',
        va='bottom',
        fontsize=8,
        bbox=bbox_props
    )

    # 3. Dividend Yield (DIV) plot
    if 'DIV' in df.columns:
        axes[2].plot(df.index, df['DIV'], color=PRIMARY_COLORS[2], linewidth=2.5)

        axes[2].set_title("Dividend Yield (%)", fontsize=12, loc='left')
        axes[2].set_ylabel('Yield (%)', fontsize=11)

        axes[2].grid(linestyle='--', alpha=0.7)

        # Add dividend annotations
        latest_div = df['DIV'].iloc[-1]

        axes[2].annotate(
            f"Current: {latest_div:.2f}%",
            xy=(df.index[-1], latest_div),
            xytext=(10, 0),
            textcoords='offset points',
            ha='left',
            va='center',
            fontsize=9,
            bbox=bbox_props
        )

        # Add industry average comparison
        avg_div = df['DIV'].mean()  # Use average value
        axes[2].axhline(y=avg_div, color='gray', linestyle='--', alpha=0.7)

        axes[2].annotate(
            f"Avg: {avg_div:.2f}%",
            xy=(df.index[0], avg_div),
            xytext=(5, 5),
            textcoords='offset points',
            ha='left',
            va='bottom',
            fontsize=8,
            bbox=bbox_props
        )

    # Overall title
    if KOREAN_FONT_PROP:
        fig.suptitle(
            f"{company_name} ({ticker}) - Fundamental Analysis",
            fontsize=16,
            fontweight='bold',
            y=0.98,
            fontproperties=KOREAN_FONT_PROP
        )
    else:
        fig.suptitle(
            f"{company_name} ({ticker}) - Fundamental Analysis",
            fontsize=16,
            fontweight='bold',
            y=0.98
        )

    # Enhance label font settings
    for ax in axes:
        ax.tick_params(labelsize=9)  # Axis label font size

        # Set Korean font
        if KOREAN_FONT_PROP:
            for label in ax.get_yticklabels():
                label.set_fontproperties(KOREAN_FONT_PROP)
            for label in ax.get_xticklabels():
                label.set_fontproperties(KOREAN_FONT_PROP)

    # X-axis date display format
    plt.xticks(rotation=45)

    # Add watermark
    fig.text(
        0.99, 0.01,
        "AI Stock Analysis",
        ha='right', va='bottom',
        color='#cccccc',
        fontsize=8
    )

    plt.tight_layout()
    plt.subplots_adjust(top=0.93)

    if save_path:
        # Explicitly re-apply Korean font before saving
        if KOREAN_FONT_PROP:
            for ax in fig.axes:
                for text in ax.texts:
                    text.set_fontproperties(KOREAN_FONT_PROP)

                for label in ax.get_xticklabels() + ax.get_yticklabels():
                    label.set_fontproperties(KOREAN_FONT_PROP)

                if hasattr(ax, 'title') and ax.title is not None:
                    ax.title.set_fontproperties(KOREAN_FONT_PROP)

                if hasattr(ax, 'yaxis') and hasattr(ax.yaxis, 'label'):
                    ax.yaxis.label.set_fontproperties(KOREAN_FONT_PROP)

            if hasattr(fig, '_suptitle') and fig._suptitle is not None:
                fig._suptitle.set_fontproperties(KOREAN_FONT_PROP)

        # Increase resolution and specify backend
        plt.savefig(save_path, dpi=300, bbox_inches='tight', backend='agg')
        plt.close()
        return save_path
    else:
        return fig

def create_trading_volume_chart(ticker, company_name=None, days=30, save_path=None):
    """
    Generate trading volume chart by investor type

    Parameters:
    -----------
    ticker : str
        Stock ticker symbol
    company_name : str, optional
        Company name (for title)
    days : int, optional
        Query period (days) - default 30 days for supply/demand analysis
    save_path : str, optional
        Chart save path (display on screen if None)

    Returns:
    --------
    fig : matplotlib figure
        Figure object containing the chart
    """
    # Calculate date range
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')

    # Get company name if not provided
    if company_name is None:
        try:
            company_name = get_market_ticker_name(ticker)
        except Exception:
            company_name = ticker

    # Fetch stock data - trading volume by investor
    df_volume = get_market_trading_volume_by_investor(start_date, end_date, ticker)

    if df_volume is None or len(df_volume) == 0:
        logger.info(f"No trading volume data available for {ticker}.")
        return None

    # Fetch daily net purchase data
    df_daily = get_market_trading_volume_by_date(start_date, end_date, ticker)

    if df_daily is None or len(df_daily) == 0:
        logger.info(f"No daily trading volume data available for {ticker}.")
        return None

    # Create chart
    fig, axes = plt.subplots(2, 1, figsize=(12, 10), gridspec_kw={'height_ratios': [2, 3]})

    # 1. Net purchase analysis by major investor groups
    # Korean investor type names (as returned by pykrx API)
    investor_types = ['기관합계', '외국인합계', '개인', '기타법인']

    # Korean to English investor name mapping (comprehensive pykrx field mapping)
    investor_names_en = {
        # Primary investor categories
        '기관합계': 'Institutions',
        '외국인합계': 'Foreigners',
        '개인': 'Individuals',
        '기타법인': 'Other Corps',
        # Institutional subcategories
        '금융투자': 'Securities',
        '보험': 'Insurance',
        '투신': 'Mutual Funds',
        '사모': 'Private Equity',
        '은행': 'Banks',
        '기타금융': 'Other Finance',
        '연기금등': 'Pension Funds',
        '연기금 등': 'Pension Funds',  # pykrx spacing variant
        # Additional categories
        '전체': 'Total',
        '외국인': 'Foreigners',
        '기관': 'Institutions',
        '기타외국인': 'Other Foreigners',  # Additional pykrx investor type
    }

    # Calculate cumulative net purchases by investor type (sum of daily data)
    # Extract columns that match our investor types
    investor_cols = [col for col in df_volume.columns if col in investor_types]
    if investor_cols:
        investor_data = df_volume[investor_cols].sum()

        # Create bar chart
        bar_width = 0.6
        pos = np.arange(len(investor_data))

        bars = axes[0].bar(
            pos,
            investor_data,
            bar_width,
            color=[PRIMARY_COLORS[i % len(PRIMARY_COLORS)] for i in range(len(investor_data))],
            alpha=0.7
        )

        # Set subplot title
        axes[0].set_title("Net Purchase by Investor Type", fontsize=12, loc='left')

        axes[0].set_xticks(pos)

        # Convert Korean labels to English for display
        english_labels = [investor_names_en.get(name, name) for name in investor_data.index]
        axes[0].set_xticklabels(english_labels, rotation=45)

        # Add zero reference line
        axes[0].axhline(y=0, color='black', linestyle='-', alpha=0.3)
        axes[0].grid(axis='y', linestyle='--', alpha=0.7)

        # Format Y-axis numbers based on value range
        max_vol = investor_data.max()
        min_vol = investor_data.min()
        formatter = select_number_formatter(max(abs(max_vol), abs(min_vol)))
        axes[0].yaxis.set_major_formatter(formatter)

        # Add value labels on bars
        for bar in bars:
            height = bar.get_height()
            value = int(height)
            va = 'bottom' if height >= 0 else 'top'
            y_pos = 0.3 if height >= 0 else -0.3

            # Apply Korean font if available
            if KOREAN_FONT_PROP:
                axes[0].text(
                    bar.get_x() + bar.get_width()/2.,
                    height + (height * 0.02 if height >= 0 else height * 0.02),
                    f'{value:,}',
                    ha='center',
                    va=va,
                    fontsize=9,
                    rotation=0,
                    fontproperties=KOREAN_FONT_PROP
                )
            else:
                axes[0].text(
                    bar.get_x() + bar.get_width()/2.,
                    height + (height * 0.02 if height >= 0 else height * 0.02),
                    f'{value:,}',
                    ha='center',
                    va=va,
                    fontsize=9,
                    rotation=0
                )

    # 2. Daily cumulative net purchase trend
    # Ensure index is datetime type
    if not isinstance(df_daily.index, pd.DatetimeIndex):
        df_daily.index = pd.to_datetime(df_daily.index)

    # Sort data by date in ascending order
    df_daily = df_daily.sort_index()

    # Filter to key investor types only
    key_investors = [col for col in df_daily.columns if col in investor_types]

    # Calculate cumulative sum of daily net purchases
    df_cumulative = df_daily[key_investors].cumsum()

    # Plot time series for each investor type
    for i, investor in enumerate(key_investors):
        # Translate Korean name to English for legend
        english_label = investor_names_en.get(investor, investor)
        axes[1].plot(
            df_cumulative.index,
            df_cumulative[investor],
            color=PRIMARY_COLORS[i % len(PRIMARY_COLORS)],
            linewidth=2,
            label=english_label
        )

    # Set subplot title and labels
    axes[1].set_title("Daily Cumulative Net Purchase Trend", fontsize=12, loc='left')

    axes[1].set_xlabel('')
    axes[1].axhline(y=0, color='black', linestyle='-', alpha=0.3)  # Zero reference line
    axes[1].grid(linestyle='--', alpha=0.7)

    # Add legend for investor types
    legend = axes[1].legend(loc='upper left')

    # Format Y-axis numbers based on value range
    max_vol = df_cumulative.max().max()
    min_vol = df_cumulative.min().min()
    formatter = select_number_formatter(max(abs(max_vol), abs(min_vol)))
    axes[1].yaxis.set_major_formatter(formatter)

    # Format X-axis dates
    axes[1].xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
    axes[1].xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
    plt.setp(axes[1].xaxis.get_majorticklabels(), rotation=45, ha='right')

    # Overall title
    if KOREAN_FONT_PROP:
        fig.suptitle(
            f"{company_name} ({ticker}) - Trading by Investor Type",
            fontsize=16,
            fontweight='bold',
            y=0.98,
            fontproperties=KOREAN_FONT_PROP
        )
    else:
        fig.suptitle(
            f"{company_name} ({ticker}) - Trading by Investor Type",
            fontsize=16,
            fontweight='bold',
            y=0.98
        )

    # Add watermark
    fig.text(
        0.99, 0.01,
        "AI Stock Analysis",
        ha='right', va='bottom',
        color='#cccccc',
        fontsize=8
    )

    plt.tight_layout()
    plt.subplots_adjust(top=0.93)

    if save_path:
        # Explicitly re-apply Korean font before saving
        if KOREAN_FONT_PROP:
            for ax in fig.axes:
                for text in ax.texts:
                    text.set_fontproperties(KOREAN_FONT_PROP)

                for label in ax.get_xticklabels() + ax.get_yticklabels():
                    label.set_fontproperties(KOREAN_FONT_PROP)

                if hasattr(ax, 'title') and ax.title is not None:
                    ax.title.set_fontproperties(KOREAN_FONT_PROP)

                # Apply font to legend text as well
                if ax.legend_ is not None:
                    for text in ax.legend_.get_texts():
                        text.set_fontproperties(KOREAN_FONT_PROP)

            if hasattr(fig, '_suptitle') and fig._suptitle is not None:
                fig._suptitle.set_fontproperties(KOREAN_FONT_PROP)

        # Increase resolution and specify backend
        plt.savefig(save_path, dpi=300, bbox_inches='tight', backend='agg')
        plt.close()
        return save_path
    else:
        return fig

def create_comprehensive_report(ticker, company_name=None, days=730, output_dir='charts'):
    """
    Generate comprehensive stock analysis report with multiple charts

    Parameters:
    -----------
    ticker : str
        Stock ticker symbol
    company_name : str, optional
        Company name (for title)
    days : int, optional
        Query period (days)
    output_dir : str, optional
        Chart save directory

    Returns:
    --------
    report_paths : dict
        Dictionary of generated chart image paths
    """
    # Fetch company name if not provided
    if company_name is None:
        try:
            company_name = get_market_ticker_name(ticker)
        except Exception:
            company_name = ticker

    # Create output directory (if not exists)
    os.makedirs(output_dir, exist_ok=True)

    # Generate report ID
    report_id = datetime.now().strftime('%Y%m%d_%H%M%S')

    # Create report subdirectory
    report_dir = os.path.join(output_dir, f"{ticker}_{report_id}")
    os.makedirs(report_dir, exist_ok=True)

    report_paths = {}

    # Generate price chart
    price_path = os.path.join(report_dir, f"{ticker}_price.png")
    try:
        create_price_chart(ticker, company_name, days, save_path=price_path)
        report_paths['price_chart'] = price_path
    except Exception as e:
        logger.info(f"Price chart generation error: {e}")

    # Generate market cap chart
    marketcap_path = os.path.join(report_dir, f"{ticker}_marketcap.png")
    try:
        create_market_cap_chart(ticker, company_name, days, save_path=marketcap_path)
        report_paths['market_cap_chart'] = marketcap_path
    except Exception as e:
        logger.info(f"Market cap chart generation error: {e}")

    # Generate fundamentals chart
    fundamentals_path = os.path.join(report_dir, f"{ticker}_fundamentals.png")
    try:
        create_fundamentals_chart(ticker, company_name, days, save_path=fundamentals_path)
        report_paths['fundamentals_chart'] = fundamentals_path
    except Exception as e:
        logger.info(f"Fundamentals chart generation error: {e}")

    # Generate trading volume chart (fixed 30 days for supply/demand analysis)
    volume_path = os.path.join(report_dir, f"{ticker}_volume.png")
    try:
        create_trading_volume_chart(ticker, company_name, days=30, save_path=volume_path)
        report_paths['trading_volume_chart'] = volume_path
    except Exception as e:
        logger.info(f"Trading volume chart generation error: {e}")

    return report_paths


# =========================================================================== #
# O'Neil / CAN SLIM vision-only charts (Phase 6 S3.5).                         #
#                                                                              #
# These are ADDITIVE, vision-only helpers. They do NOT replace or modify the  #
# existing human-report charts above. They add an RS (Relative Strength) line  #
# vs the market index and a weekly timeframe so the vision buy-quality         #
# analyzer can ground BaseAnalysis.rs_line_new_high and weekly-base reading    #
# instead of hallucinating. Both return a matplotlib `fig` (like               #
# create_price_chart) so get_chart_as_base64_html can render them.             #
# =========================================================================== #

# KRX index tickers (pykrx get_index_ohlcv): composite index per market.
_KOSPI_INDEX_TICKER = "1001"
_KOSDAQ_INDEX_TICKER = "2001"

# Module-level cache of the KOSPI ticker set, used for market detection.
# Populated lazily on first call; failures fall back to KOSPI default.
_KOSPI_TICKERS_CACHE: set | None = None


def _detect_index_ticker(ticker: str) -> str:
    """Return the index ticker for *ticker*'s listing market.

    KOSPI(1001) if the stock is KOSPI-listed, otherwise KOSDAQ(2001).
    On any detection failure, defaults to KOSPI (1001). Never raises.
    """
    global _KOSPI_TICKERS_CACHE
    try:
        from pykrx import stock as _pykrx_stock

        if _KOSPI_TICKERS_CACHE is None:
            _KOSPI_TICKERS_CACHE = set(
                _pykrx_stock.get_market_ticker_list(market="KOSPI")
            )
        if ticker in _KOSPI_TICKERS_CACHE:
            return _KOSPI_INDEX_TICKER
        return _KOSDAQ_INDEX_TICKER
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"[ONEIL] market detection failed for {ticker}, "
            f"defaulting to KOSPI index: {e}"
        )
        return _KOSPI_INDEX_TICKER


def _fetch_index_close(index_ticker: str, start_date: str, end_date: str):
    """Fetch index daily close series via the authenticated krx_data_client.

    Uses get_index_ohlcv_by_date (the project-standard authenticated wrapper)
    instead of raw pykrx, which fails under the auth layer and returns empty.
    Returns a pandas Series indexed by DatetimeIndex (close prices), or None on
    failure. Never raises.
    """
    try:
        idf = get_index_ohlcv_by_date(start_date, end_date, index_ticker)
        if idf is None or len(idf) == 0:
            return None
        if not isinstance(idf.index, pd.DatetimeIndex):
            idf.index = pd.to_datetime(idf.index)
        close_col = "종가" if "종가" in idf.columns else (
            "Close" if "Close" in idf.columns else None
        )
        if close_col is None:
            return None
        s = idf[close_col].sort_index()
        s = s[s > 0]
        return s if len(s) > 0 else None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ONEIL] index fetch failed for {index_ticker}: {e}")
        return None


def _compute_rs_line(stock_close, index_close):
    """Compute the O'Neil RS line = (stock/index) normalized to 100 at window start.

    Aligns both series on their common dates, then normalizes the ratio to 100
    at the first common date. Returns a pandas Series aligned to the stock's
    index (reindexed/forward-filled to the stock dates), or None if alignment
    yields nothing usable.
    """
    try:
        idx = index_close.reindex(stock_close.index).ffill().bfill()
        ratio = stock_close / idx
        ratio = ratio.replace([np.inf, -np.inf], np.nan).dropna()
        if len(ratio) == 0:
            return None
        base = ratio.iloc[0]
        if base == 0:
            return None
        rs = ratio / base * 100.0
        return rs.reindex(stock_close.index)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ONEIL] RS line computation failed: {e}")
        return None


def _add_rs_panel(fig, rs_series, label):
    """Draw the RS line as a clearly readable dedicated bottom subpanel on *fig*.

    Adds a taller bottom axis (kept out of the way of the mplfinance
    price/volume panels) so the RS line is legible rather than a thin strip.
    Marks EVERY point where the RS line makes a new high (running-max), with the
    most recent new high emphasised and annotated. Never raises; on error the
    chart is unchanged.
    """
    try:
        if rs_series is None:
            return
        rs_clean = rs_series.dropna()
        if len(rs_clean) == 0:
            return
        # Dedicated, clearly-sized axis at the bottom of the figure. Taller than
        # the previous 0.10 strip so the RS structure is actually readable.
        rs_ax = fig.add_axes([0.08, 0.02, 0.84, 0.18])
        x = list(range(len(rs_clean)))
        rs_vals = rs_clean.values
        rs_ax.plot(x, rs_vals, color="#8e44ad", linewidth=1.3)

        # Mark ALL RS new highs (running-max equals current value).
        running_max = rs_clean.cummax()
        is_new_high = rs_vals >= running_max.values
        nh_x = [i for i, flag in enumerate(is_new_high) if bool(flag)]
        if nh_x:
            rs_ax.scatter(
                nh_x,
                [rs_vals[i] for i in nh_x],
                marker="^",
                color="#27ae60",
                s=18,
                zorder=3,
                label="RS new high",
            )
        # Emphasise + annotate the most recent new high (O'Neil's strongest tell).
        # Guard against an empty new-high array (avoids "index -1 out of bounds
        # for axis 0 with size 0" when the RS series degenerates).
        if len(is_new_high) > 0 and bool(is_new_high[-1]):
            last_i = len(rs_vals) - 1
            rs_ax.scatter(
                [last_i],
                [rs_vals[last_i]],
                marker="^",
                color="#1e8449",
                s=70,
                zorder=4,
            )
            ann_kw = dict(fontsize=7, color="#1e8449", ha="right", va="bottom")
            if KOREAN_FONT_PROP:
                ann_kw["fontproperties"] = KOREAN_FONT_PROP
            rs_ax.annotate("RS 신고가", (last_i, rs_vals[last_i]), **ann_kw)

        rs_ax.set_xticks([])
        rs_ax.tick_params(axis="y", labelsize=7)
        rs_ax.margins(x=0.01)
        rs_ax.grid(True, axis="y", alpha=0.2)
        # Clear, fixed y-label per S6 spec (independent of which index was used).
        rs_ylabel = "RS vs KOSPI/KOSDAQ"
        rs_title = f"RS vs {label} (norm=100 at start)"
        if KOREAN_FONT_PROP:
            rs_ax.set_ylabel(rs_ylabel, fontsize=8, fontproperties=KOREAN_FONT_PROP)
            rs_ax.set_title(rs_title, fontsize=7, fontproperties=KOREAN_FONT_PROP)
        else:
            rs_ax.set_ylabel(rs_ylabel, fontsize=8)
            rs_ax.set_title(rs_title, fontsize=7)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ONEIL] RS panel draw failed: {e}")


def _resolve_chart_window(days, end_date=None):
    """Resolve ``(start_date, end_date)`` as ``'YYYYMMDD'`` strings for a chart.

    Look-ahead control for historical (as-of) rendering:

    - ``end_date=None`` → live behaviour (unchanged): end = today,
      start = today - ``days``.
    - otherwise ``end_date`` is the as-of cutoff and may be a ``datetime`` /
      ``date`` / ``'YYYYMMDD'`` / ``'YYYY-MM-DD'`` string; end = that date,
      start = end - ``days``. Nothing after the cutoff is ever fetched.
    """
    if end_date is None:
        end_dt = datetime.now()
    elif isinstance(end_date, str):
        s = end_date.replace('-', '').replace('/', '').strip()[:8]
        end_dt = datetime.strptime(s, '%Y%m%d')
    else:
        # datetime.datetime or datetime.date
        end_dt = datetime(end_date.year, end_date.month, end_date.day)
    end_s = end_dt.strftime('%Y%m%d')
    start_s = (end_dt - timedelta(days=days)).strftime('%Y%m%d')
    return start_s, end_s


def create_oneil_daily_chart(
    ticker,
    company_name=None,
    days=400,
    adjusted=True,
    save_path=None,
    market=None,
    index_ticker=None,
    return_df=False,
    end_date=None,
):
    """Generate an O'Neil DAILY chart (vision-only): candles + MA5/20/60/120 +
    volume + an RS line vs the market index.

    Reuses create_price_chart's data-fetch + styling + Korean-font setup.
    ~400 trading days so the most recent base is not over-compressed.

    Args:
        ticker:       Stock ticker symbol.
        company_name: Company name for the title (auto-fetched if None).
        days:         Lookback window in calendar days (default 400).
        adjusted:     Use adjusted prices (default True).
        save_path:    If given, save the figure there; otherwise just return it.
        market:       Optional 'KOSPI'/'KOSDAQ' hint to pick the index.
        index_ticker: Optional explicit index ticker override (e.g. '1001').
        return_df:    If True, return ``(fig, ohlc_df)`` so callers can map
                      dates to mplfinance candle index positions. The returned
                      df has the SAME row ordering as the plotted candles
                      (position i <-> df.index[i]).
        end_date:     Optional as-of cutoff (datetime/date/'YYYYMMDD'/
                      'YYYY-MM-DD'). None → today (unchanged). When given, the
                      window is [end_date - days, end_date] so no future data
                      leaks into the rendered chart (look-ahead-free backtest).

    Returns:
        matplotlib figure (or ``(fig, df)`` when ``return_df=True``), or None
        if stock data is unavailable.
    """
    start_date, end_date = _resolve_chart_window(days, end_date)

    if company_name is None:
        try:
            company_name = get_market_ticker_name(ticker)
        except Exception:
            company_name = ticker

    df = get_market_ohlcv_by_date(start_date, end_date, ticker, adjusted=adjusted)
    if df is None or len(df) == 0:
        logger.info(f"[ONEIL] No daily data for {ticker}.")
        return None

    ohlc_cols = [c for c in ['Open', 'High', 'Low', 'Close'] if c in df.columns]
    if ohlc_cols:
        df = df[df[ohlc_cols].sum(axis=1) > 0]
        if len(df) == 0:
            logger.info(f"[ONEIL] No valid daily OHLCV for {ticker}.")
            return None

    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    df = df.sort_index()

    df['MA5'] = df['Close'].rolling(window=5).mean()
    df['MA20'] = df['Close'].rolling(window=20).mean()
    df['MA60'] = df['Close'].rolling(window=60).mean()
    df['MA120'] = df['Close'].rolling(window=120).mean()

    ohlc_df = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
    s = create_mpf_style()

    # Resolve index for RS line.
    if index_ticker is None:
        if market and market.upper() == "KOSPI":
            index_ticker = _KOSPI_INDEX_TICKER
        elif market and market.upper() in ("KOSDAQ", "KOSDAK"):
            index_ticker = _KOSDAQ_INDEX_TICKER
        else:
            index_ticker = _detect_index_ticker(ticker)
    index_label = "KOSPI" if index_ticker == _KOSPI_INDEX_TICKER else "KOSDAQ"
    index_close = _fetch_index_close(index_ticker, start_date, end_date)
    rs_series = _compute_rs_line(df['Close'], index_close) if index_close is not None else None
    if rs_series is None:
        logger.warning(
            f"[ONEIL] RS line omitted for {ticker} (daily); chart still rendered."
        )

    additional_plots = [
        mpf.make_addplot(df['MA5'], color='#00aa44', width=1),
        mpf.make_addplot(df['MA20'], color='#ff9500', width=1),
        mpf.make_addplot(df['MA60'], color='#0066cc', width=1.5),
        mpf.make_addplot(df['MA120'], color='#cc3300', width=1.5, linestyle='--'),
    ]

    if KOREAN_FONT_PATH:
        font_prop = fm.FontProperties(fname=KOREAN_FONT_PATH)
        plt.rcParams['font.family'] = font_prop.get_name()
        mpl.rcParams['font.family'] = font_prop.get_name()

    if rs_series is not None:
        title = f"{company_name} ({ticker}) - O'Neil Daily (RS vs {index_label})"
    else:
        title = f"{company_name} ({ticker}) - O'Neil Daily"
    fig, axes = mpf.plot(
        ohlc_df,
        type='candle',
        style=s,
        title=title,
        ylabel='Price',
        volume=True,
        figsize=(12, 9),
        tight_layout=True,
        addplot=additional_plots,
        panel_ratios=(4, 1),
        returnfig=True,
    )

    if KOREAN_FONT_PROP:
        fig.suptitle(title, fontproperties=KOREAN_FONT_PROP, fontsize=15, fontweight='bold')

    ax1 = axes[0]
    ax1.legend(['MA5', 'MA20', 'MA60', 'MA120'], loc='upper left')
    max_price = df['High'].max()
    ax1.yaxis.set_major_formatter(select_number_formatter(max_price))

    # RS line as a dedicated thin bottom panel (clearly labeled).
    _add_rs_panel(fig, rs_series, index_label)

    fig.text(0.99, 0.005, "AI Stock Analysis", ha='right', va='bottom',
             color='#cccccc', fontsize=8)

    if save_path:
        fig.savefig(save_path, bbox_inches='tight', dpi=80)
        logger.info(f"[ONEIL] daily chart saved: {save_path}")

    if return_df:
        return fig, ohlc_df
    return fig


def create_oneil_weekly_chart(
    ticker,
    company_name=None,
    weeks=104,
    adjusted=True,
    save_path=None,
    market=None,
    index_ticker=None,
    end_date=None,
):
    """Generate an O'Neil WEEKLY chart (vision-only): weekly candles + 10-week
    and 40-week MAs (O'Neil standard) + weekly volume + an RS line vs the index.

    Fetches ~ (weeks*7 + buffer) days of DAILY OHLCV ONCE and resamples to
    weekly with pandas (open=first, high=max, low=min, close=last, volume=sum)
    — no extra per-bar fetch.

    Args:
        ticker:       Stock ticker symbol.
        company_name: Company name for the title (auto-fetched if None).
        weeks:        Number of weeks to display (default 104 ~= 2 years).
        adjusted:     Use adjusted prices (default True).
        save_path:    If given, save the figure there; otherwise just return it.
        market:       Optional 'KOSPI'/'KOSDAQ' hint to pick the index.
        index_ticker: Optional explicit index ticker override.
        end_date:     Optional as-of cutoff (datetime/date/'YYYYMMDD'/
                      'YYYY-MM-DD'). None → today (unchanged). When given, the
                      window ends at end_date so no future data leaks into the
                      rendered weekly chart (look-ahead-free backtest).

    Returns:
        matplotlib figure, or None if stock data is unavailable.
    """
    # Fetch daily ONCE with a buffer, then resample to weekly.
    # KRX rejects periods longer than ~730 days (INVALIDPERIOD2), so clamp.
    _KRX_MAX_DAYS = 730
    days = min(weeks * 7 + 40, _KRX_MAX_DAYS)
    start_date, end_date = _resolve_chart_window(days, end_date)

    if company_name is None:
        try:
            company_name = get_market_ticker_name(ticker)
        except Exception:
            company_name = ticker

    daily = get_market_ohlcv_by_date(start_date, end_date, ticker, adjusted=adjusted)
    if daily is None or len(daily) == 0:
        logger.info(f"[ONEIL] No daily data for {ticker} (weekly).")
        return None

    ohlc_cols = [c for c in ['Open', 'High', 'Low', 'Close'] if c in daily.columns]
    if ohlc_cols:
        daily = daily[daily[ohlc_cols].sum(axis=1) > 0]
        if len(daily) == 0:
            logger.info(f"[ONEIL] No valid daily OHLCV for {ticker} (weekly).")
            return None

    if not isinstance(daily.index, pd.DatetimeIndex):
        daily.index = pd.to_datetime(daily.index)
    daily = daily.sort_index()

    # Resample daily -> weekly (O'Neil aggregation).
    weekly = daily.resample('W').agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
        'Volume': 'sum',
    }).dropna(subset=['Open', 'High', 'Low', 'Close'])

    if len(weekly) == 0:
        logger.info(f"[ONEIL] Weekly resample empty for {ticker}.")
        return None

    # Keep only the most recent `weeks` bars.
    weekly = weekly.tail(weeks)

    # O'Neil standard weekly MAs: 10-week and 40-week.
    weekly['MA10'] = weekly['Close'].rolling(window=10).mean()
    weekly['MA40'] = weekly['Close'].rolling(window=40).mean()

    ohlc_df = weekly[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
    s = create_mpf_style()

    # Resolve index for RS line, fetch over the same window, resample to weekly.
    if index_ticker is None:
        if market and market.upper() == "KOSPI":
            index_ticker = _KOSPI_INDEX_TICKER
        elif market and market.upper() in ("KOSDAQ", "KOSDAK"):
            index_ticker = _KOSDAQ_INDEX_TICKER
        else:
            index_ticker = _detect_index_ticker(ticker)
    index_label = "KOSPI" if index_ticker == _KOSPI_INDEX_TICKER else "KOSDAQ"
    index_daily = _fetch_index_close(index_ticker, start_date, end_date)
    rs_series = None
    if index_daily is not None:
        try:
            index_weekly = index_daily.resample('W').last()
            rs_series = _compute_rs_line(weekly['Close'], index_weekly)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[ONEIL] weekly RS resample failed for {ticker}: {e}")
    if rs_series is None:
        logger.warning(
            f"[ONEIL] RS line omitted for {ticker} (weekly); chart still rendered."
        )

    additional_plots = [
        mpf.make_addplot(weekly['MA10'], color='#ff9500', width=1.2),
        mpf.make_addplot(weekly['MA40'], color='#cc3300', width=1.5, linestyle='--'),
    ]

    if KOREAN_FONT_PATH:
        font_prop = fm.FontProperties(fname=KOREAN_FONT_PATH)
        plt.rcParams['font.family'] = font_prop.get_name()
        mpl.rcParams['font.family'] = font_prop.get_name()

    if rs_series is not None:
        title = f"{company_name} ({ticker}) - O'Neil Weekly (RS vs {index_label})"
    else:
        title = f"{company_name} ({ticker}) - O'Neil Weekly"
    fig, axes = mpf.plot(
        ohlc_df,
        type='candle',
        style=s,
        title=title,
        ylabel='Price',
        volume=True,
        figsize=(12, 9),
        tight_layout=True,
        addplot=additional_plots,
        panel_ratios=(4, 1),
        returnfig=True,
    )

    if KOREAN_FONT_PROP:
        fig.suptitle(title, fontproperties=KOREAN_FONT_PROP, fontsize=15, fontweight='bold')

    ax1 = axes[0]
    ax1.legend(['MA10 (10wk)', 'MA40 (40wk)'], loc='upper left')
    max_price = weekly['High'].max()
    ax1.yaxis.set_major_formatter(select_number_formatter(max_price))

    _add_rs_panel(fig, rs_series, index_label)

    fig.text(0.99, 0.005, "AI Stock Analysis", ha='right', va='bottom',
             color='#cccccc', fontsize=8)

    if save_path:
        fig.savefig(save_path, bbox_inches='tight', dpi=80)
        logger.info(f"[ONEIL] weekly chart saved: {save_path}")

    return fig


def check_font_available():
    """
    Check Korean font availability and provide installation guidance
    Returns list of available Korean fonts on the system
    """
    # Get list of all available fonts
    available_fonts = [f.name for f in fm.fontManager.ttflist]

    # Filter fonts that likely support Korean (by font name keywords)
    possible_korean_fonts = [
        f for f in available_fonts if any(keyword in f for keyword in
                                          ['Gothic', 'Hangul', '고딕', 'Apple', 'Nanum', '나눔', 'Malgun', 'SD', 'Gulim', '굴림', 'Batang', '바탕', 'Dotum', '돋움'])
    ]

    # Filter fonts containing Korean characters in their name
    korean_fonts = [f for f in available_fonts if any(char in f for char in '가나다라마바사아자차카타파하')]

    # Combine both lists and remove duplicates
    all_korean_fonts = list(set(possible_korean_fonts + korean_fonts))

    if not all_korean_fonts:
        logger.info("⚠️ Warning: No Korean fonts found on system!")
        logger.info("Please install Korean fonts using one of the following methods:")

        if platform.system() == 'Windows':
            logger.info("Windows should have 'Malgun Gothic' font installed by default.")
        elif platform.system() == 'Darwin':  # macOS
            logger.info("Install Korean fonts on macOS:")
            logger.info("- Nanum Gothic (https://hangeul.naver.com/font)")
            logger.info("- Or use macOS built-in 'Apple SD Gothic Neo'.")
        else:  # Linux
            if os.path.exists('/etc/rocky-release'):  # Rocky Linux
                logger.info("Rocky Linux 8 Korean font installation:")
                logger.info("sudo dnf install google-nanum-fonts")
            else:  # Other Linux distributions
                logger.info("Linux: Nanum Gothic font installation instructions")
                logger.info("Ubuntu/Debian: sudo apt-get install fonts-nanum")
                logger.info("CentOS/RHEL: sudo dnf install google-nanum-fonts")
                logger.info("Manual install: Download Nanum Gothic fonts and copy to ~/.fonts")

        logger.info("\nAfter font installation, rebuild matplotlib font cache:")
        logger.info("import matplotlib.font_manager as fm")
        logger.info("fm.fontManager.rebuild()  # For latest matplotlib")
        logger.info("# or fm._rebuild()  # For older matplotlib versions")
    else:
        logger.info(f"Found {len(all_korean_fonts)} available Korean fonts:")
        for i, font in enumerate(all_korean_fonts[:10]):  # Show maximum 10 fonts
            logger.info(f"  {i+1}. {font}")
        if len(all_korean_fonts) > 10:
            logger.info(f"  ... and {len(all_korean_fonts)-10} more")

    return all_korean_fonts

def main():
    """
    Example usage of stock chart generation functions
    Demonstrates comprehensive report generation for Samsung Electronics
    """
    # Check available Korean fonts on system
    korean_fonts = check_font_available()

    # Example: Generate charts for Samsung Electronics
    ticker = "005930"  # Samsung Electronics ticker
    company_name = "삼성전자"  # Samsung Electronics (Korean)

    # Generate comprehensive analysis report with all charts
    report_paths = create_comprehensive_report(ticker, company_name)

    logger.info("Generated comprehensive report with the following charts:")
    for chart_type, path in report_paths.items():
        logger.info(f"- {chart_type}: {path}")

    # Individual chart generation examples (uncomment to use):

    # Generate and display price chart
    #fig_price = create_price_chart(ticker, company_name)
    #plt.figure(fig_price.number)
    #plt.show()

    # Generate and display market cap chart
    #fig_mc = create_market_cap_chart(ticker, company_name)
    #plt.figure(fig_mc.number)
    #plt.show()

    # Generate and display fundamentals chart
    #fig_fund = create_fundamentals_chart(ticker, company_name)
    #plt.figure(fig_fund.number)
    #plt.show()

    # Generate and display trading volume chart
    #fig_vol = create_trading_volume_chart(ticker, company_name, days=60)
    #plt.figure(fig_vol.number)
    #plt.show()

if __name__ == "__main__":
    main()