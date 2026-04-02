"""
indicators.py - 技术指标计算

统一历史波动率体系：
- σ = 20日涨跌幅标准差
- 间距 = 价格 × σ × 0.5
- 档位定义：level±1 = ±0.5σ, ±2 = ±1.0σ, ±3 = ±1.5σ...

作者: 西蒙斯之虎 🐯
"""
import pandas as pd
import numpy as np
import logging
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from config import STOCK_CODE

logger = logging.getLogger(__name__)


# ==================== 常量定义 ====================
# 历史波动率计算参数
HIST_VOL_PERIOD = 20  # 20日涨跌幅标准差
HIST_VOL_MULT = 0.5  # 每格 = 0.5σ

# ATR 计算参数
ATR_PERIOD = 14

# 布林带计算参数
BOLL_PERIOD = 20
BOLL_STD_MULT = 2.0


# ==================== ATR 计算 ====================

def calc_true_range(high: float, low: float, prev_close: float) -> float:
    """
    计算单一周期的真实波幅（True Range）
    TR = max(H-L, |H-PC|, |L-PC|)
    """
    tr1 = high - low
    tr2 = abs(high - prev_close)
    tr3 = abs(low - prev_close)
    return max(tr1, tr2, tr3)


def calc_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    """
    计算 ATR(14) 平均真实波幅
    使用 pandas 高效向量运算

    Args:
        df: 包含 high, low, close 列的 DataFrame（按日期升序）
        period: ATR 周期

    Returns:
        ATR Series（与 df 索引对齐）
    """
    prev_close = df['close'].shift(1)

    tr1 = df['high'] - df['low']
    tr2 = (df['high'] - prev_close).abs()
    tr3 = (df['low'] - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period, min_periods=period).mean()

    logger.info(f"[ATR] 计算完成，周期={period}，最新ATR={atr.iloc[-1]:.4f}")
    return atr


def calc_atr_vectorized(df: pd.DataFrame, period: int = ATR_PERIOD) -> float:
    """
    高效计算最新 ATR 值（纯向量版本，用于实时场景）
    返回最新的 ATR 均值
    """
    prev_close = df['close'].shift(1)

    tr1 = df['high'] - df['low']
    tr2 = (df['high'] - prev_close).abs()
    tr3 = (df['low'] - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr_series = tr.rolling(window=period, min_periods=period).mean()

    return float(atr_series.iloc[-1])


# ==================== 布林带计算 ====================

def calc_bollinger_bands(df: pd.DataFrame, period: int = BOLL_PERIOD, std_mult: float = BOLL_STD_MULT) -> tuple:
    """
    计算布林带（20日均线 ± 2σ）

    Returns:
        (sma, upper_band, lower_band): tuple of pd.Series
    """
    sma = df['close'].rolling(window=period, min_periods=period).mean()
    std = df['close'].rolling(window=period, min_periods=period).std()
    upper_band = sma + std_mult * std
    lower_band = sma - std_mult * std

    logger.info(f"[BOLL] 计算完成，周期={period}，最新中轨={sma.iloc[-1]:.4f}，上轨={upper_band.iloc[-1]:.4f}，下轨={lower_band.iloc[-1]:.4f}")
    return sma, upper_band, lower_band


# ==================== 历史波动率计算（核心） ====================

def calc_historical_volatility(df: pd.DataFrame, period: int = HIST_VOL_PERIOD) -> float:
    """
    计算历史波动率（Historical Volatility）
    使用过去 N 天的对数收益率标准差
    
    Args:
        df: 包含 close 列的 DataFrame（按日期升序）
        period: 计算周期（默认20天）
    
    Returns:
        历史波动率（float），例如 0.02 表示 2%
        
    网格间距计算公式：
        间距 = 价格 × σ × 0.5
        
    档位定义：
        level±1 = ±0.5σ
        level±2 = ±1.0σ
        level±3 = ±1.5σ
        ...
    """
    # 计算日对数收益率
    returns = np.log(df['close'] / df['close'].shift(1))
    
    # 取最近 period 个收益率计算标准差
    recent_returns = returns.dropna().iloc[-period:]
    
    # 返回日波动率（不是年化）
    hist_vol = recent_returns.std()
    
    logger.info(f"[HistoricalVolatility] 周期={period}，历史波动率={hist_vol:.6f} ({hist_vol*100:.2f}%)")
    logger.info(f"[HistoricalVolatility] 档位定义: level±1=±0.5σ, ±2=±1.0σ, ±3=±1.5σ...")
    
    return float(hist_vol)


def calc_historical_volatility_vectorized(df: pd.DataFrame, period: int = HIST_VOL_PERIOD) -> float:
    """
    高效计算最新历史波动率（纯向量版本，用于实时场景）
    返回最新的历史波动率
    """
    returns = np.log(df['close'] / df['close'].shift(1))
    hist_vol_series = returns.rolling(window=period, min_periods=period).std()
    
    return float(hist_vol_series.iloc[-1])


# ==================== 便捷函数 ====================

def calc_all_indicators(df: pd.DataFrame) -> dict:
    """
    计算所有技术指标（统一入口）
    
    Args:
        df: 包含 high, low, close 列的 DataFrame（按日期升序）
    
    Returns:
        dict: {
            'atr14': float,
            'hist_volatility': float,  # 统一使用这个 key 名
            'boll_upper': float,
            'boll_middle': float,
            'boll_lower': float,
            'last_close': float,
            'open_price': float,
            'prev_close': float,
        }
    """
    if len(df) < max(BOLL_PERIOD, HIST_VOL_PERIOD):
        raise ValueError(f"历史数据不足，需要至少 {max(BOLL_PERIOD, HIST_VOL_PERIOD)} 条，当前 {len(df)} 条")
    
    # ATR
    atr = calc_atr(df, period=ATR_PERIOD)
    atr_value = float(atr.iloc[-1])
    
    # 布林带
    sma, upper, lower = calc_bollinger_bands(df, period=BOLL_PERIOD)
    
    # 历史波动率（统一 key 名）
    hist_vol = calc_historical_volatility(df, period=HIST_VOL_PERIOD)
    
    # tushare K线数据按日期升序排列：iloc[0]=最老日期，iloc[-1]=最新日期
    result = {
        'atr14': atr_value,
        'hist_volatility': hist_vol,  # 统一 key 名
        'boll_upper': float(upper.iloc[-1]),
        'boll_middle': float(sma.iloc[-1]),
        'boll_lower': float(lower.iloc[-1]),
        'last_close': float(df['close'].iloc[-1]),
        'open_price': float(df['open'].iloc[-1]),
        'prev_close': float(df['close'].iloc[-2]) if len(df) >= 2 else None,
        'trade_date': str(df['trade_date'].iloc[-1]),
    }
    
    logger.info(f"[Indicators] 所有指标计算完成: ATR={result['atr14']:.4f}, "
                f"历史波动率={result['hist_volatility']:.4f} ({result['hist_volatility']*100:.2f}%), "
                f"布林={result['boll_lower']:.4f}~{result['boll_upper']:.4f}")
    
    return result


def get_grid_spacing(base_price: float, hist_volatility: float, mult: float = HIST_VOL_MULT) -> float:
    """
    计算网格间距
    
    公式：间距 = 价格 × σ × 0.5
    
    Args:
        base_price: 基准价格
        hist_volatility: 历史波动率（0.02 表示 2%）
        mult: 倍数（默认 0.5，即每格 0.5σ）
    
    Returns:
        每格价格间距
    """
    spacing = base_price * hist_volatility * mult
    logger.info(f"[GridSpacing] 基准价={base_price}, σ={hist_volatility:.4f} ({hist_volatility*100:.2f}%), "
                f"倍数={mult} -> 间距={spacing:.4f}")
    return spacing


def level_to_price_offset(level: int, spacing: float) -> float:
    """
    将档位转换为价格偏移量
    
    Args:
        level: 档位（0=基准价, +1=上涨1格, -1=下跌1格）
        spacing: 每格间距
    
    Returns:
        价格偏移量
    """
    return level * spacing


def price_to_level(price: float, base_price: float, spacing: float) -> int:
    """
    将价格转换为档位
    
    Args:
        price: 当前价格
        base_price: 基准价格
        spacing: 每格间距
    
    Returns:
        档位（向上取整）
    """
    if spacing == 0:
        return 0
    return int(round((price - base_price) / spacing))


# ==================== ATR 验证工具 ====================

def verify_atr_calculation():
    """
    验证 ATR 计算的正确性
    使用手动逐日计算对比 pandas 向量计算结果
    """
    import os
    import tushare as ts

    token = os.getenv("TUSHARE_TOKEN") or "d11513bc2e258334d01ddf0db02d45793325443dc1260931691d1552"
    pro = ts.pro_api(token)
    df = pro.daily(ts_code=STOCK_CODE, start_date='20251201', end_date='20260324')
    df = df.sort_values('trade_date').reset_index(drop=True)

    period = ATR_PERIOD
    prev_close = df['close'].shift(1)

    # Method 1: pandas vectorized
    tr1 = df['high'] - df['low']
    tr2 = (df['high'] - prev_close).abs()
    tr3 = (df['low'] - prev_close).abs()
    tr_vec = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr_vec = tr_vec.rolling(window=period, min_periods=period).mean()

    # Method 2: manual loop (step-by-step)
    tr_manual = []
    for i in range(len(df)):
        if i == 0:
            tr_manual.append(np.nan)
        else:
            h = df.loc[i, 'high']
            l = df.loc[i, 'low']
            pc = df.loc[i - 1, 'close']
            tr = max(h - l, abs(h - pc), abs(l - pc))
            tr_manual.append(tr)

    # ATR 手动计算（ Wilder 平滑）
    atr_manual = []
    for i in range(len(df)):
        if i < period - 1:
            atr_manual.append(np.nan)
        elif i == period - 1:
            # 第一个 ATR = 前 period 个 TR 的简单均值
            atr_manual.append(np.mean(tr_manual[:period]))
        else:
            # 后续 ATR = (前ATR * (period-1) + 当前TR) / period
            prev_atr = atr_manual[-1]
            curr_tr = tr_manual[i]
            atr_manual.append((prev_atr * (period - 1) + curr_tr) / period)

    # 比较
    print("=" * 70)
    print(f"ATR 计算验证（{STOCK_CODE}）")
    print("=" * 70)
    print(f"{'日期':<12} {'TR':>8} {'pandas向量ATR':>14} {'手动WilderATR':>14} {'差异':>10}")
    print("-" * 70)

    errors = []
    for i in range(period - 1, len(df)):
        date = df.loc[i, 'trade_date']
        tr_val = tr_manual[i]
        vec_val = atr_vec.iloc[i]
        man_val = atr_manual[i]
        diff = abs(vec_val - man_val)
        errors.append(diff)
        print(f"{date:<12} {tr_val:>8.4f} {vec_val:>14.4f} {man_val:>14.4f} {diff:>10.6f}")

    print("-" * 70)
    print(f"pandas ATR(最新): {atr_vec.iloc[-1]:.6f}")
    print(f"手动 Wilder ATR(最新): {atr_manual[-1]:.6f}")
    print(f"最大误差: {max(errors):.8f}")
    print(f"平均误差: {np.mean(errors):.8f}")

    # 用 numpy 手动验证前几个 TR
    print()
    print("=== 前5条 TR 手动验证 ===")
    for i in range(1, min(6, len(df))):
        h = df.loc[i, 'high']
        l = df.loc[i, 'low']
        pc = df.loc[i - 1, 'close']
        tr = max(h - l, abs(h - pc), abs(l - pc))
        print(f"  [{df.loc[i,'trade_date']}] H={h}, L={l}, PC={pc} -> TR={tr:.4f} | pandas TR={tr_vec.iloc[i]:.4f}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    verify_atr_calculation()
