"""
trade_logger.py - 交易记录与持仓快照持久化
管理 trades.csv / positions.csv / state.json
"""
import os
import csv
import json
import logging
import copy
from datetime import datetime, date
from typing import Optional, List, Dict
from dataclasses import dataclass, asdict

logger = logging.getLogger("TradeLogger")

# A股印花税 + 过户费 + 佣金（估算）
COMMISSION_RATE = 0.0003   # 佣金万三（双向）
STAMP_TAX_RATE = 0.001     # 印花税 千分之一（卖出时收取）
TRANSFER_RATE = 0.00002    # 过户费 万分之0.2（双向，沪市）

TRADE_CSV_HEADER = [
    "trade_id", "timestamp", "stock_code", "stock_name",
    "action", "price", "shares", "amount",
    "commission", "stamp_tax", "net_amount",
    "realized_pnl", "cumulative_pnl",
    "grid_level", "reason",
]
POSITION_CSV_HEADER = [
    "snapshot_time", "stock_code", "stock_name",
    "current_price", "position_shares", "position_cost",
    "float_pnl", "realized_pnl", "available_t0_shares",
    "atr14", "boll_upper", "boll_middle", "boll_lower",
    "grid_spacing", "grid_level", "base_price",
    "today_t0", "today_position_pnl", "today_total_pnl",
]


@dataclass
class TradeEntry:
    """单笔成交记录"""
    trade_id: str
    timestamp: str
    stock_code: str
    stock_name: str
    action: str          # BUY / SELL
    price: float
    shares: int
    amount: float         # gross amount (price * shares)
    commission: float     # 手续费（佣金）
    stamp_tax: float      # 印花税（仅卖出）
    net_amount: float    # 净金额（扣除费用）
    realized_pnl: float   # 这笔交易实现的盈亏
    cumulative_pnl: float # 累计已实现盈亏
    grid_level: int
    reason: str


@dataclass
class PositionSnapshot:
    """持仓快照"""
    snapshot_time: str
    stock_code: str
    stock_name: str
    current_price: float
    position_shares: int
    position_cost: float       # 持仓成本（参考）
    float_pnl: float           # 浮动盈亏（≈持仓盈亏）
    realized_pnl: float        # 已实现盈亏（旧口径，毛）
    available_t0_shares: int  # 可用于T+0的股数
    atr14: float
    boll_upper: float
    boll_middle: float
    boll_lower: float
    grid_spacing: float
    grid_level: int
    base_price: float
    # 今日盈亏三因子（新口径，2026-03-27）
    today_t0: float = 0.0          # T0 盈利 = Σ卖出金额 - Σ买入金额
    today_position_pnl: float = 0.0  # 持仓盈亏 = (当前价 - 基准价) × 昨日持仓
    today_total_pnl: float = 0.0      # 今日总盈亏 = T0 + 持仓盈亏


class TradeLogger:
    """
    交易记录与持仓快照管理器
    - trades.csv    每笔成交明细
    - positions.csv 每隔 N 秒（或每笔交易后）的持仓快照
    - state.json    完整状态，用于崩溃恢复
    """

    def __init__(self, data_dir: str = None):
        if data_dir is None:
            data_dir = os.path.dirname(os.path.abspath(__file__))
        self.data_dir = data_dir
        self.today_str = date.today().strftime("%Y%m%d")

        self.trades_csv = os.path.join(data_dir, f"trades_{self.today_str}.csv")
        self.positions_csv = os.path.join(data_dir, f"positions_{self.today_str}.csv")
        self.state_json = os.path.join(data_dir, "state.json")

        # 今日交易计数器
        self.trade_count = self._load_trade_count()
        self.last_snapshot_time = datetime.now()

        # 初始化 CSV 文件头（如不存在）
        self._ensure_csv_headers()

        logger.info(f"[TradeLogger] 初始化完成，数据目录: {data_dir}")
        logger.info(f"[TradeLogger] trades: {self.trades_csv}")
        logger.info(f"[TradeLogger] positions: {self.positions_csv}")
        logger.info(f"[TradeLogger] state: {self.state_json}")

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _ensure_csv_headers(self):
        """确保 CSV 文件存在且有表头"""
        if not os.path.exists(self.trades_csv):
            with open(self.trades_csv, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(TRADE_CSV_HEADER)

        if not os.path.exists(self.positions_csv):
            with open(self.positions_csv, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(POSITION_CSV_HEADER)

    def _load_trade_count(self) -> int:
        """从现有 trades.csv 恢复今日交易计数"""
        if not os.path.exists(self.trades_csv):
            return 0
        try:
            with open(self.trades_csv, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                next(reader, None)  # 跳过表头
                count = sum(1 for _ in reader)
            return count
        except Exception:
            return 0

    def _calc_commission(self, amount: float) -> float:
        """计算佣金（双向）"""
        c = amount * COMMISSION_RATE
        return max(c, 5.0)  # 最低佣金 5 元

    def _calc_stamp_tax(self, amount: float) -> float:
        """计算印花税（仅卖出，千分之一）"""
        return amount * STAMP_TAX_RATE

    # ------------------------------------------------------------------
    # 核心 API
    # ------------------------------------------------------------------

    def log_trade(self,
                  stock_code: str,
                  stock_name: str,
                  action: str,
                  price: float,
                  shares: int,
                  grid_level: int,
                  reason: str,
                  cumulative_pnl: float,
                  avg_cost: float = None) -> TradeEntry:
        """
        记录一笔成交到 trades.csv

        Args:
            avg_cost: 这笔卖出的平均成本（用于计算 realized_pnl）
        """
        self.trade_count += 1
        trade_id = f"{self.today_str}_{self.trade_count:04d}"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        gross_amount = price * shares
        commission = self._calc_commission(gross_amount)
        stamp_tax = self._calc_stamp_tax(gross_amount) if action == "SELL" else 0.0
        net_amount = gross_amount - commission - stamp_tax

        # 计算这笔交易的已实现盈亏（卖出时）
        if action == "SELL" and avg_cost is not None:
            realized_pnl = (price - avg_cost) * shares - commission - stamp_tax
        else:
            realized_pnl = 0.0

        entry = TradeEntry(
            trade_id=trade_id,
            timestamp=timestamp,
            stock_code=stock_code,
            stock_name=stock_name,
            action=action,
            price=price,
            shares=shares,
            amount=gross_amount,
            commission=commission,
            stamp_tax=stamp_tax,
            net_amount=net_amount,
            realized_pnl=realized_pnl,
            cumulative_pnl=cumulative_pnl,
            grid_level=grid_level,
            reason=reason,
        )

        # 写入 CSV
        with open(self.trades_csv, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                entry.trade_id,
                entry.timestamp,
                entry.stock_code,
                entry.stock_name,
                entry.action,
                f"{entry.price:.4f}",
                entry.shares,
                f"{entry.amount:.2f}",
                f"{entry.commission:.2f}",
                f"{entry.stamp_tax:.2f}",
                f"{entry.net_amount:.2f}",
                f"{entry.realized_pnl:.2f}",
                f"{entry.cumulative_pnl:.2f}",
                entry.grid_level,
                entry.reason,
            ])

        logger.info(
            f"[TradeLogger] 成交记录 #{trade_id} {action} {shares}股@{price:.4f} "
            f"金额={gross_amount:.2f} 费用={commission+stamp_tax:.2f} "
            f"实现盈亏={realized_pnl:.2f}"
        )

        return entry

    def log_position_snapshot(self,
                              stock_code: str,
                              stock_name: str,
                              current_price: float,
                              position_shares: int,
                              position_cost: float,
                              base_position: int,
                              realized_pnl: float,
                              atr14: float,
                              boll_upper: float,
                              boll_middle: float,
                              boll_lower: float,
                              grid_spacing: float,
                              grid_level: int,
                              base_price: float,
                              today_t0: float = 0.0,
                              today_position_pnl: float = 0.0,
                              today_total_pnl: float = 0.0) -> PositionSnapshot:
        """
        记录一个持仓快照到 positions.csv
        每次网格交易后自动调用；每隔30分钟 mainloop 也会主动调用
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 浮动盈亏：按成本计算
        float_pnl = (current_price - position_cost) * position_shares if position_cost > 0 else 0.0

        # 可T+0股数 = 当前持仓 - 底仓（>0才有）
        available_t0 = max(0, position_shares - base_position)

        snapshot = PositionSnapshot(
            snapshot_time=now_str,
            stock_code=stock_code,
            stock_name=stock_name,
            current_price=current_price,
            position_shares=position_shares,
            position_cost=position_cost,
            float_pnl=float_pnl,
            realized_pnl=realized_pnl,
            available_t0_shares=available_t0,
            atr14=atr14,
            boll_upper=boll_upper,
            boll_middle=boll_middle,
            boll_lower=boll_lower,
            grid_spacing=grid_spacing,
            grid_level=grid_level,
            base_price=base_price,
            today_t0=today_t0,
            today_position_pnl=today_position_pnl,
            today_total_pnl=today_total_pnl,
        )

        with open(self.positions_csv, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                snapshot.snapshot_time,
                snapshot.stock_code,
                snapshot.stock_name,
                f"{snapshot.current_price:.4f}",
                snapshot.position_shares,
                f"{snapshot.position_cost:.4f}",
                f"{snapshot.float_pnl:.2f}",
                f"{snapshot.realized_pnl:.2f}",
                snapshot.available_t0_shares,
                f"{snapshot.atr14:.4f}",
                f"{snapshot.boll_upper:.4f}",
                f"{snapshot.boll_middle:.4f}",
                f"{snapshot.boll_lower:.4f}",
                f"{snapshot.grid_spacing:.4f}",
                snapshot.grid_level,
                f"{snapshot.base_price:.4f}",
                f"{snapshot.today_t0:.2f}",
                f"{snapshot.today_position_pnl:.2f}",
                f"{snapshot.today_total_pnl:.2f}",
            ])

        return snapshot

    def save_state(self,
                   engine_state: dict,
                   market_state: dict,
                   today_date: str = None) -> str:
        """
        将完整状态保存到 state.json（用于崩溃恢复）
        """
        if today_date is None:
            today_date = self.today_str

        state = {
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "date": today_date,
            "engine": engine_state,
            "market": market_state,
        }

        # 原子写入：先写临时文件再 rename
        tmp = self.state_json + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.state_json)

        logger.info(f"[TradeLogger] 状态已保存到 state.json")
        return self.state_json

    def load_state(self) -> Optional[dict]:
        """
        从 state.json 恢复状态
        Returns None if 文件不存在或日期非今日
        """
        if not os.path.exists(self.state_json):
            logger.warning("[TradeLogger] state.json 不存在，无法恢复")
            return None

        try:
            with open(self.state_json, "r", encoding="utf-8") as f:
                state = json.load(f)

            saved_date = state.get("date", "")
            if saved_date != self.today_str:
                logger.warning(f"[TradeLogger] state.json 日期 {saved_date} 非今日，不恢复")
                return None

            logger.info(f"[TradeLogger] 状态恢复成功: saved_at={state.get('saved_at')}")
            return state

        except Exception as e:
            logger.error(f"[TradeLogger] 读取 state.json 失败: {e}")
            return None

    # ------------------------------------------------------------------
    # 复盘查询
    # ------------------------------------------------------------------

    def get_today_trades(self) -> List[TradeEntry]:
        """读取今日所有成交记录"""
        trades = []
        if not os.path.exists(self.trades_csv):
            return trades
        try:
            with open(self.trades_csv, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    trades.append(TradeEntry(
                        trade_id=row["trade_id"],
                        timestamp=row["timestamp"],
                        stock_code=row["stock_code"],
                        stock_name=row["stock_name"],
                        action=row["action"],
                        price=float(row["price"]),
                        shares=int(row["shares"]),
                        amount=float(row["amount"]),
                        commission=float(row["commission"]),
                        stamp_tax=float(row["stamp_tax"]),
                        net_amount=float(row["net_amount"]),
                        realized_pnl=float(row["realized_pnl"]),
                        cumulative_pnl=float(row["cumulative_pnl"]),
                        grid_level=int(row["grid_level"]),
                        reason=row["reason"],
                    ))
        except Exception as e:
            logger.error(f"[TradeLogger] 读取 trades.csv 失败: {e}")
        return trades

    def get_today_positions(self) -> List[PositionSnapshot]:
        """读取今日所有持仓快照"""
        snapshots = []
        if not os.path.exists(self.positions_csv):
            return snapshots
        try:
            with open(self.positions_csv, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    snapshots.append(PositionSnapshot(
                        snapshot_time=row["snapshot_time"],
                        stock_code=row["stock_code"],
                        stock_name=row["stock_name"],
                        current_price=float(row["current_price"]),
                        position_shares=int(row["position_shares"]),
                        position_cost=float(row["position_cost"]),
                        float_pnl=float(row["float_pnl"]),
                        realized_pnl=float(row["realized_pnl"]),
                        available_t0_shares=int(row["available_t0_shares"]),
                        atr14=float(row["atr14"]),
                        boll_upper=float(row["boll_upper"]),
                        boll_middle=float(row["boll_middle"]),
                        boll_lower=float(row["boll_lower"]),
                        grid_spacing=float(row["grid_spacing"]),
                        grid_level=int(row["grid_level"]),
                        base_price=float(row["base_price"]),
                        today_t0=float(row.get("today_t0", 0.0)),
                        today_position_pnl=float(row.get("today_position_pnl", 0.0)),
                        today_total_pnl=float(row.get("today_total_pnl", 0.0)),
                    ))
        except Exception as e:
            logger.error(f"[TradeLogger] 读取 positions.csv 失败: {e}")
        return snapshots

    def print_day_summary(self):
        """打印今日复盘摘要"""
        trades = self.get_today_trades()
        positions = self.get_today_positions()

        if not trades:
            print("\n  今日无成交记录")
            return

        buy_count = sum(1 for t in trades if t.action == "BUY")
        sell_count = sum(1 for t in trades if t.action == "SELL")
        total_fee = sum(t.commission + t.stamp_tax for t in trades)
        total_realized = sum(t.realized_pnl for t in trades)

        last_trade = trades[-1]
        first_trade = trades[0]

        print("\n" + "=" * 60)
        print("  今日交易复盘摘要")
        print("=" * 60)
        print(f"  股票: {first_trade.stock_code} {first_trade.stock_name}")
        print(f"  首笔: {first_trade.timestamp} {first_trade.action} {first_trade.shares}股@{first_trade.price:.4f}")
        print(f"  末笔: {last_trade.timestamp} {last_trade.action} {last_trade.shares}股@{last_trade.price:.4f}")
        print(f"  买入: {buy_count} 笔")
        print(f"  卖出: {sell_count} 笔")
        print(f"  总手续费: {total_fee:.2f} 元")
        print(f"  已实现盈亏: {'+' if total_realized >= 0 else ''}{total_realized:.2f} 元")
        print(f"  持仓快照数: {len(positions)}")
        print("=" * 60)

        if positions:
            last_pos = positions[-1]
            print(f"\n  最新持仓快照 ({last_pos.snapshot_time}):")
            print(f"    价格={last_pos.current_price:.4f} | 持仓={last_pos.position_shares}股")
            print(f"    浮动盈亏={last_pos.float_pnl:.2f} | 已实现={last_pos.realized_pnl:.2f}")
