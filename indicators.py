"""
indicators.py - 技术指标计算（ATR、布林带）
"""
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


def calc_true_range(high: float, low: float, prev_close: float) -> float:
    """
    计算单一周期的真实波幅（True Range）
    TR = max(H-L, |H-PC|, |L-PC|)
    """
    tr1 = high - low
    tr2 = abs(high - prev_close)
    tr3 = abs(low - prev_close)
    return max(tr1, tr2, tr3)


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
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


def calc_bollinger_bands(df: pd.DataFrame, period: int = 20, std_mult: float = 2.0) -> tuple:
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


def calc_atr_vectorized(df: pd.DataFrame, period: int = 14) -> float:
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


def verify_atr_calculation():
    """
    验证 ATR 计算的正确性
    使用手动逐日计算对比 pandas 向量计算结果
    """
    import os
    import tushare as ts

    token = os.getenv("TUSHARE_TOKEN") or "d11513bc2e258334d01ddf0db02d45793325443dc1260931691d1552"
    pro = ts.pro_api(token)
    df = pro.daily(ts_code='000825.SZ', start_date='20251201', end_date='20260324')
    df = df.sort_values('trade_date').reset_index(drop=True)

    period = 14
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
    print("ATR 计算验证（太钢不锈 000825.SZ）")
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
