"""
market_data.py - 市场数据获取
- tushare 历史K线
- 腾讯接口实时行情
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
    STOCK_CODE, BOLL_PERIOD, ATR_PERIOD
)
from indicators import calc_atr, calc_bollinger_bands

logger = logging.getLogger(__name__)


def get_tushare_client():
    """获取 tushare pro 接口"""
    pro = ts.pro_api(TUSHARE_TOKEN)
    return pro


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
    df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
    df = df.sort_values('trade_date').reset_index(drop=True)

    logger.info(f"[MarketData] 获取历史K线 {ts_code} 从 {start_date} 到 {end_date}，共 {len(df)} 条")
    return df


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


def build_indicators(df: pd.DataFrame) -> dict:
    """
    从历史数据构建技术指标

    Returns:
        dict: {
            'atr14': float,
            'boll_upper': float,
            'boll_middle': float,
            'boll_lower': float,
            'last_close': float,
            'open_price': float,
            'prev_close': float,
        }
    """
    if len(df) < BOLL_PERIOD:
        raise ValueError(f"历史数据不足，需要至少 {BOLL_PERIOD} 条，当前 {len(df)} 条")

    atr_series = calc_atr(df, period=ATR_PERIOD)
    sma, upper, lower = calc_bollinger_bands(df, period=BOLL_PERIOD)

    result = {
        'atr14': float(atr_series.iloc[-1]),
        'boll_upper': float(upper.iloc[-1]),
        'boll_middle': float(sma.iloc[-1]),
        'boll_lower': float(lower.iloc[-1]),
        'last_close': float(df['close'].iloc[-1]),
        'open_price': float(df['open'].iloc[-1]),
        'prev_close': float(df['close'].iloc[-2]) if len(df) >= 2 else None,
        'trade_date': str(df['trade_date'].iloc[-1]),
    }
    return result


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


class MarketDataManager:
    """
    市场数据管理器
    封装历史数据（tushare）和实时数据（腾讯接口）
    """

    def __init__(self, ts_code: str = STOCK_CODE):
        self.ts_code = ts_code
        self.qq_code = get_qq_code(ts_code)
        self.history_df: Optional[pd.DataFrame] = None
        self.indicators: Optional[dict] = None
        self._today_open: Optional[float] = None

    def initialize(self):
        """初始化：从 tushare 加载历史数据并计算指标"""
        logger.info("[MarketDataManager] 开始初始化...")

        # 获取最近100个交易日历史数据（足够计算20日布林带和14日ATR）
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=180)).strftime("%Y%m%d")

        self.history_df = fetch_daily_history(
            ts_code=self.ts_code,
            start_date=start_date,
            end_date=end_date
        )

        if len(self.history_df) < BOLL_PERIOD:
            raise ValueError(f"历史数据不足 {BOLL_PERIOD} 条，仅获取到 {len(self.history_df)} 条")

        self.indicators = build_indicators(self.history_df)

        # 尝试从腾讯实时行情获取真实的今日开盘价
        # tushare 日线在交易时段不含今日数据，所以以昨日 K 线最后一根的 open 当"今日开盘"是错的
        realtime_open = fetch_realtime_price(self.qq_code)
        if realtime_open is not None:
            # 用腾讯实时行情的今开（字段4），但需要从腾讯数据中解析
            # fetch_realtime_price 只返回当前价，所以我们直接解析腾讯原始数据
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
                    f"布林={self.indicators['boll_lower']:.4f}~{self.indicators['boll_upper']:.4f}, "
                    f"今日开盘={self._today_open}")

        return self.indicators

    def get_realtime_price(self) -> Optional[float]:
        """获取实时价格（腾讯接口）"""
        return fetch_realtime_price(self.qq_code)

    def get_grid_spacing(self, current_time: datetime = None) -> float:
        """
        根据当前时间计算动态网格间距

        从 GRID_SPACING_RULES 读取配置，根据当前时间匹配最近的规则时段。
        规则是"从该时间点开始使用该倍数"，例如 ((9,30), 1.75) 表示 09:30 之后用 1.75，
        直到下一个时间点。

        Args:
            current_time: datetime（默认 now）

        Returns:
            每格价格间距
        """
        from config import GRID_SPACING_RULES, BASE_MULTIPLIER, GRID_COUNT

        if current_time is None:
            current_time = datetime.now()

        cur_min = current_time.hour * 60 + current_time.minute

        base_spacing = self.indicators['atr14'] / GRID_COUNT

        # 找到当前时间所属的规则时段
        # 规则按时间升序排列，找到最后一个 (hour, minute) <= cur_min 的规则
        multiplier = BASE_MULTIPLIER  # 默认倍数
        for (rule_hour, rule_minute), rule_mult in GRID_SPACING_RULES:
            rule_total_min = rule_hour * 60 + rule_minute
            if cur_min >= rule_total_min:
                multiplier = rule_mult

        return base_spacing * multiplier

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
            'grid_spacing': self.get_grid_spacing() if self.indicators else None,
        }
