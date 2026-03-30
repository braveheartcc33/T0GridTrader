#!/usr/bin/env python3
"""
159567.SZ 网格交易回测 - 2026-03-27
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import tushare as ts
import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta

# ========== 参数 ==========
STOCK_CODE = "159567.SZ"
TRADE_DATE = "20260327"
BASE_PRICE = 0.710
GRID_COUNT = 10
SHARES_PER_GRID = 1000
INITIAL_BASE_SHARES = 10000
ATR14 = 0.0258
GRID_SPACING = 0.0026  # 每格间距（任务给定）

# Tushare token
TUSHARE_TOKEN = "d11513bc2e258334d01ddf0db02d45793325443dc1260931691d1552"

# 网格档位边界
MAX_LEVEL = GRID_COUNT // 2   # 5
MIN_LEVEL = -MAX_LEVEL        # -5

# 交易时段
MORNING_START = (9, 30)
MORNING_END = (11, 30)
AFTERNOON_START = (13, 0)
AFTERNOON_END = (15, 0)

# 动态倍数规则（从config）
GRID_SPACING_RULES = [
    ((9, 30), 4.00),
    ((10, 0), 3.00),
    ((11, 0), 2.00),
    ((11, 30), 3.00),
    ((13, 0), 4.00),
    ((13, 30), 3.00),
    ((14, 30), 2.00),
    ((15, 0), 0.00),
]

def get_spacing_with_multiplier(dt):
    cur_min = dt.hour * 60 + dt.minute
    multiplier = 1.0
    for (hour, minute), rule_mult in sorted(GRID_SPACING_RULES, reverse=True):
        if cur_min >= hour * 60 + minute:
            multiplier = rule_mult
            break
    return GRID_SPACING * multiplier, multiplier

def is_trading_time(dt):
    cur_min = dt.hour * 60 + dt.minute
    mor_s = MORNING_START[0] * 60 + MORNING_START[1]
    mor_e = MORNING_END[0] * 60 + MORNING_END[1]
    aft_s = AFTERNOON_START[0] * 60 + AFTERNOON_START[1]
    aft_e = AFTERNOON_END[0] * 60 + AFTERNOON_END[1]
    return (mor_s <= cur_min <= mor_e) or (aft_s <= cur_min <= aft_e)

def is_closing_window(dt):
    cur_min = dt.hour * 60 + dt.minute
    aft_e = AFTERNOON_END[0] * 60 + AFTERNOON_END[1]
    return cur_min >= (aft_e - 30)

def price_to_level(price, spacing):
    return int(round((price - BASE_PRICE) / spacing))

# ========== 获取分钟K线数据 ==========
print("=" * 80)
print(f"159567.SZ 网格交易回测 - 2026-03-27")
print("=" * 80)
print(f"基准价: {BASE_PRICE} | 网格档位: {GRID_COUNT}档 | 每格间距: {GRID_SPACING} | ATR(14): {ATR14}")
print(f"持仓底仓: {INITIAL_BASE_SHARES}股 | 每格股数: {SHARES_PER_GRID}")
print(f"布林上轨: 0.7604 | 中轨: 0.7213 | 下轨: 0.6823")
print("=" * 80)

pro = ts.pro_api(TUSHARE_TOKEN)

# 尝试获取5分钟K线（fund_daily分钟接口）
try:
    df = pro.fund_daily(ts_code=STOCK_CODE, trade_date=TRADE_DATE)
    print(f"[数据] fund_daily 返回 {len(df)} 条")
except Exception as e:
    print(f"[数据] fund_daily 失败: {e}")
    df = pd.DataFrame()

# 尝试分钟接口
if df.empty:
    try:
        df_min = pro.stk_mins(ts_code=STOCK_CODE, freq='5min', start_date=TRADE_DATE+' 09:30:00', end_date=TRADE_DATE+' 15:00:00')
        print(f"[数据] stk_mins(5min) 返回 {len(df_min) if df_min is not None else 0} 条")
        if df_min is not None and not df_min.empty:
            df = df_min.sort_values('ts').reset_index(drop=True)
    except Exception as e:
        print(f"[数据] stk_mins 失败: {e}")

if df.empty or df is None:
    try:
        df_min = pro.stk_mins(ts_code=STOCK_CODE, freq='1min', start_date=TRADE_DATE+' 09:30:00', end_date=TRADE_DATE+' 15:00:00')
        print(f"[数据] stk_mins(1min) 返回 {len(df_min) if df_min is not None else 0} 条")
        if df_min is not None and not df_min.empty:
            df = df_min.sort_values('ts').reset_index(drop=True)
    except Exception as e:
        print(f"[数据] stk_mins(1min) 失败: {e}")

# 如果还是空，尝试bar接口
if df.empty or df is None:
    try:
        df_bar = ts.bar(STOCK_CODE, conn=pro, freq='5min', start_date=TRADE_DATE)
        print(f"[数据] ts.bar(5min) 返回 {len(df_bar) if df_bar is not None else 0} 条")
        if df_bar is not None and not df_bar.empty:
            df = df_bar.reset_index()
    except Exception as e:
        print(f"[数据] ts.bar 失败: {e}")

print(f"\n最终使用数据量: {len(df)} 条")
if not df.empty:
    print(f"列名: {df.columns.tolist()}")
    print(f"前3条:\n{df.head(3)}")
    print(f"后3条:\n{df.tail(3)}")

# ========== 回测引擎 ==========
if df.empty:
    print("无法获取数据，退出")
    sys.exit(1)

# 统一处理时间戳
if 'ts' in df.columns:
    df['datetime'] = pd.to_datetime(df['ts'])
elif 'trade_time' in df.columns:
    df['datetime'] = pd.to_datetime(df['trade_time'])
elif 'datetime' in df.columns:
    df['datetime'] = pd.to_datetime(df['datetime'])
else:
    # 尝试构建
    if 'date' in df.columns and 'time' in df.columns:
        df['datetime'] = pd.to_datetime(df['date'] + ' ' + df['time'])
    else:
        df['datetime'] = pd.to_datetime(df.iloc[:, 0])

# 统一价格列
price_col = None
for col in ['close', 'price', 'open']:
    if col in df.columns:
        price_col = col
        break
if price_col is None:
    price_col = df.columns[-1]  # 取最后一列

df['price'] = pd.to_numeric(df[price_col], errors='coerce')
df = df.dropna(subset=['price', 'datetime']).sort_values('datetime').reset_index(drop=True)

print(f"\n清洗后数据: {len(df)} 条")
print(f"价格范围: {df['price'].min():.4f} ~ {df['price'].max():.4f}")
print(f"时间范围: {df['datetime'].min()} ~ {df['datetime'].max()}")

# ========== 模拟网格 ==========
# 状态
current_position = INITIAL_BASE_SHARES
base_position = INITIAL_BASE_SHARES
yesterday_position = INITIAL_BASE_SHARES  # 假设就是底仓
position_cost = BASE_PRICE
cumulative_sells = 0
cumulative_buys = 0
last_trade_price = BASE_PRICE
current_level = 0

trade_records = []
total_realized_pnl = 0.0

print(f"\n{'='*80}")
print(f"{'时间':<20} {'价格':>7} {'涨跌':>7} {'档位':>5} {'动作':<5} {'股数':>6} {'持仓变化':>8} {'盈亏':>10} {'说明'}")
print(f"{'='*80}")

prev_price = BASE_PRICE

for idx, row in df.iterrows():
    dt = row['datetime']
    current_price = row['price']
    
    if not is_trading_time(dt):
        prev_price = current_price
        continue

    # 当前网格间距
    spacing, multiplier = get_spacing_with_multiplier(dt)
    if spacing <= 0:
        prev_price = current_price
        continue

    # 当前档位
    new_level = price_to_level(current_price, spacing)
    price_change = abs(current_price - last_trade_price)

    # 跳过：价格变动不足一格
    if price_change < spacing:
        prev_price = current_price
        continue

    # 尾盘强制平仓（14:30开始）
    if is_closing_window(dt):
        diff = current_position - yesterday_position
        if diff > 0:
            # 卖出
            pnl = (current_price - position_cost) * diff
            total_realized_pnl += pnl
            cumulative_sells += diff
            action_str = "卖出"
            pos_change = -diff
            print(f"{dt.strftime('%Y-%m-%d %H:%M:%S'):<20} {current_price:>7.4f} {(current_price-prev_price):>+7.4f} {new_level:>5} {action_str:<5} {diff:>6} {pos_change:>+8} {pnl:>+10.2f} 尾盘强制平仓")
            trade_records.append({
                'datetime': dt, 'price': current_price, 'change': current_price - prev_price,
                'level': new_level, 'action': 'SELL', 'shares': diff, 'pos_change': pos_change,
                'pnl': pnl, 'reason': '尾盘强制平仓'
            })
            current_position -= diff
            last_trade_price = current_price
        elif diff < 0:
            # 买入补回
            diff = -diff
            pnl = 0.0
            total_cost_before = position_cost * current_position
            current_position += diff
            position_cost = (total_cost_before + current_price * diff) / current_position
            cumulative_buys += diff
            action_str = "买入"
            pos_change = +diff
            print(f"{dt.strftime('%Y-%m-%d %H:%M:%S'):<20} {current_price:>7.4f} {(current_price-prev_price):>+7.4f} {new_level:>5} {action_str:<5} {diff:>6} {pos_change:>+8} {pnl:>+10.2f} 尾盘补回")
            trade_records.append({
                'datetime': dt, 'price': current_price, 'change': current_price - prev_price,
                'level': new_level, 'action': 'BUY', 'shares': diff, 'pos_change': pos_change,
                'pnl': pnl, 'reason': '尾盘补回'
            })
            last_trade_price = current_price
        prev_price = current_price
        continue

    # 网格边界熔断
    if new_level > MAX_LEVEL:
        prev_price = current_price
        continue
    if new_level < MIN_LEVEL:
        prev_price = current_price
        continue

    # 目标持仓
    target_position = base_position - new_level * SHARES_PER_GRID
    trade_shares = abs(current_position - target_position)

    if trade_shares == 0:
        prev_price = current_price
        current_level = new_level
        continue

    if current_position > target_position:
        # 需要卖出
        available = max(0, base_position - cumulative_sells)
        actual = min(trade_shares, available)
        if actual > 0:
            pnl = (current_price - position_cost) * actual
            total_realized_pnl += pnl
            cumulative_sells += actual
            pos_change = -actual
            print(f"{dt.strftime('%Y-%m-%d %H:%M:%S'):<20} {current_price:>7.4f} {(current_price-prev_price):>+7.4f} {new_level:>5} {'卖出':<5} {actual:>6} {pos_change:>+8} {pnl:>+10.2f} 网格卖出")
            trade_records.append({
                'datetime': dt, 'price': current_price, 'change': current_price - prev_price,
                'level': new_level, 'action': 'SELL', 'shares': actual, 'pos_change': pos_change,
                'pnl': pnl, 'reason': f'网格@{current_price}档={new_level}'
            })
            current_position -= actual
            last_trade_price = current_price
            current_level = new_level
    else:
        # 需要买入
        available = max(0, base_position - cumulative_buys)
        actual = min(trade_shares, available)
        if actual > 0:
            total_cost_before = position_cost * current_position
            current_position += actual
            position_cost = (total_cost_before + current_price * actual) / current_position
            cumulative_buys += actual
            pos_change = +actual
            print(f"{dt.strftime('%Y-%m-%d %H:%M:%S'):<20} {current_price:>7.4f} {(current_price-prev_price):>+7.4f} {new_level:>5} {'买入':<5} {actual:>6} {pos_change:>+8} {0.0:>+10.2f} 网格买入")
            trade_records.append({
                'datetime': dt, 'price': current_price, 'change': current_price - prev_price,
                'level': new_level, 'action': 'BUY', 'shares': actual, 'pos_change': pos_change,
                'pnl': 0.0, 'reason': f'网格@{current_price}档={new_level}'
            })
            last_trade_price = current_price
            current_level = new_level

    prev_price = current_price

# ========== 汇总 ==========
print(f"\n{'='*80}")
print("【回测汇总】")
print(f"{'='*80}")

total_trades = len(trade_records)
buy_trades = sum(1 for t in trade_records if t['action'] == 'BUY')
sell_trades = sum(1 for t in trade_records if t['action'] == 'SELL')
total_pnl = sum(t['pnl'] for t in trade_records)

print(f"总成交次数: {total_trades}")
print(f"买入次数:   {buy_trades}")
print(f"卖出次数:   {sell_trades}")
print(f"总盈亏:     {total_pnl:+.2f} 元")
print(f"最终持仓:   {current_position} 股")
print(f"持仓成本:   {position_cost:.4f}")
print(f"最终价格:   {df['price'].iloc[-1]:.4f}")
print(f"浮动盈亏:   {(df['price'].iloc[-1] - position_cost) * current_position:+.2f} 元")
print(f"累计实现盈亏: {total_realized_pnl:+.2f} 元")
print(f"{'='*80}")
