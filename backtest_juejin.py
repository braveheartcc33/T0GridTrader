#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
掘金量化 T0 网格回测框架
- 动态波动率网格
- 资金流T+0计算：T+0 = 卖出总额 - 买入总额 - 手续费
- 上下5格仓位限制
- 14:30尾盘强制平仓
"""

import pandas as pd
import numpy as np
import argparse, sys, os

# ========== 交易时间工具 ==========

TRADE_START  = "09:30"
TRADE_END    = "14:57"
LUNCH_START  = "11:30"
LUNCH_END    = "12:00"

def in_trading_hours(dt):
    t = dt.strftime("%H:%M")
    if TRADE_START <= t < LUNCH_START:
        return True
    if LUNCH_END <= t < TRADE_END:
        return True
    return False

def in_close_window(dt):
    return dt.strftime("%H:%M") >= "14:30"


# ========== 网格交易引擎 ==========

class GridSim:
    """
    网格交易模拟器

    规则：
    - 底仓 base_shares 股，每日按基准价开盘
    - 每格间距 = 基准价 × 波动率 / grid_count
    - 每格股数 = base_shares // grid_count
    - 向上最多 grid_count//2 格（卖），向下最多 grid_count//2 格（买）
    - T+0收益 = 当日卖出总额 - 当日买入总额 - 手续费
    """

    def __init__(self, grid_count=10, default_vol_pct=0.06,
                 base_shares=100000, fee_rate=0.0003):
        self.grid_count   = grid_count
        self.default_vol_pct = default_vol_pct
        self.base_shares  = base_shares
        self.fee_rate     = fee_rate

        # 持仓状态
        self.pos     = 0     # 当前持仓
        self.cost    = 0.0   # 持仓成本
        self.base_price = 0.0
        self.spacing = 0.0   # 每格间距
        self.day     = None

        # 当日买卖统计（用于T+0资金流计算）
        self.day_buy_vol  = 0
        self.day_sell_vol = 0
        self.day_buy_amt = 0.0
        self.day_sell_amt = 0.0
        self.day_total_fee = 0.0   # 当日手续费合计
        self.trades       = []
        self.day_realized = 0.0   # 已实现盈亏（持仓成本法，仅供参考）

        # 价格状态
        self.prev_price     = 0.0
        self.last_exec_price = None   # 上次成交价（用于判断是否够一格）
        self.active_vol_pct = self.default_vol_pct

    def reset(self):
        self.pos     = 0
        self.cost    = 0.0
        self.day     = None

    def set_vol(self, vol_pct):
        self.active_vol_pct = vol_pct

    def init_day(self, date, base_price, vol_pct):
        """每日初始化"""
        self.day           = date
        self.base_price    = base_price
        self.active_vol_pct = vol_pct if vol_pct > 0 else self.default_vol_pct
        self.spacing       = base_price * self.active_vol_pct / self.grid_count
        self.day_buy_vol   = 0
        self.day_sell_vol  = 0
        self.day_buy_amt   = 0.0
        self.day_sell_amt  = 0.0
        self.day_total_fee = 0.0
        self.trades        = []
        self.day_realized  = 0.0
        if self.pos == 0:
            self.pos  = self.base_shares
            self.cost = base_price
        self.prev_price     = base_price
        self.last_exec_price = None

    # ---- 仓位规则：上下各5格（各5万股）----
    def _can_buy(self, vol):
        return self.day_buy_vol + vol <= self.base_shares // 2

    def _can_sell(self, vol):
        return self.day_sell_vol + vol <= self.base_shares // 2

    def _execute(self, dt, price, vol, reason):
        if vol == 0 or self.spacing <= 0:
            return
        vol     = int(vol)
        action  = "买入" if vol > 0 else "卖出"
        abs_vol = abs(vol)
        fee     = abs_vol * price * self.fee_rate

        if vol > 0:
            if not self._can_buy(abs_vol):
                return
            # 更新持仓成本
            cost  = self.cost * self.pos + price * abs_vol
            self.pos += abs_vol
            self.cost = cost / self.pos
            self.day_buy_vol += abs_vol
            self.day_buy_amt  += abs_vol * price
            self.trades.append({
                'dt': dt, 'price': price, 'vol': abs_vol,
                'action': action, 'reason': reason,
                'fee': fee, 'pos_after': self.pos
            })
            self.last_exec_price = price
            self.day_total_fee  += fee
        else:
            if not self._can_sell(abs_vol):
                return
            realized = (price - self.cost) * abs_vol - fee
            self.day_realized += realized
            self.pos           -= abs_vol
            self.day_sell_vol += abs_vol
            self.day_sell_amt  += abs_vol * price
            self.trades.append({
                'dt': dt, 'price': price, 'vol': abs_vol,
                'action': action, 'reason': reason,
                'realized': realized, 'fee': fee, 'pos_after': self.pos
            })
            self.last_exec_price = price
            self.day_total_fee   += fee

    def on_bar(self, dt, price):
        if not in_trading_hours(dt):
            return
        if self.day != dt.date():
            return  # 换日由外层处理

        # 尾盘强制平仓
        if in_close_window(dt):
            if self.pos != self.base_shares:
                self._execute(dt, price, self.base_shares - self.pos, "尾盘平仓")
            return

        # prev_price 每根K线都更新（不管有没有交易）
        prev_price = self.prev_price
        self.prev_price = price

        if self.spacing <= 0:
            return

        lv     = int((price - self.base_price) / self.spacing)
        prev_lv = int((prev_price - self.base_price) / self.spacing)
        if lv == prev_lv:
            return

        # 核心过滤：价格必须距上次成交价至少一整格间距
        if self.last_exec_price is not None and abs(price - self.last_exec_price) < self.spacing:
            return

        # 每格股数
        spg = self.base_shares // self.grid_count
        # 限制最多上下5格（各5万股）
        max_grid = self.grid_count // 2
        lv = max(-max_grid, min(max_grid, lv))
        target_pos = self.base_shares - lv * spg
        diff = target_pos - self.pos
        self._execute(dt, price, diff, f"网格({prev_lv}→{lv})")

    def snapshot(self, close_price):
        """生成日结算快照"""
        hold_pnl = (close_price - self.base_price) * self.base_shares
        # T+0用资金流算法：卖出 - 买入 - 手续费
        t0_pnl = self.day_sell_amt - self.day_buy_amt - self.day_total_fee
        return {
            'date':         self.day,
            'close':        close_price,
            'base_price':   self.base_price,
            'base_pos':     self.base_shares,
            'final_pos':    self.pos,
            'buy_vol':      self.day_buy_vol,
            'sell_vol':     self.day_sell_vol,
            'trade_count':  len(self.trades),
            't0_realized': t0_pnl,        # 资金流T+0
            'hold_pnl':     hold_pnl,      # 持仓不动收益
            'total_pnl':    t0_pnl,        # 策略合计 = T+0（持仓盈亏单独列示）
            'active_vol_pct': self.active_vol_pct,
            'hv_display':   f"{self.active_vol_pct*100:.2f}%",
            'spacing':      self.spacing,
            'trades':       self.trades,
        }


def build_daily_vol(df_1m, window=3, default_vol=0.06):
    """
    从1m数据构建日线，并计算滚动已实现波动率
    vol[i] = 前window日日对数收益率标准差 × √252（年化）
    首日无数据时用 default_vol
    """
    daily = (df_1m.groupby('date')
                  .agg(close=('close', 'last'))
                  .reset_index()
                  .sort_values('date'))
    daily['log_ret'] = np.log(daily['close'] / daily['close'].shift(1))
    daily['hv'] = daily['log_ret'].rolling(window).std() * np.sqrt(252)
    daily['hv'] = daily['hv'].fillna(default_vol)
    return daily[['date', 'close', 'hv']]


def run_backtest(symbol, csv_path, grid_count=10, default_vol_pct=0.06,
                 base_shares=100000, out_csv=None):
    """回测主流程"""
    print(f"\n{'='*72}")
    print(f"  {symbol}  T0网格回测（动态波动率）")
    print(f"  档位={grid_count}  默认波动率={default_vol_pct*100:.1f}%  底仓={base_shares}")
    print(f"{'='*72}")

    # ---- 加载1m数据 ----
    df = pd.read_csv(csv_path)
    df['dt']   = pd.to_datetime(df['time'])
    df['date'] = df['dt'].dt.date
    print(f"数据: {len(df)}条1m  {df['date'].min()} ~ {df['date'].max()}")

    # ---- 构建日线+波动率 ----
    daily = build_daily_vol(df, window=3, default_vol=default_vol_pct)
    dates  = sorted(daily['date'].unique())
    print(f"交易日: {len(dates)}天  平均波动率: {daily['hv'].mean()*100:.2f}%")

    # ---- 预计算每日前收盘 ----
    prev_close = {}
    closes = daily.set_index('date')['close'].to_dict()
    for i, d in enumerate(dates):
        if i == 0:
            prev_close[d] = closes[d]   # 首日用当日收盘作基准
        else:
            prev_close[d] = closes[dates[i-1]]

    # ---- 表头 ----
    print(f"\n{'日期':<12} {'收盘':>7} {'波动率':>8} {'每格间距':>10} "
          f"{'T+0收益':>12} {'持仓收益':>10} {'策略合计':>10} {'笔数':>6}")
    print("-" * 80)

    records  = []
    sim      = GridSim(grid_count=grid_count,
                       default_vol_pct=default_vol_pct,
                       base_shares=base_shares)
    sim.reset()

    for i, day in enumerate(dates):
        day_df  = df[df['date'] == day].sort_values('dt').reset_index(drop=True)
        if len(day_df) == 0:
            continue

        bp       = prev_close[day]
        hv       = daily[daily['date'] == day].iloc[0]['hv']
        sim.init_day(day, bp, hv)

        for _, row in day_df.iterrows():
            sim.on_bar(row['dt'], float(row['close']))

        # 尾盘未平仓则强制平
        last_row = day_df.iloc[-1]
        if sim.pos != sim.base_shares:
            sim._execute(last_row['dt'], float(last_row['close']),
                         sim.base_shares - sim.pos, "尾盘平仓")

        snap = sim.snapshot(float(last_row['close']))
        records.append(snap)

        flag = "📈" if snap['t0_realized'] > 0 else "📉" if snap['t0_realized'] < 0 else "➡️"
        print(f"  {str(day):<12} {snap['close']:>7.3f} {snap['hv_display']:>8} "
              f"{snap['spacing']:>10.4f} {snap['t0_realized']:>12,.2f} "
              f"{snap['hold_pnl']:>10,.2f} {snap['total_pnl']:>10,.2f} "
              f"{snap['trade_count']}笔 {flag}")

    # ---- 汇总 ----
    total_t0     = sum(r['t0_realized'] for r in records)
    total_hold   = sum(r['hold_pnl']    for r in records)
    total_trades = sum(r['trade_count'] for r in records)
    win_days     = sum(1 for r in records if r['t0_realized'] > 0)
    all_days     = len(records)

    # 手续费估算（用累计买卖量）
    total_buy  = sum(r['buy_vol']  for r in records)
    total_sell = sum(r['sell_vol'] for r in records)
    avg_price  = sum(r['close'] * (r['buy_vol'] + r['sell_vol']) for r in records) / max(1, total_buy + total_sell)
    est_fee     = (total_buy + total_sell) * avg_price * sim.fee_rate

    print(f"\n{'='*72}")
    print(f"  回测汇总")
    print(f"{'='*72}")
    print(f"  档位={grid_count}  默认波动率={default_vol_pct*100:.1f}%  底仓={base_shares}")
    print(f"  交易日: {all_days}天  平均已实现波动率: {daily['hv'].mean()*100:.2f}%")
    print(f"  总交易笔数: {total_trades}笔  日均: {total_trades/all_days:.1f}笔")
    print(f"  ★ T+0已实现收益: {total_t0:>12,.2f}")
    print(f"  ★ 持仓不动收益(机会): {total_hold:>10,.2f}")
    print(f"  ★ 策略合计盈亏: {total_t0:>12,.2f}")
    print(f"  盈亏天数: {win_days}/{all_days} ({win_days/all_days*100:.1f}%)")
    print(f"  估算手续费(双向): {est_fee:>10,.2f}")
    print(f"  净盈亏(扣手续费): {total_t0 - est_fee:>12,.2f}")

    if out_csv:
        out = pd.DataFrame(records)
        col_order = ['date','close','base_price','hv_display','spacing',
                     'base_pos','final_pos','buy_vol','sell_vol',
                     'trade_count','t0_realized','hold_pnl','total_pnl']
        out[col_order].to_csv(out_csv, index=False)
        print(f"\n日统计已保存: {out_csv}")

    return records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="掘金量化 T0 网格回测")
    parser.add_argument("--symbol",      default="SHSE.513720")
    parser.add_argument("--csv",         default="/home/administrator/.openclaw/workspace/juejin_data/fund/1m/513720_1m_2026.csv")
    parser.add_argument("--grid-count",  type=int, default=10)
    parser.add_argument("--default-vol",  type=float, default=0.06)
    parser.add_argument("--base-shares", type=int, default=100000)
    parser.add_argument("--out-csv")
    args = parser.parse_args()

    out = args.out_csv or f"/home/administrator/.openclaw/workspace/juejin_data/backtest_{args.symbol.split('.')[-1]}_dynvol_gc{args.grid_count}.csv"
    run_backtest(args.symbol, args.csv,
                 grid_count=args.grid_count,
                 default_vol_pct=args.default_vol,
                 base_shares=args.base_shares,
                 out_csv=out)
