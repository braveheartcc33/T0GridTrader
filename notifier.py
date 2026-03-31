"""
notifier.py - 飞书通知模块
"""
import logging
import time
from datetime import datetime
from typing import Optional

import httpx

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from config import FEISHU_WEBHOOK_URL, FEISHU_SIGNING_KEY, STOCK_CODE

logger = logging.getLogger(__name__)


def generate_sign(timestamp: str, secret: str) -> str:
    """生成飞书签名"""
    import hmac, hashlib, base64
    string_to_sign = f"{timestamp}\n{secret}"
    sign = base64.b64encode(hmac.new(
        string_to_sign.encode('utf-8'),
        b'',
        hashlib.sha256
    ).digest()).decode('utf-8')
    return sign


class GridNotifier:
    """网格交易飞书通知器"""

    def __init__(self,
                 webhook_url: Optional[str] = None,
                 signing_key: Optional[str] = None,
                 stock_code: str = None,
                 stock_name: str = None):
        self.webhook_url = webhook_url or FEISHU_WEBHOOK_URL
        self.signing_key = signing_key or FEISHU_SIGNING_KEY
        self.stock_code = stock_code
        self.stock_name = stock_name
        self.enabled = not self.webhook_url.startswith("https://open.feishu.cn")

    def _sign_payload(self, payload: dict) -> dict:
        """为 payload 添加签名"""
        timestamp = str(int(time.time()))
        sign = generate_sign(timestamp, self.signing_key)
        payload["timestamp"] = timestamp
        payload["sign"] = sign
        return payload

    def send_text(self, message: str) -> bool:
        """发送纯文本消息"""
        if self.enabled:
            logger.warning("飞书 Webhook 未配置，跳过发送")
            return False

        try:
            payload = {
                "msg_type": "text",
                "content": {"text": message}
            }
            payload = self._sign_payload(payload)

            with httpx.Client(timeout=10.0) as client:
                response = client.post(self.webhook_url, json=payload)

            if response.status_code == 200:
                result = response.json()
                if result.get("code") == 0 or result.get("StatusCode") == 0:
                    return True
            logger.error(f"飞书发送失败: {response.text}")
            return False
        except Exception as e:
            logger.error(f"飞书发送异常: {e}")
            return False

    def send_trade_signal(self, signal_type: str, price: float,
                          grid_level: int, action: str,
                          shares: int, reason: str = "",
                          available_sell: int = 0, current_position: int = 0,
                          base_position: int = 0, total_levels: int = 10,
                          atr14: float = 0.0, grid_spacing: float = 0.0,
                          spacing_multiplier: float = 1.0,
                          today_t0: float = None,
                          today_position_pnl: float = None,
                          today_total_pnl: float = None) -> bool:
        """
        发送网格交易信号

        Args:
            signal_type: BUY / SELL / STOP_LOSS / INFO
            price: 触发价格
            grid_level: 网格档位
            action: 买入/卖出描述
            shares: 股数
            reason: 原因说明
            today_t0: 今日 T0 盈利
            today_position_pnl: 今日持仓盈亏
            today_total_pnl: 今日总盈亏
        """
        emoji_map = {
            "BUY": "🟢",
            "SELL": "🔴",
            "STOP_LOSS": "🚨",
            "INFO": "ℹ️",
        }
        emoji = emoji_map.get(signal_type, "ℹ️")

        title_map = {
            "BUY": "网格买入信号",
            "SELL": "网格卖出信号",
            "STOP_LOSS": "⚠️ 止损信号",
            "INFO": "系统通知",
        }
        title = title_map.get(signal_type, "通知")

        stock_display = f"{self.stock_code} {self.stock_name}" if self.stock_name else (self.stock_code or STOCK_CODE)
        message_parts = [
            f"{emoji} **{title}**",
            f"时间: {datetime.now().strftime('%H:%M:%S')}",
            f"股票: {stock_display}",
            f"触发价格: {price:.3f}",
            f"网格档位: 第 {grid_level} 档（共{total_levels}档）",
            f"ATR(14): {atr14:.4f} | 间距: {grid_spacing:.4f} (x{spacing_multiplier:.1f})",
            f"交易方向: {action}",
            f"交易数量: {shares} 股",
            f"持仓: {current_position} | 底仓: {base_position} | 可卖出: {available_sell}",
            f"原因: {reason}",
        ]

        # 今日盈亏三因子（T0 / 持仓 / 合计）
        if today_t0 is not None and today_position_pnl is not None and today_total_pnl is not None:
            t0_str = f"{'+' if today_t0 >= 0 else ''}{today_t0:.2f}"
            pos_str = f"{'+' if today_position_pnl >= 0 else ''}{today_position_pnl:.2f}"
            tot_str = f"{'+' if today_total_pnl >= 0 else ''}{today_total_pnl:.2f}"
            message_parts.append(f"今日 T0: {t0_str} | 持仓: {pos_str} | 合计: {tot_str}")

        message = "\n".join(message_parts)
        return self.send_text(message)

    def send_status_report(self,
                           current_price: float,
                           position: int,
                           base_position: int,
                           today_t0: float,
                           today_position_pnl: float,
                           today_total_pnl: float,
                           grid_status: dict,
                           indicators: dict) -> bool:
        """
        发送持仓状态汇报（每30分钟）
        """
        position_type = "加仓中" if position > base_position else ("减仓中" if position < base_position else "仅底仓")

        stock_display = f"{self.stock_code} {self.stock_name}" if self.stock_name else (self.stock_code or STOCK_CODE)
        message = (
            f"📊 **网格交易状态汇报**\n"
            f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"股票: {stock_display}\n"
            f"当前价格: {current_price:.3f}\n"
            f"持仓状态: {position_type}（{position}股 / 底仓{base_position}股）\n"
            f"T0 盈利: {'+' if today_t0 >= 0 else ''}{today_t0:.2f} 元\n"
            f"持仓盈亏: {'+' if today_position_pnl >= 0 else ''}{today_position_pnl:.2f} 元\n"
            f"今日总盈亏: {'+' if today_total_pnl >= 0 else ''}{today_total_pnl:.2f} 元\n"
            f"指标 - ATR(14): {indicators.get('atr14', 0):.4f}\n"
            f"指标 - 布林上轨: {indicators.get('boll_upper', 0):.4f}\n"
            f"指标 - 布林中轨: {indicators.get('boll_middle', 0):.4f}\n"
            f"指标 - 布林下轨: {indicators.get('boll_lower', 0):.4f}\n"
            f"基准价: {grid_status.get('base_price', 0):.3f}\n"
            f"当前档位: {grid_status.get('current_level', 0)} / {grid_status.get('total_levels', 10)}"
        )

        if self.enabled:
            logger.info(f"[Status] {message}")
            return False

        try:
            payload = {
                "msg_type": "text",
                "content": {"text": message}
            }
            payload = self._sign_payload(payload)

            with httpx.Client(timeout=10.0) as client:
                response = client.post(self.webhook_url, json=payload)

            if response.status_code == 200:
                result = response.json()
                return result.get("code") == 0 or result.get("StatusCode") == 0
            return False
        except Exception as e:
            logger.error(f"飞书状态汇报异常: {e}")
            return False

    def send_init_report(self, indicators: dict, base_price: float,
                         grid_count: int, spacing: float) -> bool:
        """发送系统初始化报告"""
        stock_display = f"{self.stock_code} {self.stock_name}" if self.stock_name else (self.stock_code or STOCK_CODE)
        message = (
            f"🚀 **网格交易系统启动**\n"
            f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"股票: {stock_display}\n"
            f"基准价: {base_price:.3f}\n"
            f"网格数量: {grid_count} 档\n"
            f"每格间距(ATR): {spacing:.4f}\n"
            f"ATR(14): {indicators.get('atr14', 0):.4f}\n"
            f"布林上轨: {indicators.get('boll_upper', 0):.4f}\n"
            f"布林中轨: {indicators.get('boll_middle', 0):.4f}\n"
            f"布林下轨: {indicators.get('boll_lower', 0):.4f}\n"
            f"最后收盘价: {indicators.get('last_close', 0):.3f}"
        )
        return self.send_text(message)
