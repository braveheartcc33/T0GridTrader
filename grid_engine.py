"""
grid_engine.py - 核心网格交易引擎（v3 - 破网停机版）

核心逻辑：
- 网格边界：档位 -5 到 +5（共11档）
- 盘中价格超出上下限 → 停止交易，等待尾盘
- 14:30 尾盘统一将持仓恢复到昨日收盘数量
- T+0 规则：每日卖出额度 = 底仓数量（10000股）
"""
import logging
from datetime import datetime
from typing import Optional, List
from dataclasses import dataclass
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from config import (
    GRID_COUNT, SHARES_PER_GRID, STOP_LOSS_PCT, INITIAL_BASE_SHARES,
    TRADING_MORNING_START, TRADING_MORNING_END,
    TRADING_AFTERNOON_START, TRADING_AFTERNOON_END,
)

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    timestamp: str
    action: str    # BUY / SELL
    price: float
    shares: int
    amount: float
    grid_level: int
    reason: str
    pnl: float = 0.0
    realized_pnl: float = 0.0


class GridEngine:
    """
    网格交易引擎（破网停机版）
    - 网格边界熔断：价格超出上下限（+-5档）时停止交易
    - 14:30 尾盘统一平仓回到昨日收盘数量
    - T+0 规则：每日净卖出上限 = 底仓数量
    """

    def __init__(self,
                 base_price: float,
                 grid_count: int = GRID_COUNT,
                 shares_per_grid: int = SHARES_PER_GRID,
                 stop_loss_pct: float = STOP_LOSS_PCT,
                 initial_base_shares: int = INITIAL_BASE_SHARES,
                 atr14: float = None,
                 boll_upper: float = None,
                 boll_lower: float = None,
                 boll_middle: float = None,
                 yesterday_close_position: int = None):
        self.base_price = base_price
        self.grid_count = grid_count
        self.shares_per_grid = shares_per_grid
        self.stop_loss_pct = stop_loss_pct
        self.initial_base_shares = initial_base_shares
        self.atr14 = atr14
        self.boll_upper = boll_upper
        self.boll_lower = boll_lower
        self.boll_middle = boll_middle

        # 昨日收盘持仓（强制平仓目标）
        self.yesterday_position = yesterday_close_position or initial_base_shares

        # 网格间距
        self.base_spacing = atr14 / grid_count if atr14 else base_price * 0.01
        self.last_grid_spacing = self.base_spacing

        # 网格边界
        self.MAX_LEVEL = grid_count // 2   # = 5
        self.MIN_LEVEL = -self.MAX_LEVEL   # = -5

        # 持仓状态
        self.base_position = initial_base_shares
        self.current_position = initial_base_shares
        self.position_cost = base_price
        self.base_cost = base_price

        # T+0 追踪
        self.cumulative_sells = 0
        self.cumulative_buys = 0

        # 当前档位（初始化为基准档位）
        self.current_level = 0

        # 交易记录和盈亏
        self.trade_records: List[TradeRecord] = []
        self.today_realized_pnl = 0.0
        self.realized_pnl = 0.0
        self.total_pnl = 0.0

        # 止损标记
        self.stop_loss_triggered = False

        # 构建网格
        self.grid_levels = self._build_grid()

        # 实时价格
        self.last_price: float = base_price
        # 上次成交价格（用于控制最小价格变动门槛）
        self.last_trade_price: float = base_price

        logger.info(f"[GridEngine] 初始化: 基准价={base_price}, 底仓={initial_base_shares}, "
                    f"昨日持仓={self.yesterday_position}, 每格间距={self.base_spacing:.4f}, "
                    f"网格边界={self.MIN_LEVEL}~{self.MAX_LEVEL}")

    def _build_grid(self) -> List[dict]:
        half = self.grid_count // 2
        levels = []
        for i in range(-half, half + 1):
            levels.append({'level': i, 'price': self.base_price + i * self.base_spacing})
        levels.sort(key=lambda x: x['price'])
        logger.info(f"[GridEngine] 网格 {len(levels)} 档: {levels[0]['price']:.4f}~{levels[-1]['price']:.4f}")
        return levels

    def update_grid_spacing(self, new_spacing: float):
        """更新网格间距"""
        self.last_grid_spacing = new_spacing

    def update_price(self, current_price: float):
        self.last_price = current_price

    def _is_trading_time(self, dt: datetime = None) -> bool:
        if dt is None:
            dt = datetime.now()
        cur_min = dt.hour * 60 + dt.minute
        mor_s = TRADING_MORNING_START[0] * 60 + TRADING_MORNING_START[1]
        mor_e = TRADING_MORNING_END[0] * 60 + TRADING_MORNING_END[1]
        aft_s = TRADING_AFTERNOON_START[0] * 60 + TRADING_AFTERNOON_START[1]
        aft_e = TRADING_AFTERNOON_END[0] * 60 + TRADING_AFTERNOON_END[1]
        return (mor_s <= cur_min <= mor_e) or (aft_s <= cur_min <= aft_e)

    def _is_closing_window(self, dt: datetime = None) -> bool:
        if dt is None:
            dt = datetime.now()
        cur_min = dt.hour * 60 + dt.minute
        aft_e = TRADING_AFTERNOON_END[0] * 60 + TRADING_AFTERNOON_END[1]
        return cur_min >= (aft_e - 30)

    def _price_to_level(self, price: float) -> int:
        if self.last_grid_spacing == 0:
            return 0
        return int(round((price - self.base_price) / self.last_grid_spacing))

    def _record_trade(self, action, price, shares, grid_level, reason, current_time):
        """记录一笔交易"""
        avg_cost = self.position_cost
        if action == "SELL":
            pnl = (price - avg_cost) * shares
            self.realized_pnl += pnl
            self.today_realized_pnl += pnl
            self.total_pnl += pnl
            self.current_position -= shares
            self.cumulative_sells += shares
        else:  # BUY
            pnl = 0.0
            total_cost_before = self.position_cost * self.current_position
            self.current_position += shares
            self.position_cost = (total_cost_before + price * shares) / self.current_position
            self.cumulative_buys += shares

        record = TradeRecord(
            timestamp=current_time.strftime("%Y-%m-%d %H:%M:%S"),
            action=action, price=price, shares=shares,
            amount=price * shares, grid_level=grid_level,
            reason=reason, pnl=pnl, realized_pnl=self.realized_pnl,
        )
        self.trade_records.append(record)
        return record

    def check_and_trade(self, current_price: float, current_time: datetime = None) -> tuple:
        """
        检查是否触发网格交易信号
        Returns: (TradeRecord or None, avg_cost)
        """
        if current_time is None:
            current_time = datetime.now()

        prev_price = self.last_price
        self.update_price(current_price)
        avg_cost = self.position_cost
        current_level = self._price_to_level(current_price)

        # 0. 最小价格变动门槛：两次交易之间价格变动必须 >= 一个网格间距
        price_change = abs(current_price - self.last_trade_price)
        if price_change < self.last_grid_spacing:
            logger.debug(f"[GridEngine] 跳过：价格变动{price_change:.4f} < 间距{self.last_grid_spacing:.4f}")
            return None, avg_cost

        # 1. 止损检查（不受最小价格变动门槛约束）
        loss = (current_price - self.base_price) / self.base_price
        if loss <= self.stop_loss_pct and not self.stop_loss_triggered:
            self.stop_loss_triggered = True
            record = self._record_trade(
                "SELL", current_price, self.current_position,
                0, f"止损触发, 亏损{loss*100:.2f}%, 清仓", current_time
            )
            self.last_trade_price = current_price
            logger.warning(f"[GridEngine] ⚠️ 止损触发! 价格={current_price}, 亏损={loss*100:.2f}%")
            return record, avg_cost

        # 2. 尾盘30分钟强制平仓（不受最小价格变动门槛约束）
        if self._is_closing_window(current_time):
            diff = self.current_position - self.yesterday_position
            if diff > 0:
                record = self._record_trade(
                    "SELL", current_price, diff,
                    current_level,
                    f"尾盘强制平仓, 持仓{self.current_position}→{self.yesterday_position}", current_time
                )
                self.last_trade_price = current_price
                logger.info(f"[GridEngine] 尾盘平仓: 卖{diff}股@{current_price}")
                return record, avg_cost
            elif diff < 0:
                record = self._record_trade(
                    "BUY", current_price, -diff,
                    current_level,
                    f"尾盘补回, 持仓{self.current_position}→{self.yesterday_position}", current_time
                )
                self.last_trade_price = current_price
                logger.info(f"[GridEngine] 尾盘补仓: 买{-diff}股@{current_price}")
                return record, avg_cost
            return None, avg_cost

        # 3. 非交易时段不交易
        if not self._is_trading_time(current_time):
            return None, avg_cost

        # 4. 网格边界熔断：超出上下限就停止，等待尾盘
        if current_level > self.MAX_LEVEL:
            logger.info(f"[GridEngine] ⚠️ 价格超出网格上限(档位{current_level}>{self.MAX_LEVEL})，暂停交易，等待尾盘")
            return None, avg_cost

        if current_level < self.MIN_LEVEL:
            logger.info(f"[GridEngine] ⚠️ 价格超出网格下限(档位{current_level}<{self.MIN_LEVEL})，暂停交易，等待尾盘")
            return None, avg_cost

        # 5. 正常网格交易
        # 规则1：累计买<=底仓，累计卖<=底仓
        # 规则2：每档有固定目标持仓 = 底仓 - 档位×每格股数
        # 规则3：可交易量不够时，能买/卖多少是多少
        # 附加约束：两次交易之间价格变动必须 >= 一个网格间距
        target_position = self.base_position - current_level * self.shares_per_grid
        trade_shares = abs(self.current_position - target_position)

        if trade_shares == 0:
            return None, avg_cost

        if self.current_position > target_position:
            # 持仓 > 目标 → 需要卖出
            # 可卖额度 = 底仓 - 累计卖出
            available = max(0, self.base_position - self.cumulative_sells)
            actual = min(trade_shares, available)
            if actual > 0:
                pos_before = self.current_position
                record = self._record_trade(
                    "SELL", current_price, actual, current_level,
                    f"网格@{current_price} 档={current_level} 持仓{pos_before}→{target_position}", current_time
                )
                logger.info(f"[GridEngine] 卖出: {actual}股@{current_price} 档={current_level} 持仓{pos_before}→{self.current_position}")
                self.last_trade_price = current_price
                self.current_level = current_level
                return record, avg_cost
            else:
                logger.info(f"[GridEngine] 跳过卖出: 可卖额度=0（累计已卖{self.cumulative_sells}股）")
                self.current_level = current_level
                return None, avg_cost
        else:
            # 持仓 < 目标 → 需要买入
            # 可买额度 = 底仓 - 累计买入
            available = max(0, self.base_position - self.cumulative_buys)
            actual = min(trade_shares, available)
            if actual > 0:
                record = self._record_trade(
                    "BUY", current_price, actual, current_level,
                    f"网格@{current_price} 档={current_level} 持仓{self.current_position}→{target_position}", current_time
                )
                logger.info(f"[GridEngine] 买入: {actual}股@{current_price} 档={current_level} 持仓{self.current_position}→{self.current_position}")
                self.last_trade_price = current_price
                self.current_level = current_level
                return record, avg_cost
            else:
                logger.info(f"[GridEngine] 跳过买入: 可买额度=0（累计已买{self.cumulative_buys}股）")
                self.current_level = current_level
                return None, avg_cost

        return None, avg_cost

    def force_close_all_t0(self, current_price: float, current_time: datetime = None) -> List[TradeRecord]:
        """强制平所有 T+0 仓位（14:30 尾盘调用）"""
        if current_time is None:
            current_time = datetime.now()
        records = []
        while self.current_position != self.yesterday_position:
            if self.current_position > self.yesterday_position:
                diff = self.current_position - self.yesterday_position
                r = self._record_trade(
                    "SELL", current_price, diff,
                    self._price_to_level(current_price),
                    "尾盘强制平仓", current_time
                )
                records.append(r)
            else:
                diff = self.yesterday_position - self.current_position
                r = self._record_trade(
                    "BUY", current_price, diff,
                    self._price_to_level(current_price),
                    "尾盘补回", current_time
                )
                records.append(r)
            if len(records) > 20:
                break
        return records

    def get_status(self) -> dict:
        # available_sell = 底仓 - 今日累计净卖出（买的不算！）
        # 卖了多少 = cumulative_sells
        # available_sell 永远不超过 base_position
        available_sell = max(0, self.base_position - self.cumulative_sells)
        return {
            'base_price': self.base_price,
            'current_price': self.last_price,
            'base_position': self.base_position,
            'current_position': self.current_position,
            'yesterday_position': self.yesterday_position,
            'position_cost': self.position_cost,
            'cumulative_sells': self.cumulative_sells,
            'available_sell_quota': available_sell,
            'today_float_pnl': (self.last_price - self.position_cost) * self.current_position,
            'today_realized_pnl': self.today_realized_pnl,
            'realized_pnl': self.realized_pnl,
            'total_pnl': self.total_pnl,
            'atr14': self.atr14,
            'boll_upper': self.boll_upper,
            'boll_middle': self.boll_middle,
            'boll_lower': self.boll_lower,
            'stop_loss_triggered': self.stop_loss_triggered,
            'grid_count': self.grid_count,
            'base_spacing': self.base_spacing,
            'current_spacing': self.last_grid_spacing,
            'current_level': self._price_to_level(self.last_price),
            'max_level': self.MAX_LEVEL,
            'min_level': self.MIN_LEVEL,
        }

    def get_trade_records(self) -> List[dict]:
        return [
            {
                'timestamp': r.timestamp,
                'action': r.action,
                'price': r.price,
                'shares': r.shares,
                'amount': r.amount,
                'grid_level': r.grid_level,
                'reason': r.reason,
                'pnl': r.pnl,
                'realized_pnl': r.realized_pnl,
            }
            for r in self.trade_records
        ]

    def reset_day(self):
        """新交易日重置"""
        self.today_realized_pnl = 0.0
        self.realized_pnl = 0.0
        self.trade_records = []
        self.stop_loss_triggered = False
        self.cumulative_sells = 0
        self.cumulative_buys = 0
        self.current_level = 0
        self.last_trade_price = self.base_price
        logger.info("[GridEngine] 日内重置完成")
