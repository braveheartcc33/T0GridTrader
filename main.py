"""
main.py - 网格交易系统主循环
明日（2026-03-25）开盘启动
"""
import logging
import sys
import os
import time
import signal
from datetime import datetime, date, timedelta
from threading import Thread, Event

# 设置工作目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("grid_trader.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger("GridTrader")

# 全局停止标志
shutdown_event = Event()


def signal_handler(signum, frame):
    """处理退出信号"""
    logger.warning(f"收到信号 {signum}，准备关闭...")
    shutdown_event.set()


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


from config import STOCK_CODE, STOCK_NAME

class GridTraderApp:
    """
    网格交易主程序
    """

    def __init__(self):
        from market_data import MarketDataManager
        from grid_engine import GridEngine
        from notifier import GridNotifier
        from trade_logger import TradeLogger
        from config import (
            GRID_COUNT, SHARES_PER_GRID,
            STOP_LOSS_PCT, INITIAL_BASE_SHARES,
            POLL_INTERVAL_SEC,
            TRADING_MORNING_START, TRADING_MORNING_END,
            TRADING_AFTERNOON_START, TRADING_AFTERNOON_END,
        )
        # 绑定到实例，避免局部导入在其他方法中不可见
        self.GridEngineCls = GridEngine


        self.STOCK_CODE = STOCK_CODE
        self.STOCK_NAME = STOCK_NAME
        self.POLL_INTERVAL_SEC = POLL_INTERVAL_SEC

        # 交易时间段（分钟）
        self.mor_start = TRADING_MORNING_START[0] * 60 + TRADING_MORNING_START[1]  # 570
        self.mor_end = TRADING_MORNING_END[0] * 60 + TRADING_MORNING_END[1]          # 690
        self.aft_start = TRADING_AFTERNOON_START[0] * 60 + TRADING_AFTERNOON_START[1]  # 780
        self.aft_end = TRADING_AFTERNOON_END[0] * 60 + TRADING_AFTERNOON_END[1]        # 900

        self.market_mgr = MarketDataManager(STOCK_CODE)
        self.notifier = GridNotifier()
        self.trade_logger = TradeLogger(BASE_DIR)
        self.engine = None

        # 状态汇报计时
        self.last_status_time = datetime.now()
        self.status_interval_seconds = 30 * 60  # 每30分钟
        self.last_snapshot_time = datetime.now()
        self.snapshot_interval_seconds = 60      # 每60秒记录持仓快照

        logger.info("=" * 60)
        logger.info("  网格交易系统初始化")
        logger.info(f"  股票: {STOCK_CODE}")
        logger.info(f"  日期: {date.today()}")
        logger.info("=" * 60)

    def _is_trading_time(self, dt: datetime = None) -> bool:
        """判断是否在交易时段（不含午间休市）"""
        if dt is None:
            dt = datetime.now()
        cur_min = dt.hour * 60 + dt.minute
        return (self.mor_start <= cur_min <= self.mor_end) or (self.aft_start <= cur_min <= self.aft_end)

    def _is_closing_time(self, dt: datetime = None) -> bool:
        """尾盘30分钟（14:30起）"""
        if dt is None:
            dt = datetime.now()
        cur_min = dt.hour * 60 + dt.minute
        return cur_min >= (self.aft_end - 30)

    def _minutes_to_close(self, dt: datetime = None) -> int:
        """距离收盘还有多少分钟"""
        if dt is None:
            dt = datetime.now()
        cur_min = dt.hour * 60 + dt.minute
        if self.aft_start <= cur_min <= self.aft_end:
            return self.aft_end - cur_min
        return -1

    def wait_until_market_open(self):
        """等待直到开盘（09:25 集合竞价后正式启动）"""
        logger.info("[Init] 等待开盘，当前为盘后/隔夜阶段...")

        while not shutdown_event.is_set():
            now = datetime.now()
            cur_min = now.hour * 60 + now.minute

            if self.mor_start <= cur_min <= self.mor_end or self.aft_start <= cur_min <= self.aft_end:
                logger.info(f"[Init] 交易时段开始，当前时间 {now.strftime('%H:%M:%S')}")
                return True

            next_open = now.replace(hour=9, minute=25, second=0, microsecond=0)
            if now.hour >= 15 or (now.hour == 11 and now.minute > 30):
                next_open += timedelta(days=1)
            elif now.hour < 9 or (now.hour == 9 and now.minute < 25):
                pass
            elif self.mor_end < cur_min < self.aft_start:
                next_open = now.replace(hour=13, minute=0, second=0, microsecond=0)

            wait_sec = (next_open - now).total_seconds()
            if wait_sec > 0:
                logger.info(f"[Init] 距离开盘还有 {int(wait_sec//60)} 分，休眠...")
                shutdown_event.wait(timeout=min(wait_sec, 300))
            else:
                time.sleep(30)

        return False

    def initialize(self):
        """系统初始化：从 tushare 加载历史数据，启动引擎"""
        logger.info("[Init] 开始初始化市场数据...")

        indicators = self.market_mgr.initialize()

        base_price = self.market_mgr._today_open
        if base_price is None:
            base_price = indicators['last_close']
            logger.info(f"[Init] 无今日开盘价，使用昨日收盘价: {base_price}")

        logger.info(f"[Init] 基准价: {base_price}, ATR(14): {indicators['atr14']:.4f}")
        logger.info(f"[Init] 布林带: {indicators['boll_lower']:.4f} ~ {indicators['boll_upper']:.4f}")

        self.engine = self.GridEngineCls(
            base_price=base_price,
            atr14=indicators['atr14'],
            boll_upper=indicators['boll_upper'],
            boll_lower=indicators['boll_lower'],
            boll_middle=indicators['boll_middle'],
            yesterday_close_position=indicators.get('last_close_position', 10000),
        )

        # 发送初始化报告
        spacing = indicators['atr14'] / self.engine.grid_count
        self.notifier.send_init_report(indicators, base_price,
                                        self.engine.grid_count, spacing)

        # 保存初始状态
        self._save_state()

        # 记录初始持仓快照
        self._log_position_snapshot()

        logger.info("[Init] 初始化完成，等待交易时段...")
        return True

    def trading_loop(self):
        """盘中主循环：每3-5秒轮询腾讯实时行情，检查网格触发"""
        logger.info("[Loop] 进入交易循环...")

        last_price = None
        no_data_count = 0

        while not shutdown_event.is_set():
            now = datetime.now()

            # 非交易时段
            if not self._is_trading_time(now):
                time.sleep(10)
                continue

            # 尾盘强制平仓：不管持仓多少，全部平回昨天收盘数量
            if self._is_closing_time(now):
                mins_left = self._minutes_to_close(now)
                logger.info(f"[Loop] 进入尾盘，最后 {mins_left} 分钟，强制平仓...")
                price = self.market_mgr.get_realtime_price()
                if price:
                    records = self.engine.force_close_all_t0(price, now)
                    for rec in records:
                        self._log_trade(rec)
                    self._save_state()
                self._send_status_report(now)
                self._log_position_snapshot()
                break

            # 获取实时价格
            price = self.market_mgr.get_realtime_price()

            if price is None:
                no_data_count += 1
                if no_data_count % 5 == 0:
                    logger.warning(f"[Loop] 连续 {no_data_count} 次获取行情失败")
                time.sleep(self.POLL_INTERVAL_SEC)
                continue

            no_data_count = 0

            # 价格无变化，跳过
            if last_price is not None and abs(price - last_price) < 0.001:
                time.sleep(self.POLL_INTERVAL_SEC)
                continue

            last_price = price

            # 更新动态网格间距
            current_spacing = self.market_mgr.get_grid_spacing(now)
            if abs(current_spacing - self.engine.last_grid_spacing) > 0.0001:
                self.engine.update_grid_spacing(current_spacing)
                logger.info(f"[Loop] 网格间距更新: {current_spacing:.4f}")

            # 检查网格触发
            result = self.engine.check_and_trade(price, now)
            record, avg_cost = result if result else (None, None)

            if record:
                self._log_trade(record)
                self._log_position_snapshot()
                self._save_state()

                # 飞书通知：直接从engine读取最新值，不用get_status()（避免读到旧state）
                indicators = self.market_mgr.get_indicators()
                spacing, multiplier = self.market_mgr.get_grid_spacing_with_multiplier()
                # 可卖出 = max(0, 底仓 - 今日累计卖出)
                available_sell = max(0, self.engine.base_position - self.engine.cumulative_sells)
                self.notifier.send_trade_signal(
                    signal_type=record.action,
                    price=record.price,
                    grid_level=record.grid_level,
                    action="买入" if record.action == "BUY" else "卖出",
                    shares=record.shares,
                    reason=record.reason,
                    available_sell=available_sell,
                    current_position=self.engine.current_position,
                    base_position=self.engine.base_position,
                    total_levels=self.engine.MAX_LEVEL * 2,
                    atr14=indicators.get('atr14', 0),
                    grid_spacing=spacing,
                    spacing_multiplier=multiplier,
                )

            # 每30分钟状态汇报
            elapsed_status = (now - self.last_status_time).total_seconds()
            if elapsed_status >= self.status_interval_seconds:
                self._send_status_report(now)
                self.last_status_time = now

            # 每分钟持仓快照
            elapsed_snapshot = (now - self.last_snapshot_time).total_seconds()
            if elapsed_snapshot >= self.snapshot_interval_seconds:
                self._log_position_snapshot()
                self.last_snapshot_time = now

            # 日志输出
            status = self.engine.get_status()
            avail = max(0, self.engine.base_position - self.engine.cumulative_sells)
            logger.info(
                f"[Tick] 价格={price:.3f} | "
                f"持仓={status['current_position']}股 "
                f"(成本={status['position_cost']:.4f}, 可卖={avail}) | "
                f"已实现={status['today_realized_pnl']:.2f} | "
                f"浮动={status['today_float_pnl']:.2f} | "
                f"间距={current_spacing:.4f} | 档位={status['current_level']}"
            )

            time.sleep(self.POLL_INTERVAL_SEC)

        logger.info("[Loop] 交易循环结束")

    # ------------------------------------------------------------------
    # 持久化方法
    # ------------------------------------------------------------------

    def _log_trade(self, record):
        """将成交记录写入 trades.csv + state.json"""
        status = self.engine.get_status()

        entry = self.trade_logger.log_trade(
            stock_code=self.STOCK_CODE,
            stock_name=self.STOCK_NAME,
            action=record.action,
            price=record.price,
            shares=record.shares,
            grid_level=record.grid_level,
            reason=record.reason,
            cumulative_pnl=status['today_realized_pnl'],
            avg_cost=status['position_cost'],
        )

        logger.info(
            f"[TradeLogger] 成交 #{entry.trade_id} {record.action} "
            f"{record.shares}股@{record.price:.4f} "
            f"金额={record.amount:.2f} 费用={entry.commission+entry.stamp_tax:.2f} "
            f"实现盈亏={entry.realized_pnl:.2f}"
        )

    def _log_position_snapshot(self):
        """将当前持仓快照写入 positions.csv"""
        status = self.engine.get_status()
        indicators = self.market_mgr.indicators

        self.trade_logger.log_position_snapshot(
            stock_code=self.STOCK_CODE,
            stock_name=self.STOCK_NAME,
            current_price=status['current_price'],
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
        )

    def _save_state(self):
        """保存完整状态到 state.json"""
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
        }
        market_state = {
            'atr14': status['atr14'],
            'boll_upper': status['boll_upper'],
            'boll_middle': status['boll_middle'],
            'boll_lower': status['boll_lower'],
            'last_price': status['current_price'],
        }
        self.trade_logger.save_state(engine_state, market_state)

    def _send_status_report(self, now: datetime):
        """发送状态汇报（飞书）"""
        status = self.engine.get_status()
        indicators = self.market_mgr.indicators

        self.notifier.send_status_report(
            current_price=status['current_price'],
            position=status['current_position'],
            base_position=status['base_position'],
            today_pnl=status['today_realized_pnl'],
            grid_status=status,
            indicators=indicators,
        )
        logger.info(f"[Status] 已发送状态汇报: 持仓={status['current_position']}股, "
                    f"已实现={status['today_realized_pnl']:.2f}")

    def run(self):
        """主运行入口"""
        try:
            self.initialize()

            if not self.wait_until_market_open():
                logger.info("系统退出（收到停止信号）")
                return

            self.trading_loop()
            self._send_final_report()

        except Exception as e:
            logger.exception(f"[Fatal] 系统异常: {e}")
            self.notifier.send_text(f"🚨 网格交易系统异常: {e}")

    def _send_final_report(self):
        """发送收盘报告"""
        try:
            status = self.engine.get_status()
            records = self.engine.get_trade_records()

            trades_text = "\n".join([
                f"{r['timestamp']} {r['action']} {r['price']:.3f}x{r['shares']}={r['amount']:.2f} ({r['reason']})"
                for r in records[-10:]
            ]) if records else "今日无交易"

            message = (
                f"📋 **收盘报告**\n"
                f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"股票: {self.STOCK_CODE} {self.STOCK_NAME}\n"
                f"收盘价: {status['current_price']:.3f}\n"
                f"持仓: {status['current_position']}股 (底仓{status['base_position']}股)\n"
                f"持仓成本: {status['position_cost']:.4f}\n"
                f"已实现盈亏: {'+' if status['today_realized_pnl'] >= 0 else ''}{status['today_realized_pnl']:.2f} 元\n"
                f"今日交易笔数: {len(records)}\n"
                f"---\n{trades_text}"
            )
            self.notifier.send_text(message)
            logger.info("[Final] 收盘报告已发送")

            # 打印复盘摘要
            self.trade_logger.print_day_summary()

        except Exception as e:
            logger.error(f"[Final] 收盘报告发送失败: {e}")


def main():
    """入口"""
    logger.info("=" * 60)
    logger.info("  A股网格交易系统 - 明日开盘启动")
    logger.info(f"  股票: {STOCK_CODE}")
    logger.info(f"  日期: {__import__('datetime').date.today().strftime('%Y-%m-%d')} (今日收盘，明日启动)")
    logger.info("=" * 60)

    app = GridTraderApp()

    logger.info("")
    logger.info("=" * 60)
    logger.info("  [演示模式] 今日盘后初始化验证")
    logger.info("=" * 60)

    try:
        # 正式启动交易系统
        logger.info("[Main] 正式启动交易系统...")
        app.run()

    except Exception as e:
        logger.exception(f"[Fatal] 系统启动失败: {e}")
        print(f"\n❌ 系统启动失败: {e}")


if __name__ == "__main__":
    main()
