"""
Multi-Engine 多股票网格交易管理器
支持多只股票同时运行独立的网格策略
"""
import logging
import os
import time
import signal
from datetime import datetime, date, timedelta
from threading import Thread, Event
import json

# 设置工作目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("multi_grid_trader.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger("MultiGridTrader")

from config import (
    STOCKS, MULTI_STOCK_MODE, STATE_FILE_PREFIX,
    TRADING_MORNING_START, TRADING_MORNING_END,
    TRADING_AFTERNOON_START, TRADING_AFTERNOON_END,
)


class GridTraderUnit:
    """
    单只股票的网格交易单元
    包含独立的引擎、通知器、日志记录器
    """
    
    def __init__(self, config: dict):
        self.config = config
        self.code = config['code']
        self.name = config['name']
        
        # 导入依赖模块
        from market_data import MarketDataManager
        from grid_engine import GridEngine
        from notifier import GridNotifier
        from trade_logger import TradeLogger
        
        # 独立组件
        self.market_mgr = MarketDataManager(self.code)
        self.notifier = GridNotifier(stock_code=self.code, stock_name=self.name)
        self.trade_logger = TradeLogger(BASE_DIR, stock_code=self.code)
        self.engine = None
        
        # 状态文件
        self.state_file = os.path.join(BASE_DIR, f"{STATE_FILE_PREFIX}{self.code}.json")
        
        # 交易统计
        self._cumulative_net_pnl = 0.0
        self._today_sell_amount = 0.0
        self._today_buy_amount = 0.0
        self._today_sell_shares = 0
        self._today_buy_shares = 0
        
        # 交易时间段（分钟）
        self.mor_start = TRADING_MORNING_START[0] * 60 + TRADING_MORNING_START[1]
        self.mor_end = TRADING_MORNING_END[0] * 60 + TRADING_MORNING_END[1]
        self.aft_start = TRADING_AFTERNOON_START[0] * 60 + TRADING_AFTERNOON_START[1]
        self.aft_end = TRADING_AFTERNOON_END[0] * 60 + TRADING_AFTERNOON_END[1]
        
        logger.info(f"[{self.code}] 网格交易单元初始化完成")
    
    def is_trading_time(self, dt: datetime = None) -> bool:
        """判断是否在交易时段"""
        if dt is None:
            dt = datetime.now()
        cur_min = dt.hour * 60 + dt.minute
        return (self.mor_start <= cur_min <= self.mor_end) or (self.aft_start <= cur_min <= self.aft_end)
    
    def is_closing_time(self, dt: datetime = None) -> bool:
        """尾盘30分钟"""
        if dt is None:
            dt = datetime.now()
        cur_min = dt.hour * 60 + dt.minute
        return cur_min >= (self.aft_end - 30)
    
    def minutes_to_close(self, dt: datetime = None) -> int:
        """距离收盘还有多少分钟"""
        if dt is None:
            dt = datetime.now()
        cur_min = dt.hour * 60 + dt.minute
        if self.aft_start <= cur_min <= self.aft_end:
            return self.aft_end - cur_min
        return -1
    
    def initialize(self):
        """初始化市场数据和网格引擎"""
        logger.info(f"[{self.code}] 开始初始化...")
        
        indicators = self.market_mgr.initialize()
        base_price = indicators['last_close']
        
        self.engine = self.GridEngineCls = GridEngine(
            base_price=base_price,
            atr14=indicators['atr14'],
            boll_upper=indicators['boll_upper'],
            boll_lower=indicators['boll_lower'],
            boll_middle=indicators['boll_middle'],
            yesterday_close_position=indicators.get('last_close_position', self.config['base_shares']),
            grid_count=self.config['grid_count'],
            shares_per_grid=self.config['shares_per_grid'],
            base_shares=self.config['base_shares'],
            stop_loss_enabled=self.config['stop_loss_enabled'],
        )
        
        # 发送初始化报告
        spacing = indicators['atr14'] / self.engine.grid_count
        self.notifier.send_init_report(indicators, base_price,
                                        self.engine.grid_count, spacing)
        
        self._save_state()
        logger.info(f"[{self.code}] 初始化完成，基准价={base_price}")
        return True
    
    def _calc_today_pnl(self, current_price: float) -> tuple:
        """计算今日盈亏三因子"""
        net_shares = self._today_sell_shares - self._today_buy_shares
        t0 = self._today_sell_amount - self._today_buy_amount - net_shares * current_price
        base_price = self.engine.base_price
        yesterday_pos = self.engine.yesterday_position
        position_pnl = (current_price - base_price) * yesterday_pos
        return t0, position_pnl, t0 + position_pnl
    
    def _log_trade(self, record, avg_cost=None):
        """记录成交"""
        cost = record.pre_trade_cost if record.pre_trade_cost > 0 else (avg_cost if avg_cost is not None else self.engine.position_cost)
        
        if record.action == "SELL":
            self._today_sell_amount += record.price * record.shares
            self._today_sell_shares += record.shares
            gross = record.price * record.shares
            commission = max(gross * 0.0003, 5.0)
            stamp_tax = gross * 0.001
            trade_net_pnl = (record.price - cost) * record.shares - commission - stamp_tax
            self._cumulative_net_pnl += trade_net_pnl
        else:
            self._today_buy_amount += record.price * record.shares
            self._today_buy_shares += record.shares
        
        entry = self.trade_logger.log_trade(
            stock_code=self.code,
            stock_name=self.name,
            action=record.action,
            price=record.price,
            shares=record.shares,
            grid_level=record.grid_level,
            reason=record.reason,
            cumulative_pnl=self._cumulative_net_pnl,
            avg_cost=cost,
        )
        
        t0_profit, position_pnl, total_pnl = self._calc_today_pnl(record.price)
        logger.info(f"[{self.code}] 成交 #{entry.trade_id} {record.action} {record.shares}股@{record.price:.4f}")
    
    def _save_state(self):
        """保存状态"""
        if self.engine is None:
            return
            
        status = self.engine.get_status()
        engine_state = {
            'base_price': status['base_price'],
            'current_position': status['current_position'],
            'position_cost': status['position_cost'],
            'base_position': status['base_position'],
            'cumulative_sells': status['cumulative_sells'],
            'current_level': status['current_level'],
            'today_realized_pnl': status['today_realized_pnl'],
            'total_pnl': status['total_pnl'],
            'stop_loss_triggered': status['stop_loss_triggered'],
            'trade_records': self.engine.get_trade_records(),
            'today_sell_amount': self._today_sell_amount,
            'today_buy_amount': self._today_buy_amount,
            'cumulative_net_pnl': self._cumulative_net_pnl,
        }
        market_state = {
            'atr14': status['atr14'],
            'boll_upper': status['boll_upper'],
            'boll_middle': status['boll_middle'],
            'boll_lower': status['boll_lower'],
            'last_price': status['current_price'],
        }
        self.trade_logger.save_state(engine_state, market_state)
    
    def _log_position_snapshot(self):
        """记录持仓快照"""
        if self.engine is None:
            return
        status = self.engine.get_status()
        indicators = self.market_mgr.indicators
        current_price = status['current_price']
        t0, position_pnl, total = self._calc_today_pnl(current_price)
        
        self.trade_logger.log_position_snapshot(
            stock_code=self.code,
            stock_name=self.name,
            current_price=current_price,
            position_shares=status['current_position'],
            position_cost=status['position_cost'],
            base_position=status['base_position'],
            realized_pnl=status['today_realized_pnl'],
            atr14=status['atr14'],
            boll_upper=status['boll_upper'],
            boll_middle=status['boll_middle'],
            boll_lower=status['boll_lower'],
            grid_spacing=status['current_spacing'],
            grid_level=status['current_level'],
            base_price=status['base_price'],
            today_t0=t0,
            today_position_pnl=position_pnl,
            today_total_pnl=total,
        )
    
    def process_tick(self, now: datetime) -> dict:
        """
        处理一次行情tick
        返回: {'action': 'trade'|'status'|'none', 'details': {...}}
        """
        if not self.is_trading_time(now):
            return {'action': 'none'}
        
        price = self.market_mgr.get_realtime_price()
        if price is None:
            return {'action': 'none'}
        
        result = {'action': 'none', 'price': price}
        
        # 尾盘强制平仓
        if self.is_closing_time(now):
            logger.info(f"[{self.code}] 进入尾盘，强制平仓...")
            records = self.engine.force_close_all_t0(price, now)
            for rec in records:
                self._log_trade(rec)
            self._save_state()
            self._log_position_snapshot()
            result['action'] = 'force_close'
            return result
        
        # 更新动态网格间距
        current_spacing = self.market_mgr.get_grid_spacing(now)
        if abs(current_spacing - self.engine.last_grid_spacing) > 0.0001:
            self.engine.update_grid_spacing(current_spacing)
        
        # 检查网格触发
        trade_result = self.engine.check_and_trade(price, now)
        
        if trade_result:
            record, avg_cost = trade_result
            self._log_trade(record, avg_cost)
            self._save_state()
            self._log_position_snapshot()
            result['action'] = 'trade'
            result['details'] = {
                'record': record,
                'current_position': self.engine.current_position,
                'base_position': self.engine.base_position,
            }
        
        return result
    
    def get_status_summary(self) -> dict:
        """获取状态摘要"""
        if self.engine is None:
            return {'code': self.code, 'status': 'not_initialized'}
        
        status = self.engine.get_status()
        current_price = status.get('current_price', 0)
        t0, position_pnl, total = self._calc_today_pnl(current_price)
        
        return {
            'code': self.code,
            'name': self.name,
            'price': current_price,
            'position': status['current_position'],
            'base_position': status['base_position'],
            't0': t0,
            'position_pnl': position_pnl,
            'total_pnl': total,
            'grid_level': status['current_level'],
            'trades_today': len(self.engine.get_trade_records()),
        }


class GridTraderMulti:
    """
    多股票网格交易管理器
    为每只股票启动独立的交易单元
    """
    
    def __init__(self, stocks_config: list = None):
        self.configs = stocks_config or STOCKS
        self.traders = {}
        self.threads = {}
        self.shutdown_event = Event()
        self.running = False
        
        # 初始化每个股票的交易单元
        for cfg in self.configs:
            code = cfg['code']
            self.traders[code] = GridTraderUnit(cfg)
            logger.info(f"[Multi] 已注册股票: {code} {cfg['name']}")
        
        logger.info(f"[Multi] 共初始化 {len(self.traders)} 只股票的交易单元")
    
    def initialize_all(self):
        """初始化所有股票的交易引擎"""
        for code, trader in self.traders.items():
            logger.info(f"[Multi] 初始化 {code}...")
            trader.initialize()
    
    def start_all(self):
        """启动所有交易单元（独立线程）"""
        self.running = True
        
        for code, trader in self.traders.items():
            thread = Thread(target=self._trader_loop, args=(code,), daemon=True)
            thread.start()
            self.threads[code] = thread
            logger.info(f"[Multi] 启动 {code} 交易线程")
    
    def _trader_loop(self, code: str):
        """单个股票的交易循环"""
        trader = self.traders[code]
        last_status_time = datetime.now()
        
        while not self.shutdown_event.is_set():
            now = datetime.now()
            
            # 每tick处理
            result = trader.process_tick(now)
            
            # 每30分钟状态汇报
            elapsed = (now - last_status_time).total_seconds()
            if elapsed >= 1800:  # 30分钟
                summary = trader.get_status_summary()
                logger.info(f"[{code}] 状态: 价格={summary.get('price', 0):.3f} "
                           f"持仓={summary.get('position', 0)} T0={summary.get('t0', 0):.2f}")
                last_status_time = now
            
            # 休息一下
            from config import POLL_INTERVAL_SEC
            time.sleep(POLL_INTERVAL_SEC)
        
        logger.info(f"[Multi] {code} 交易线程退出")
    
    def stop_all(self):
        """停止所有交易"""
        logger.info("[Multi] 正在停止所有交易单元...")
        self.shutdown_event.set()
        
        # 等待线程结束
        for code, thread in self.threads.items():
            thread.join(timeout=5)
            logger.info(f"[Multi] {code} 线程已停止")
        
        self.running = False
        logger.info("[Multi] 所有交易单元已停止")
    
    def get_all_status(self) -> list:
        """获取所有股票状态"""
        return [trader.get_status_summary() for trader in self.traders.values()]
    
    def run(self):
        """主运行入口"""
        try:
            self.initialize_all()
            self.start_all()
            
            # 等待信号
            signal.signal(signal.SIGINT, lambda s, f: self.stop_all())
            signal.signal(signal.SIGTERM, lambda s, f: self.stop_all())
            
            while self.running:
                time.sleep(60)
                status_list = self.get_all_status()
                logger.info(f"[Multi] 状态汇总: {status_list}")
                
        except Exception as e:
            logger.exception(f"[Multi] 异常: {e}")
        finally:
            self.stop_all()


# ==================== CLI 入口 ====================
def main():
    """多股票网格交易入口"""
    logger.info("=" * 60)
    logger.info("  多股票网格交易系统")
    logger.info(f"  股票列表: {[s['code'] for s in STOCKS]}")
    logger.info(f"  日期: {date.today()}")
    logger.info("=" * 60)
    
    multi = GridTraderMulti()
    multi.run()


if __name__ == "__main__":
    main()
