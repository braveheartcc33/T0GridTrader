#!/usr/bin/env python3
"""
多周期网格交易回测框架
支持 1min / 5min / 15min / 日线 多个时间框架
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional
import json
import logging
import glob

# 设置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("Backtest")

# ========== 配置 ==========
TUSHARE_TOKEN = "d11513bc2e258334d01ddf0db02d45793325443dc1260931691d1552"

# 网格参数配置（可测试不同参数组合）
GRID_CONFIGS = [
    {'grid_count': 10, 'atr_mult': 3.0, 'name': '10档-3倍ATR'},
    {'grid_count': 10, 'atr_mult': 4.0, 'name': '10档-4倍ATR'},
    {'grid_count': 10, 'atr_mult': 5.0, 'name': '10档-5倍ATR'},
    {'grid_count': 15, 'atr_mult': 4.0, 'name': '15档-4倍ATR'},
    {'grid_count': 20, 'atr_mult': 3.0, 'name': '20档-3倍ATR'},
]

# 回测时间范围
BACKTEST_START = "20260301"
BACKTEST_END = "20260331"

# 交易时间段
MORNING_START = (9, 30)
MORNING_END = (11, 30)
AFTERNOON_START = (13, 0)
AFTERNOON_END = (15, 0)

# 动态网格倍数规则
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


class GridBacktester:
    """
    单周期网格回测引擎
    """
    
    def __init__(self, stock_code: str, config: dict, base_shares: int = 100000):
        self.stock_code = stock_code
        self.config = config
        self.grid_count = config['grid_count']
        self.atr_mult = config['atr_mult']
        self.base_shares = base_shares
        
        # 网格边界
        self.max_level = self.grid_count // 2
        self.min_level = -self.max_level
        
        # 状态
        self.reset()
    
    def reset(self):
        """重置状态"""
        self.current_position = self.base_shares
        self.base_position = self.base_shares
        self.position_cost = 0.0
        self.cumulative_sells = 0
        self.cumulative_buys = 0
        self.last_trade_price = 0.0
        self.current_level = 0
        self.total_realized_pnl = 0.0
        self.trade_records = []
        self.base_price = 0.0
        self.atr = 0.0
        self.grid_spacing = 0.0
    
    def set_base_price(self, price: float, atr: float):
        """设置基准价和ATR"""
        self.base_price = price
        self.atr = atr
        self.grid_spacing = atr * self.atr_mult / self.grid_count
        self.position_cost = price
        self.last_trade_price = price
        logger.debug(f"基准价={price}, ATR={atr:.4f}, 间距={self.grid_spacing:.4f}")
    
    def get_spacing_with_multiplier(self, dt) -> tuple:
        """获取当前网格间距和倍数"""
        cur_min = dt.hour * 60 + dt.minute
        multiplier = 1.0
        for (hour, minute), rule_mult in sorted(GRID_SPACING_RULES, reverse=True):
            if cur_min >= hour * 60 + minute:
                multiplier = rule_mult
                break
        return self.grid_spacing * multiplier, multiplier
    
    def is_trading_time(self, dt) -> bool:
        """是否在交易时段"""
        cur_min = dt.hour * 60 + dt.minute
        mor_s = MORNING_START[0] * 60 + MORNING_START[1]
        mor_e = MORNING_END[0] * 60 + MORNING_END[1]
        aft_s = AFTERNOON_START[0] * 60 + AFTERNOON_START[1]
        aft_e = AFTERNOON_END[0] * 60 + AFTERNOON_END[1]
        return (mor_s <= cur_min <= mor_e) or (aft_s <= cur_min <= aft_e)
    
    def is_closing_window(self, dt) -> bool:
        """是否到尾盘（14:30起）"""
        cur_min = dt.hour * 60 + dt.minute
        aft_e = AFTERNOON_END[0] * 60 + AFTERNOON_END[1]
        return cur_min >= (aft_e - 30)
    
    def price_to_level(self, price: float, spacing: float) -> int:
        """价格转档位"""
        return int(round((price - self.base_price) / spacing))
    
    def process_bar(self, dt: datetime, price: float) -> Optional[dict]:
        """
        处理一根K线
        返回交易记录或None
        """
        if not self.is_trading_time(dt):
            return None
        
        spacing, multiplier = self.get_spacing_with_multiplier(dt)
        if spacing <= 0:
            return None
        
        new_level = self.price_to_level(price, spacing)
        price_change = abs(price - self.last_trade_price)
        
        # 价格变动不足一格，跳过
        if price_change < spacing:
            return None
        
        # 边界熔断
        if new_level > self.max_level:
            self.last_trade_price = price
            return None
        if new_level < self.min_level:
            self.last_trade_price = price
            return None
        
        # 尾盘强制平仓
        if self.is_closing_window(dt):
            return self._force_close(dt, price)
        
        # 计算目标持仓
        shares_per_grid = self.base_shares // self.grid_count
        target_position = self.base_position - new_level * shares_per_grid
        trade_shares = abs(self.current_position - target_position)
        
        if trade_shares == 0:
            self.last_trade_price = price
            self.current_level = new_level
            return None
        
        record = None
        
        if self.current_position > target_position:
            # 需要卖出
            available = max(0, self.base_position - self.cumulative_sells)
            actual = min(trade_shares, available)
            if actual > 0:
                pnl = (price - self.position_cost) * actual
                self.total_realized_pnl += pnl
                self.cumulative_sells += actual
                self.current_position -= actual
                record = {
                    'datetime': dt, 'price': price, 'level': new_level,
                    'action': 'SELL', 'shares': actual, 'pnl': pnl,
                    'reason': f'网格@{price}'
                }
        else:
            # 需要买入
            available = max(0, self.base_position - self.cumulative_buys)
            actual = min(trade_shares, available)
            if actual > 0:
                total_cost = self.position_cost * self.current_position
                self.current_position += actual
                self.position_cost = (total_cost + price * actual) / self.current_position
                self.cumulative_buys += actual
                record = {
                    'datetime': dt, 'price': price, 'level': new_level,
                    'action': 'BUY', 'shares': actual, 'pnl': 0,
                    'reason': f'网格@{price}'
                }
        
        if record:
            self.trade_records.append(record)
            self.last_trade_price = price
            self.current_level = new_level
        
        return record
    
    def _force_close(self, dt: datetime, price: float) -> Optional[dict]:
        """尾盘强制平仓"""
        diff = self.current_position - self.base_position
        if diff == 0:
            return None
        
        if diff > 0:
            # 卖出多余持仓
            pnl = (price - self.position_cost) * diff
            self.total_realized_pnl += pnl
            self.cumulative_sells += diff
            self.current_position -= diff
            record = {
                'datetime': dt, 'price': price, 'level': 0,
                'action': 'SELL', 'shares': diff, 'pnl': pnl,
                'reason': '尾盘强制平仓'
            }
        else:
            # 买入补足
            diff = -diff
            total_cost = self.position_cost * self.current_position
            self.current_position += diff
            self.position_cost = (total_cost + price * diff) / self.current_position
            self.cumulative_buys += diff
            record = {
                'datetime': dt, 'price': price, 'level': 0,
                'action': 'BUY', 'shares': diff, 'pnl': 0,
                'reason': '尾盘补回'
            }
        
        self.trade_records.append(record)
        return record
    
    def get_stats(self, end_price: float) -> dict:
        """获取回测统计"""
        floating_pnl = (end_price - self.position_cost) * self.current_position
        total_pnl = self.total_realized_pnl + floating_pnl
        
        buy_trades = sum(1 for r in self.trade_records if r['action'] == 'BUY')
        sell_trades = sum(1 for r in self.trade_records if r['action'] == 'SELL')
        
        winning_trades = sum(1 for r in self.trade_records if r.get('pnl', 0) > 0)
        win_rate = winning_trades / sell_trades * 100 if sell_trades > 0 else 0
        
        return {
            'total_trades': len(self.trade_records),
            'buy_trades': buy_trades,
            'sell_trades': sell_trades,
            'total_realized_pnl': self.total_realized_pnl,
            'floating_pnl': floating_pnl,
            'total_pnl': total_pnl,
            'final_position': self.current_position,
            'final_cost': self.position_cost,
            'final_price': end_price,
            'win_rate': win_rate,
        }


class MultiTimeframeBacktester:
    """
    多周期回测管理器
    """
    
    def __init__(self, stock_code: str, data_dir: str = None):
        self.stock_code = stock_code
        self.data_dir = data_dir or os.path.dirname(os.path.abspath(__file__))
        
        # 尝试导入tushare
        self.pro = None
        try:
            import tushare as ts
            self.ts = ts
            self.pro = ts.pro_api(TUSHARE_TOKEN)
        except Exception as e:
            logger.warning(f"tushare导入失败: {e}")
            self.ts = None
    
    def load_daily_data(self, start_date: str, end_date: str) -> pd.DataFrame:
        """从tushare加载日线数据"""
        if self.pro is None:
            logger.warning("无tushare，无法加载日线数据")
            return pd.DataFrame()
        
        try:
            df = self.pro.daily(ts_code=self.stock_code, start_date=start_date, end_date=end_date)
            df = df.sort_values('trade_date').reset_index(drop=True)
            df['datetime'] = pd.to_datetime(df['trade_date'])
            df['price'] = df['close'].astype(float)
            return df
        except Exception as e:
            logger.error(f"加载日线失败: {e}")
            return pd.DataFrame()
    
    def load_snapshot_data(self, start_date: str, end_date: str) -> pd.DataFrame:
        """
        从本地快照CSV加载数据（模拟分钟数据）
        positions_YYYYMMDD.csv 格式
        """
        all_data = []
        pattern = os.path.join(self.data_dir, "positions_*.csv")
        
        for csv_file in sorted(glob.glob(pattern)):
            filename = os.path.basename(csv_file)
            # 提取日期
            date_str = filename.replace("positions_", "").replace(".csv", "")
            
            if start_date <= date_str <= end_date:
                try:
                    df = pd.read_csv(csv_file)
                    # 过滤股票代码
                    if 'stock_code' in df.columns:
                        df = df[df['stock_code'] == self.stock_code]
                    
                    if not df.empty:
                        df['datetime'] = pd.to_datetime(df['snapshot_time'])
                        df = df.sort_values('datetime').reset_index(drop=True)
                        all_data.append(df)
                        logger.debug(f"加载 {csv_file}: {len(df)} 条")
                except Exception as e:
                    logger.warning(f"加载 {csv_file} 失败: {e}")
        
        if all_data:
            combined = pd.concat(all_data, ignore_index=True)
            combined = combined.sort_values('datetime').reset_index(drop=True)
            logger.info(f"共加载快照数据 {len(combined)} 条")
            return combined
        
        return pd.DataFrame()
    
    def simulate_intraday_from_snapshots(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        从快照数据模拟日内交易
        使用09:35/09:45/09:55三个时间点
        """
        if df.empty:
            return df
        
        # 添加时间列
        df['time'] = df['datetime'].dt.time
        
        # 筛选三个关键时间点
        from datetime import time as dt_time
        times_to_keep = [
            dt_time(9, 35),
            dt_time(9, 45),
            dt_time(9, 55),
        ]
        
        # 尝试按时间筛选，如果没有匹配的，就用全部数据
        intraday = df[df['time'].isin(times_to_keep)].copy()
        
        if intraday.empty:
            # 如果没有精确匹配的，用每个小时的数据
            logger.info("使用每小时数据模拟日内交易")
            # 保留每小时第一个快照
            df['_hour_key'] = df['datetime'].dt.strftime('%Y-%m-%d %H')
            intraday = df.groupby('_hour_key').first().reset_index()
            intraday = intraday.drop(columns=['_hour_key'], errors='ignore')
        
        return intraday
    
    def run_single_backtest(self, df: pd.DataFrame, config: dict, base_shares: int) -> dict:
        """运行单次回测"""
        if df.empty:
            return {'error': 'no data'}
        
        # 确保有价格列
        if 'price' not in df.columns:
            for col in ['close', 'price', 'open', 'current_price']:
                if col in df.columns:
                    df['price'] = df[col].astype(float)
                    break
        
        if 'price' not in df.columns or 'datetime' not in df.columns:
            return {'error': 'invalid data format'}
        
        df = df.dropna(subset=['price', 'datetime']).sort_values('datetime').reset_index(drop=True)
        
        if len(df) < 2:
            return {'error': 'insufficient data'}
        
        # 初始化回测引擎
        tester = GridBacktester(self.stock_code, config, base_shares)
        
        # 使用第一行的ATR作为初始ATR
        if 'atr14' in df.columns:
            atr = df['atr14'].iloc[0]
        else:
            # 简单计算
            prices = df['price'].values
            if len(prices) >= 2:
                atr = np.mean(np.abs(np.diff(prices)))
            else:
                atr = prices[0] * 0.01
        
        # 设置基准价
        first_price = df['price'].iloc[0]
        tester.set_base_price(first_price, atr)
        
        # 逐根K线回测
        for idx, row in df.iterrows():
            tester.process_bar(row['datetime'], row['price'])
        
        # 获取统计
        end_price = df['price'].iloc[-1]
        stats = tester.get_stats(end_price)
        stats['config'] = config['name']
        
        return stats
    
    def run(self, start_date: str, end_date: str, 
            timeframes: List[str] = None,
            stock_configs: List[dict] = None) -> Dict:
        """
        运行多周期多参数回测
        
        Args:
            start_date: 开始日期 YYYYMMDD
            end_date: 结束日期 YYYYMMDD
            timeframes: 时间周期列表 ['daily', 'snapshot']
            stock_configs: 股票配置列表
        
        Returns:
            回测结果字典
        """
        if timeframes is None:
            timeframes = ['snapshot']
        
        if stock_configs is None:
            stock_configs = [{'code': self.stock_code, 'base_shares': 100000}]
        
        results = {
            'period': f"{start_date}-{end_date}",
            'stock_code': self.stock_code,
            'timeframes': {},
        }
        
        # 对每个时间周期
        for tf in timeframes:
            logger.info(f"[回测] 周期: {tf}")
            tf_results = []
            
            # 加载数据
            if tf == 'daily':
                df = self.load_daily_data(start_date, end_date)
            elif tf == 'snapshot':
                # 从快照数据模拟日内交易
                df = self.load_snapshot_data(start_date, end_date)
                if not df.empty:
                    # 提取价格和关键时间点
                    df = self.simulate_intraday_from_snapshots(df)
            else:
                logger.warning(f"不支持的周期: {tf}")
                continue
            
            if df.empty:
                logger.warning(f"[回测] {tf} 数据为空")
                continue
            
            logger.info(f"[回测] {tf} 数据量: {len(df)} 条")
            
            # 对每个参数配置
            for config in GRID_CONFIGS:
                logger.info(f"[回测] {tf} - {config['name']}")
                base_shares = stock_configs[0].get('base_shares', 100000)
                stats = self.run_single_backtest(df, config, base_shares)
                if 'error' not in stats:
                    tf_results.append(stats)
            
            if tf_results:
                results['timeframes'][tf] = tf_results
        
        return results
    
    def generate_report(self, results: Dict) -> str:
        """生成回测报告"""
        report = []
        report.append("=" * 80)
        report.append(f"# 网格交易回测报告")
        report.append("")
        report.append(f"- **股票**: {results.get('stock_code', 'N/A')}")
        report.append(f"- **回测期间**: {results.get('period', 'N/A')}")
        report.append(f"- **生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("")
        report.append("=" * 80)
        report.append("")
        
        # 找出最优参数
        best_config = None
        best_pnl = float('-inf')
        
        for tf, tf_results in results.get('timeframes', {}).items():
            report.append(f"## {tf}周期")
            report.append("")
            report.append("| 参数 | 交易次数 | 买入 | 卖出 | 实现盈亏 | 浮动盈亏 | 总盈亏 | 胜率 |")
            report.append("|------|----------|------|------|----------|----------|--------|------|")
            
            for r in tf_results:
                config_name = r.get('config', 'N/A')
                total_trades = r.get('total_trades', 0)
                buy_trades = r.get('buy_trades', 0)
                sell_trades = r.get('sell_trades', 0)
                realized = r.get('total_realized_pnl', 0)
                floating = r.get('floating_pnl', 0)
                total = r.get('total_pnl', 0)
                win_rate = r.get('win_rate', 0)
                
                report.append(f"| {config_name} | {total_trades} | {buy_trades} | {sell_trades} "
                             f"| {realized:+.2f} | {floating:+.2f} | {total:+.2f} | {win_rate:.1f}% |")
                
                if total > best_pnl:
                    best_pnl = total
                    best_config = f"{tf} - {config_name}"
            
            report.append("")
        
        report.append("=" * 80)
        report.append("")
        if best_config:
            report.append(f"**最优参数**: {best_config}, **总盈亏**: {best_pnl:+.2f}")
        else:
            report.append("**无有效回测结果**")
        report.append("")
        report.append("=" * 80)
        
        return "\n".join(report)


def run_backtest_demo():
    """运行演示回测"""
    logger.info("=" * 60)
    logger.info("  网格交易多周期回测")
    logger.info("=" * 60)
    
    # 使用159567.SZ
    stock_code = "159567.SZ"
    
    # 创建回测器
    backtester = MultiTimeframeBacktester(stock_code)
    
    # 运行回测（使用快照数据模拟日内交易）
    results = backtester.run(
        start_date="20260324",
        end_date="20260331",
        timeframes=['snapshot'],  # 使用快照数据
        stock_configs=[{'code': stock_code, 'base_shares': 100000}]
    )
    
    # 生成报告
    report = backtester.generate_report(results)
    print("\n" + report)
    
    # 保存报告
    report_file = f"backtest_report_{datetime.now().strftime('%Y%m%d')}.md"
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report)
    logger.info(f"报告已保存到: {report_file}")
    
    return results


if __name__ == "__main__":
    results = run_backtest_demo()
