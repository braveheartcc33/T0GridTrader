# T0 Grid Trader - A股网格交易系统

> 基于 ATR 动态间距的 A 股 T+0 网格交易策略，支持多股票参数化配置 + 多周期回测

## 功能特点

- **多股票支持**：通过 `STOCKS` 参数列表同时管理多只股票，每只股票独立持仓、网格参数、状态文件
- **前复权数据**：均线/ATR 基于前复权价格计算，避免分红除权缺口破坏趋势
- **ATR 动态间距**：网格间距根据 ATR 倍数自动计算
- **多周期回测**：支持日线、snapshot（09:35/09:45/09:55）、自定义分钟数据回测
- **飞书推送**：实时交易信号推送

## 快速开始

### 1. 单股票模式

编辑 `config.py`，填入股票代码和参数：

```python
STOCK_CODE = "159567.SZ"
STOCK_NAME = "港股创新药"
INITIAL_BASE_SHARES = 100000  # 初始底仓
SHARES_PER_GRID = 10000       # 每格交易量
GRID_COUNT = 10               # 网格档位
MULTI_STOCK_MODE = False
```

运行：
```bash
python3 main.py
```

### 2. 多股票模式

在 `config.py` 中设置 `MULTI_STOCK_MODE = True`，并配置 `STOCKS` 列表：

```python
MULTI_STOCK_MODE = True

STOCKS = [
    {
        'code': '159567.SZ',
        'name': '港股创新药ETF',
        'base_shares': 100000,       # 初始底仓
        'shares_per_grid': 10000,     # 每格交易量
        'grid_count': 10,            # 网格档位
        'atr_spacing': 4.0,          # ATR倍数
        'stop_loss_enabled': False,
    },
    {
        'code': '512480.SH',
        'name': '半导体ETF',
        'base_shares': 50000,
        'shares_per_grid': 5000,
        'grid_count': 10,
        'atr_spacing': 3.5,
        'stop_loss_enabled': False,
    },
]
```

运行多股票模式：
```bash
python3 main.py  # 自动检测 MULTI_STOCK_MODE
```

每只股票有独立状态文件：`state_{code}.json`

## 核心参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `INITIAL_BASE_SHARES` | 初始底仓股数 | 100000 |
| `SHARES_PER_GRID` | 每格交易量（股） | 10000 |
| `GRID_COUNT` | 网格档位数 | 10 |
| `atr_spacing` | ATR倍数（每格 = ATR × 此倍数） | 4.0 |
| `stop_loss_enabled` | 是否启用止损 | False |
| `STATE_FILE_PREFIX` | 状态文件前缀 | state |
| `STATE_FILE_DIR` | 状态文件目录 | .（当前目录）|

## 多周期回测

### 使用说明

```python
from backtest_grid import MultiTimeframeBacktester
import pandas as pd

# 加载数据（邢不行格式）
# df 包含 datetime, close 列（或 open/high/low/vol）
df = pd.read_csv('your_stock_data.csv')

# 创建回测器
backtester = MultiTimeframeBacktester(
    stock_code='159567.SZ',
    initial_position=100000,
    shares_per_trade=10000,
    atr_period=14,
)

# 运行回测（指定日期范围）
results = backtester.run(
    df=df,
    start_date='20260301',
    end_date='20260331',
    timeframes=['snapshot', '1min', '5min', '15min', 'daily']
)

# 打印报告
backtester.print_report(results)
```

### DataFrame 输入格式

```python
df = pd.DataFrame({
    'datetime': ['2026-03-01 09:35:00', '2026-03-01 09:36:00', ...],
    'close': [0.752, 0.753, ...],
    # 可选：'open', 'high', 'low', 'vol'
})
```

### 回测报告示例

```
================================================================================
# 网格交易回测报告

- **股票**: 159567.SZ
- **回测期间**: 20260324-20260331
- **生成时间**: 2026-03-31 13:24:40

================================================================================

## snapshot周期

| 参数 | 交易次数 | 买入 | 卖出 | 实现盈亏 | 浮动盈亏 | 总盈亏 | 胜率 |
|------|----------|------|------|----------|----------|--------|------|
| 10档-3倍ATR | 1 | 0 | 1 | +310.00 | +2790.00 | +3100.00 | 100.0% |
| 10档-4倍ATR | 1 | 0 | 1 | +460.00 | +2790.00 | +3250.00 | 100.0% |
| 10档-5倍ATR | 1 | 0 | 1 | +460.00 | +2790.00 | +3250.00 | 100.0% |
| 15档-4倍ATR | 1 | 0 | 1 | +206.65 | +2893.35 | +3100.00 | 100.0% |
| 20档-3倍ATR | 3 | 0 | 3 | +590.00 | +2480.00 | +3070.00 | 100.0% |

================================================================================

**最优参数**: snapshot - 10档-4倍ATR, **总盈亏**: +3250.00
```

## 文件结构

```
grid_trader/
├── main.py              # 主入口（自动识别单/多股票模式）
├── config.py            # 参数配置（单股票 + 多股票列表）
├── multi_engine.py       # 多股票管理器
├── grid_engine.py        # 网格引擎核心
├── market_data.py        # 市场数据（前复权 + ATR/布林带）
├── notifier.py           # 飞书通知
├── trade_logger.py       # 交易记录
├── indicators.py         # 技术指标
├── backtest_grid.py      # 多周期回测模块
└── state_{code}.json    # 运行状态文件（每只股票独立）
```

## 数据源

- **实时行情**：腾讯财经接口（实时价格、分钟数据）
- **历史数据**：tushare fund_daily API
- **回测数据**：邢不行格式日线数据（`~/.openclaw/stock-trading-data-pro/`）
- **ATR/布林带**：基于前复权价格计算

## 状态文件

每只股票独立状态文件，格式：`state_{code}.json`

```json
{
  "engine": {
    "base_price": 0.759,
    "base_position": 100000,
    "current_position": 100000,
    "position_cost": 0.759,
    "today_realized_pnl": 0.0,
    "today_position_pnl": 0.0,
    "today_total_pnl": 0.0
  },
  "date": "20260331",
  "enabled": true,
  "saved_at": "2026-03-31 13:25:00"
}
```

## 飞书通知

交易信号、状态汇报、异常告警实时推送至飞书群。

---
*本项目仅供学习研究，不构成投资建议。*
