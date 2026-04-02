"""
market_data.py - 市场数据获取

功能：
- tushare 历史K线获取
- 腾讯接口实时行情
- 技术指标计算（统一使用 indicators.py）

关键变更（2026-04-02）：
- 统一历史波动率 key 为 'hist_volatility'
- 全天固定间距，无时段切换

作者: 西蒙斯之虎 🐯
"""
import time
import logging
import pandas as pd
import numpy as np
import requests
from datetime import datetime, date, timedelta
from typing import Optional, Tuple

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import tushare as ts
from config import (
    TUSHARE_TOKEN, TENGXUN_REALTIME_URL,
    STOCK_CODE, BOLL_PERIOD, ATR_PERIOD,
    GRID_COUNT, HIST_VOL_PERIOD,
    TRADING_MORNING_START, TRADING_MORNING_END,
    TRADING_AFTERNOON_START, TRADING_AFTERNOON_END,
    USE_HIST_VOL,
)
from indicators import (
    calc_atr, 
    calc_bollinger_bands, 
    calc_historical_volatility,
    calc_all_indicators,
    get_grid_spacing,
    HIST_VOL_PERIOD,
    HIST_VOL_MULT,
)

logger = logging.getLogger(__name__)


# ==================== Tushare 客户端 ====================

def get_tushare_client():
    """获取 tushare pro 接口"""
    pro = ts.pro_api(TUSHARE_TOKEN)
    return pro


# ==================== 历史数据获取 ====================

def fetch_daily_history(ts_code: str = STOCK_CODE,
                        start_date: str = "20251201",
                        end_date: str = None) -> pd.DataFrame:
    """
    获取日线历史数据（tushare pro daily 接口）

    Args:
        ts_code: 股票代码，如 000825.SZ
        start_date: 开始日期 YYYYMMDD
        end_date: 结束日期 YYYYMMDD，默认今天

    Returns:
        按日期升序的 DataFrame
    """
    if end_date is None:
        end_date = datetime.now().strftime("%Y%m%d")

    pro = get_tushare_client()
    # 判断是否为基金/ETF（代码以 1或5 开头）
    symbol = ts_code.split('.')[0]
    is_fund = symbol.startswith('1') or symbol.startswith('5')
    
    if is_fund:
        df = pro.fund_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
    else:
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
    df = df.sort_values('trade_date').reset_index(drop=True)

    logger.info(f"[MarketData] 获取历史K线 {ts_code} 从 {start_date} 到 {end_date}，共 {len(df)} 条")
    return df


# ==================== 实时行情获取 ====================

def fetch_realtime_price(qq_code: str = "sz000825", max_retries: int = 3, retry_wait: float = 3.0) -> Optional[float]:
    """
    获取实时价格，优先腾讯接口，失败后自动切换 Tushare 备援

    Args:
        qq_code: 腾讯股票代码，如 sz000825（深圳）、sh600000（上海）
        max_retries: 最大重试次数（默认3次，含首次）
        retry_wait: 重试前等待秒数（默认3秒）

    Returns:
        当前价格（float），全部失败返回 None
    """
    # ---- 优先：腾讯接口 ----
    price = _fetch_tencent_price(qq_code, max_retries, retry_wait)
    if price is not None:
        return price

    # ---- 备援：Tushare ----
    logger.warning(f"[MarketData] 腾讯行情不可用，切换到 Tushare 备援...")
    return _fetch_tushare_realtime_price(qq_code)


def _fetch_tencent_price(qq_code: str, max_retries: int, retry_wait: float) -> Optional[float]:
    """腾讯行情核心请求（内部函数）"""
    url = f"{TENGXUN_REALTIME_URL}{qq_code}"

    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, timeout=5)
            resp.encoding = 'gbk'
            text = resp.text.strip()

            # 解析格式: v_sz000825="1,...,最新价,...,..."
            if '=' not in text:
                logger.warning(f"[MarketData] 腾讯行情格式异常（第{attempt}次）: {text[:80]}")
                if attempt < max_retries:
                    time.sleep(retry_wait)
                    continue
                return None

            parts = text.split('=')[1].strip('"; ')
            fields = parts.split('~')

            if len(fields) < 35:
                logger.warning(f"[MarketData] 腾讯行情字段不足（第{attempt}次）: 期望35+，实际{len(fields)}")
                if attempt < max_retries:
                    time.sleep(retry_wait)
                    continue
                return None

            price_str = fields[3]
            price = float(price_str)

            if attempt > 1:
                logger.info(f"[MarketData] 腾讯重试成功（第{attempt}次）: 价格={price}")
            return price

        except requests.exceptions.Timeout:
            logger.warning(f"[MarketData] 腾讯行情请求超时（第{attempt}次），重试中...")
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"[MarketData] 腾讯行情连接失败（第{attempt}次）: {e}")
        except Exception as e:
            logger.warning(f"[MarketData] 腾讯行情获取异常（第{attempt}次）: {e}")

        if attempt < max_retries:
            time.sleep(retry_wait)

    logger.warning(f"[MarketData] 腾讯行情获取最终失败（已重试{max_retries}次）")
    return None


def _fetch_tushare_realtime_price(qq_code: str) -> Optional[float]:
    """
    Tushare 实时行情备援接口（使用新浪数据源）
    将腾讯代码 sz000825 → tushare 格式 000825.SZ → 新浪格式 sz000825
    """
    try:
        # 转换: sz000825 → 000825.SZ
        symbol = qq_code[2:]  # 去掉 sz 或 sh
        if qq_code.startswith('sz'):
            ts_code = f"{symbol}.SZ"
        else:
            ts_code = f"{symbol}.SH"

        df = ts.realtime_quote(ts_code, src='sina')
        if df is None or df.empty:
            logger.warning("[MarketData] Tushare 实时行情返回空数据")
            return None

        # price 列是当前价格
        price = float(df.iloc[0]['price'])
        logger.info(f"[MarketData] Tushare 实时行情成功: {ts_code} = {price}")
        return price

    except Exception as e:
        logger.error(f"[MarketData] Tushare 实时行情获取失败: {e}")
        return None


def get_qq_code(ts_code: str = STOCK_CODE) -> str:
    """转换 tushare 代码到腾讯代码格式"""
    symbol = ts_code.split('.')[0]  # 000825
    if ts_code.endswith('.SH'):
        return f"sh{symbol}"
    else:
        return f"sz{symbol}"


# ==================== 指标构建 ====================

def build_indicators(df: pd.DataFrame) -> dict:
    """
    从历史数据构建技术指标（统一入口）
    
    注意：统一使用 'hist_volatility' 作为 key 名

    Returns:
        dict: {
            'atr14': float,
            'hist_volatility': float,  # 统一 key 名
            'boll_upper': float,
            'boll_middle': float,
            'boll_lower': float,
            'last_close': float,
            'open_price': float,
            'prev_close': float,
        }
    """
    return calc_all_indicators(df)


def get_today_open_price(ts_code: str = STOCK_CODE,
                          trade_date: str = None) -> Optional[float]:
    """
    获取今日开盘价（从 tushare）
    """
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y%m%d")

    pro = get_tushare_client()
    df = pro.daily(ts_code=ts_code, start_date=trade_date, end_date=trade_date)
    if len(df) > 0:
        return float(df['open'].iloc[0])
    return None


# ==================== MarketDataManager 类 ====================

class MarketDataManager:
    """
    市场数据管理器
    封装历史数据（tushare）和实时数据（腾讯接口）
    
    关键设计：
    - 全天固定网格间距，无时段切换
    - 历史波动率 = 20日涨跌幅标准差
    - 间距 = 价格 × σ × 0.5
    """

    def __init__(self, ts_code: str = STOCK_CODE):
        self.ts_code = ts_code
        self.qq_code = get_qq_code(ts_code)
        self.history_df: Optional[pd.DataFrame] = None
        self.indicators: Optional[dict] = None
        self._today_open: Optional[float] = None
        
        # 预计算的网格间距（固定不变）
        self._grid_spacing: Optional[float] = None

    def initialize(self):
        """初始化：从 tushare 加载历史数据并计算指标"""
        logger.info("[MarketDataManager] 开始初始化...")

        # 获取最近180个交易日历史数据（足够计算20日布林带和20日波动率）
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=180)).strftime("%Y%m%d")

        self.history_df = fetch_daily_history(
            ts_code=self.ts_code,
            start_date=start_date,
            end_date=end_date
        )

        if len(self.history_df) < max(BOLL_PERIOD, HIST_VOL_PERIOD):
            raise ValueError(f"历史数据不足 {max(BOLL_PERIOD, HIST_VOL_PERIOD)} 条，仅获取到 {len(self.history_df)} 条")

        # 使用统一的指标计算函数
        self.indicators = build_indicators(self.history_df)

        # 预计算网格间距（固定不变，全天使用）
        self._grid_spacing = self._calculate_grid_spacing()
        logger.info(f"[MarketDataManager] 固定网格间距: {self._grid_spacing:.4f}")

        # 尝试从腾讯实时行情获取真实的今日开盘价
        realtime_open = fetch_realtime_price(self.qq_code)
        if realtime_open is not None:
            import requests as _req
            try:
                _resp = _req.get(f"{TENGXUN_REALTIME_URL}{self.qq_code}", timeout=5)
                _parts = _resp.text.split('~')
                if len(_parts) > 4 and _parts[4]:
                    self._today_open = float(_parts[4])
                    logger.info(f"[MarketDataManager] 今日开盘从腾讯获取: {self._today_open} (tushare K线最后一根 open={self.indicators['open_price']})")
                else:
                    self._today_open = self.indicators['open_price']
                    logger.warning(f"[MarketDataManager] 腾讯无今日开盘，使用tushare: {self._today_open}")
            except Exception as e:
                self._today_open = self.indicators['open_price']
                logger.warning(f"[MarketDataManager] 腾讯开盘价解析失败，使用tushare: {e}")
        else:
            self._today_open = self.indicators['open_price']

        logger.info(f"[MarketDataManager] 初始化完成: ATR={self.indicators['atr14']:.4f}, "
                    f"历史波动率={self.indicators['hist_volatility']:.4f} ({self.indicators['hist_volatility']*100:.2f}%), "
                    f"布林={self.indicators['boll_lower']:.4f}~{self.indicators['boll_upper']:.4f}, "
                    f"固定间距={self._grid_spacing:.4f}, "
                    f"今日开盘={self._today_open}")

        return self.indicators

    def _calculate_grid_spacing(self) -> float:
        """
        计算网格间距（全天固定）
        
        公式：间距 = 价格 × σ × 0.5
        - σ = 20日涨跌幅标准差（hist_volatility）
        - 0.5 = 每格对应 0.5σ
        """
        if not USE_HIST_VOL:
            # 回退到 ATR 方式（已废弃，仅兼容）
            logger.warning("[MarketDataManager] USE_HIST_VOL=False，回退到ATR方式（已废弃）")
            base_spacing = self.indicators['atr14'] / GRID_COUNT
            return base_spacing
        
        hist_vol = self.indicators.get('hist_volatility', 0)
        if hist_vol is None or hist_vol == 0:
            logger.warning("[MarketDataManager] 历史波动率为0，回退到ATR")
            base_spacing = self.indicators['atr14'] / GRID_COUNT
            return base_spacing
        
        last_close = self.indicators['last_close']
        spacing = get_grid_spacing(last_close, hist_vol, HIST_VOL_MULT)
        
        logger.info(f"[MarketDataManager] 网格间距计算: {spacing:.4f} = 价格{last_close} × σ{hist_vol:.4f} × {HIST_VOL_MULT}")
        return spacing

    def get_realtime_price(self) -> Optional[float]:
        """获取实时价格（腾讯接口）"""
        return fetch_realtime_price(self.qq_code)

    def get_indicators(self) -> dict:
        """获取当前指标数据"""
        return self.indicators or {}

    def get_grid_spacing(self, current_time: datetime = None) -> float:
        """
        获取网格间距（全天固定，无需参数）
        
        注意：此方法保留用于兼容性，不再根据时段切换
        
        Args:
            current_time: 保留参数，仅用于兼容性（已废弃）
        
        Returns:
            每格价格间距
        """
        return self._grid_spacing if self._grid_spacing else 0.0

    def get_hist_volatility(self) -> float:
        """获取历史波动率"""
        return self.indicators.get('hist_volatility', 0) if self.indicators else 0

    def _is_trading_time(self, dt: datetime = None) -> bool:
        """判断是否在交易时段"""
        if dt is None:
            dt = datetime.now()
        hour = dt.hour
        minute = dt.minute
        cur_min = hour * 60 + minute

        mor_start = TRADING_MORNING_START[0] * 60 + TRADING_MORNING_START[1]
        mor_end = TRADING_MORNING_END[0] * 60 + TRADING_MORNING_END[1]
        aft_start = TRADING_AFTERNOON_START[0] * 60 + TRADING_AFTERNOON_START[1]
        aft_end = TRADING_AFTERNOON_END[0] * 60 + TRADING_AFTERNOON_END[1]

        return (mor_start <= cur_min <= mor_end) or (aft_start <= cur_min <= aft_end)

    def get_status(self) -> dict:
        """获取当前市场状态"""
        price = self.get_realtime_price()
        return {
            'price': price,
            'indicators': self.indicators,
            'today_open': self._today_open,
            'is_trading': self._is_trading_time(),
            'grid_spacing': self._grid_spacing,
            'hist_volatility': self.get_hist_volatility(),
        }
