"""
Grid Trader 配置
A股网格交易系统 - 配置参数
"""
import os

# ==================== 股票配置 ====================
STOCK_CODE = "159567.SZ"
STOCK_NAME = "港股创新药"

# ==================== 网格参数 ====================
GRID_COUNT = 10           # 网格档位数量
STOP_LOSS_PCT = -0.02     # 止损幅度 -2%
INITIAL_BASE_SHARES = 10000   # 初始底仓股数（T+0需要先有底仓）

# 每格股数（固定，根据资金管理计算）
# 每次网格触发买卖的股数
SHARES_PER_GRID = 1000    # 每格1000股

# 基准价：开盘价（或昨日收盘价），系统启动时自动设置
BASE_PRICE = None         # 运行时设置

# ==================== 动态网格间距参数 ====================
# 网格间距分段配置
# 格式：((小时, 分钟), 倍数)，按时间升序
# 如果当前时间不匹配任何段，用 BASE_MULTIPLIER
# 每格基础间距 = ATR(14) / GRID_COUNT × 倍数
GRID_SPACING_RULES = [
    ((9, 30), 4.00),   # 09:30-?? 开盘宽松（间距加大4倍）
    ((10, 0), 3.00),   # 10:00 正常偏高
    ((11, 0), 2.00),   # 11:00 正常
    ((11, 30), 3.00),  # 11:30 偏高
    ((13, 0), 4.00),   # 13:00 下午开盘宽松
    ((13, 30), 3.00),  # 13:30 正常偏高
    ((14, 30), 2.00),  # 14:30 尾盘正常
    ((15, 0), 0.00),   # 15:00 收盘（不交易）
]

BASE_MULTIPLIER = 1.0  # 默认倍数（不在任何段内时使用）

# ==================== 向后兼容的别名（废弃） ====================
OPENING_MULTIPLIER = 1.75
CLOSING_MULTIPLIER = 0.70
MORNING_CLOSE_MULTIPLIER = 0.70
AFTERNOON_OPEN_MULTIPLIER = 1.75
OPENING_WINDOW_MIN = 30
CLOSING_WINDOW_MIN = 30

# ==================== 指标参数 ====================
ATR_PERIOD = 14           # ATR周期
BOLL_PERIOD = 20           # 布林带周期
BOLL_STD_MULT = 2          # 布林带标准差倍数

# ==================== 数据源 ====================
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN") or "d11513bc2e258334d01ddf0db02d45793325443dc1260931691d1552"

# 腾讯实时行情接口
TENGXUN_REALTIME_URL = "https://qt.gtimg.cn/q="

# ==================== 飞书通知配置 ====================
FEISHU_WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/b001cf93-2f59-4289-8240-567c276e4144"
FEISHU_SIGNING_KEY = "Bwo5lC6e8X1EnNP3eBPaDh"

# ==================== 交易时间段（北京时间） ====================
# 上午: 09:30 - 11:30
# 下午: 13:00 - 15:00
TRADING_MORNING_START = (9, 30)
TRADING_MORNING_END = (11, 30)
TRADING_AFTERNOON_START = (13, 0)
TRADING_AFTERNOON_END = (15, 0)

# ==================== 轮询配置 ====================
POLL_INTERVAL_SEC = 10    # 实时行情轮询间隔（秒），腾讯接口需控制频率

# ==================== 日志配置 ====================
LOG_LEVEL = "INFO"
LOG_FILE = "grid_trader.log"
