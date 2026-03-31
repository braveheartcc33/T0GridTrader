# 网格交易系统 - 操作手册

> 文档版本：v6 | 最后更新：2026-03-31

---

## 1. 系统概述

网格交易是一种 **高抛低吸** 的震荡策略。把价格分成若干档位，跌到某档就买、涨到某档就卖，循环反复。

本系统特点：
- **前复权数据**：均线/ATR 基于复权价计算，消除分红除权缺口干扰
- **ATR 动态间距**：每格间距 = ATR × 倍数（由每只股票单独配置）
- **T+0 支持**：底仓先行，当日可无限次买卖
- **多股票支持**：一份配置同时跑多只股票，每只独立参数、独立状态

---

## 2. 快速启动

```bash
cd ~/.openclaw/workspace/FlashNewsTrade/grid_trader
python3 main.py
```

运行后会自动检测：
- `MULTI_STOCK_MODE = True` → 多股票模式
- `MULTI_STOCK_MODE = False` → 单股票模式

---

## 3. 配置说明

### 3.1 多股票参数列表

编辑 `config.py` 中的 `STOCKS` 列表：

```python
STOCKS = [
    {
        'code': '159567.SZ',         # 股票代码
        'name': '港股创新药',          # 名称（随意）
        'base_shares': 100000,        # 初始底仓股数
        'shares_per_grid': 10000,     # 每格买卖的交易量（股）
        'grid_count': 10,             # 网格档位数（两侧对称）
        'atr_spacing': 4.0,          # ATR倍数（每格 = ATR × 4.0）
        'stop_loss_enabled': False,   # 是否启用止损
    },
    {
        'code': '512480.SH',          # 第二只股票
        'name': '半导体ETF',
        'base_shares': 50000,
        'shares_per_grid': 5000,
        'grid_count': 10,
        'atr_spacing': 3.5,          # 第二只股票用不同的ATR倍数
        'stop_loss_enabled': False,
    },
]
```

**关键参数说明：**

| 参数 | 说明 | 示例值 |
|------|------|--------|
| `code` | 股票代码（带交易所后缀 .SZ / .SH） | `159567.SZ` |
| `base_shares` | 初始底仓，启动前账户需先持有这些股票 | `100000` |
| `shares_per_grid` | 每格触发时买卖的股数 | `10000` |
| `grid_count` | 网格总档位数（两侧各一半，如 10 档 = -5 到 +5） | `10` |
| `atr_spacing` | ATR 倍数，每格间距 = ATR(14) × 此倍数 | `4.0` |
| `stop_loss_enabled` | 是否启用固定止损 | `False` |

**建议 `atr_spacing` 取值范围：3.0 ~ 5.0**
- 值越小：网格越密，交易次数越多，适合高波动股票
- 值越大：网格越疏，交易次数越少，适合低波动股票

### 3.2 全局参数

```python
ATR_PERIOD = 14        # ATR 计算周期
BOLL_PERIOD = 20        # 布林带周期
POLL_INTERVAL_SEC = 10  # 价格轮询间隔（秒）
```

### 3.3 时段间距规则（可选高级配置）

`GRID_SPACING_RULES` 控制不同交易时段的 ATR 倍数：

```python
GRID_SPACING_RULES = [
    ((9, 30), 4.00),   # 开盘宽松
    ((10, 0), 3.00),    # 正常
    ((11, 0), 2.00),    # 收紧
    ((11, 30), 3.00),   # 偏宽
    ((13, 0), 4.00),    # 下午开盘宽松
    ((13, 30), 3.00),   # 正常
    ((14, 30), 2.00),   # 尾盘收紧
    ((15, 0), 0.00),    # 收盘不交易
]
```

此为全局配置，所有股票共用同一套时段规则。如需完全不同的时段策略，可在 `STOCKS` 中单独扩展。

---

## 4. 状态文件

每只股票有独立状态文件：`state_{code}.json`

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
  "saved_at": "2026-03-31 09:25:00"
}
```

**字段说明：**

| 字段 | 说明 |
|------|------|
| `base_price` | 基准价（昨日收盘 / 开盘价） |
| `base_position` | 初始底仓股数 |
| `current_position` | 当前持仓股数 |
| `today_realized_pnl` | 今日T+0已实现盈亏 |
| `today_position_pnl` | 今日持仓盈亏（以基准价为基准） |
| `today_total_pnl` | 今日总盈亏 |
| `enabled` | 是否允许交易（每日开盘前自动开启） |

**手动控制：**
```bash
# 开通网格（允许交易）
python3 -c "import json; s=json.load(open('state_159567.SZ.json')); s['enabled']=True; json.dump(s,open('state_159567.SZ.json','w'),indent=2)"

# 关闭网格（停止交易）
python3 -c "import json; s=json.load(open('state_159567.SZ.json')); s['enabled']=False; json.dump(s,open('state_159567.SZ.json','w'),indent=2)"
```

---

## 5. 网格原理

### 5.1 基准价与档位

- **基准价**：每日启动时取昨日收盘价（前复权）
- **每格间距** = ATR(14) × `atr_spacing`
- **网格边界**：基准价 ± grid_count/2 × 每格间距

以 159567 为例（ATR=0.0269，atr_spacing=4.0）：

```
基准价: 0.759
每格间距: 0.0269 × 4.0 = 0.1076

档位 0  → 0.759（基准）
档位+1  → 0.767（涨0.1076，超出则卖）
档位+2  → 0.874（再涨0.1076，再卖）
档位-1  → 0.751（跌0.1076，超出则买）
档位-2  → 0.644（再跌0.1076，再买）
```

### 5.2 交易规则

| 方向 | 触发条件 | 操作 |
|------|----------|------|
| 卖出 | 价格从基准线上穿一档 | 减仓 `shares_per_grid` |
| 买入 | 价格从基准线下穿一档 | 加仓 `shares_per_grid` |
| 止损 | 跌破基准价超过阈值（可选） | 全部清仓 |

### 5.3 T+0 盈亏计算

```
今日总盈亏 = T0盈亏 + 持仓盈亏

T0盈亏 = Σ(卖出金额) - Σ(买入金额) - 净空头市值变化
持仓盈亏 = (当前价 - 基准价) × 昨日收盘持仓
```

---

## 6. 多周期回测

### 6.1 数据格式

用户传入 DataFrame：

```python
import pandas as pd
df = pd.DataFrame({
    'datetime': ['2026-03-01 09:30:00', ...],  # 时间戳
    'close': [0.752, ...],                       # 收盘价（必须）
    'open': [0.751, ...],                        # 可选
    'high': [0.755, ...],                        # 可选
    'low': [0.750, ...],                         # 可选
    'vol': [100000, ...],                        # 可选
})
```

**最低要求**：`datetime` + `close`

### 6.2 运行回测

```python
import sys
sys.path.insert(0, '.')
from backtest_grid import MultiTimeframeBacktester

backtester = MultiTimeframeBacktester(
    stock_code='159567.SZ',
    initial_position=100000,     # 初始持仓
    shares_per_trade=10000,      # 每格交易量
    atr_period=14,
)

results = backtester.run(
    df=df,
    start_date='20260301',
    end_date='20260331',
    timeframes=['snapshot', '1min', '5min', '15min', 'daily']
)

backtester.print_report(results)
```

### 6.3 回测报告示例

```
================================================================================
# 网格交易回测报告

- **股票**: 159567.SZ
- **回测期间**: 20260301-20260331
- **生成时间**: 2026-03-31 13:24

================================================================================

## snapshot周期

| 参数 | 交易次数 | 实现盈亏 | 浮动盈亏 | 总盈亏 | 胜率 |
|------|----------|----------|----------|--------|------|
| 10档-3倍ATR | 3 | +930 | +2790 | +3720 | 100% |
| 10档-4倍ATR | 2 | +920 | +2790 | +3710 | 100% |
| 10档-5倍ATR | 1 | +460 | +2790 | +3250 | 100% |
| 15档-4倍ATR | 4 | +826 | +2893 | +3719 | 100% |

**最优参数**: snapshot - 10档-4倍ATR, 总盈亏: +3710
```

### 6.4 ATR 参数敏感性

回测会自动对比不同 ATR 倍数（3.0 / 3.5 / 4.0 / 4.5 / 5.0）的表现，帮每只股票找到最优间距参数。

---

## 7. 文件结构

```
grid_trader/
├── main.py                  # 主入口（自动识别单/多股票模式）
├── config.py                # 参数配置（STOCKS 列表）
├── multi_engine.py          # 多股票管理器
│   ├── GridTraderUnit       # 单只股票网格单元
│   └── GridTraderMulti      # 多只股票管理器
├── grid_engine.py           # 网格引擎核心
├── market_data.py           # 市场数据（前复权 + ATR/布林带）
├── notifier.py              # 飞书通知
├── trade_logger.py          # 交易记录
├── indicators.py            # 技术指标
├── backtest_grid.py         # 多周期回测模块
│   ├── GridBacktester       # 单周期回测引擎
│   └── MultiTimeframeBacktester  # 多周期管理器
└── state_{code}.json       # 运行状态（每只股票独立）
```

---

## 8. 常见问题

**Q: 收盘后状态文件怎么清理？**
A: 每日开盘前 `enabled` 会自动设为 `True`，持仓归零重新开始。不需要手动清理。

**Q: 开盘前怎么预热基准价？**
A: 系统启动时自动从 tushare 读取昨日收盘价（前复权）作为基准价。

**Q: 多股票模式下一只股票报错会影响其他吗？**
A: 不会。每只股票运行在独立线程/进程，单只异常不影响整体。

**Q: 可以回测自定义的分钟数据吗？**
A: 可以。传入包含 `datetime` + `close` 的 DataFrame 即可，系统按 `datetime` 自动分日处理。

---

## 9. 更新记录

| 日期 | 版本 | 更新内容 |
|------|------|---------|
| 2026-03-31 | v6 | 多股票参数化 + multi_engine + 多周期回测 + 前复权修复 |
| 2026-03-26 | v5 | 去除止损逻辑，网格自保护 |
| 2026-03-24 | v4 | T+0双因子盈亏计算 |
| 2026-03-20 | v3 | 飞书通知 + 状态汇报 |

---

*本系统仅供学习研究，不构成投资建议。*
