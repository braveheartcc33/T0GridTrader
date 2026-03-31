# T0GridTrader - A股 T+0 网格交易系统

> 文档版本：v7 | 最后更新：2026-03-31

---

## 1. 项目简介

T0GridTrader 是一个针对 **A股 T+0 交易规则** 设计的网格交易系统。

### 核心特性

- **网格交易**：价格划分档位，跌档买入、涨档卖出，高抛低吸
- **T+0 策略**：底仓先行，每日卖出额度不超过底仓数量，买入量亦不超过底仓数量（上限=底仓数量）
- **ATR 动态间距**：每格间距 = ATR(14) × `atr_spacing`，根据波动率自动调整
- **多股票支持**：一份配置同时运行多只股票，每只独立参数、独立状态文件
- **前复权数据**：均线/ATR 基于复权价计算，消除分红除权缺口干扰
- **飞书通知**：实时推送交易信号和盈亏报告

---

## 2. 快速启动

### 2.1 单股票模式

```bash
cd ~/.openclaw/workspace-simons-tiger/T0GridTrader
python3 main.py
```

### 2.2 多股票模式

在 `config.py` 的 `STOCKS` 列表中添加多只股票配置：

```bash
# 配置好多只股票后，直接运行
python3 main.py
```

系统会自动检测：
- `len(STOCKS) > 1` → 多股票模式（multi_engine.py）
- `len(STOCKS) == 1` → 单股票模式（grid_engine.py）

---

## 3. config.py 完整参数说明

### 3.1 每只股票参数（STOCKS 列表）

| 参数 | 类型 | 说明 | 示例值 |
|------|------|------|--------|
| `code` | str | 股票代码（带交易所后缀 .SZ / .SH） | `159567.SZ` |
| `name` | str | 股票名称（飞书通知显示用） | `港股创新药` |
| `base_shares` | int | **底仓股数**，系统启动前账户需先持有 | `100000` |
| `shares_per_grid` | int | **每格交易量**，每次触发网格买卖的股数 | `10000` |
| `grid_count` | int | 网格总档位数（两侧各一半，如 10 档 = -5 到 +5） | `10` |
| `atr_spacing` | float | **ATR 倍数**，每格间距 = ATR × 此倍数 | `4.0` |
| `stop_loss_enabled` | bool | 是否启用止损（建议关闭，网格本身有保护） | `False` |

### 3.2 多股票配置示例

```python
STOCKS = [
    {
        'code': '159567.SZ',
        'name': '港股创新药',
        'base_shares': 100000,      # 底仓 10 万股
        'shares_per_grid': 10000,   # 每格交易 1 万股
        'grid_count': 10,           # 10 档网格 (-5 ~ +5)
        'atr_spacing': 4.0,         # ATR × 4.0 = 每格间距
        'stop_loss_enabled': False,
    },
    {
        'code': '512880.SZ',
        'name': '证券ETF',
        'base_shares': 50000,       # 底仓 5 万股
        'shares_per_grid': 5000,    # 每格交易 5 千股
        'grid_count': 10,
        'atr_spacing': 3.5,         # 第二只股票用更小的 ATR 倍数
        'stop_loss_enabled': False,
    },
]
```

### 3.3 全局参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `ATR_PERIOD` | 14 | ATR 计算周期 |
| `BOLL_PERIOD` | 20 | 布林带周期 |
| `POLL_INTERVAL_SEC` | 10 | 价格轮询间隔（秒） |
| `GRID_SPACING_RULES` | 列表 | 分时段 ATR 倍数（可选） |

### 3.4 atr_spacing 取值建议

- **建议范围**：3.0 ~ 5.0
- 值越小：网格越密，交易次数多，单笔盈利小
- 值越大：网格越疏，交易次数少，单笔盈利大
- 高波动股票用大值（4.0+），低波动股票用小值（3.0 左右）

---

## 4. 网格原理

### 4.1 基准价与档位计算

- **基准价**：每日启动时取昨日收盘价（前复权价）
- **每格间距** = ATR(14) × `atr_spacing`
- **网格边界**：基准价 ± (grid_count/2) × 每格间距

### 4.2 间距公式

```
每格间距 = ATR(14) × atr_spacing
```

例如：
- 基准价：0.759
- ATR(14)：0.0269
- atr_spacing：4.0
- 每格间距：0.0269 × 4.0 = 0.1076

### 4.3 档位示意

```
档位  0  →  0.759（基准价）
档位+1  →  0.867（涨 0.1076，超出则卖出）
档位+2  →  0.974（再涨 0.1076，再卖出）
档位-1  →  0.651（跌 0.1076，超出则买入）
档位-2  →  0.544（再跌 0.1076，再买入）
```

### 4.4 交易规则

| 触发条件 | 操作 |
|----------|------|
| 价格从基准线上穿 1 档 | 卖出 `shares_per_grid` 股 |
| 价格从基准线下穿 1 档 | 买入 `shares_per_grid` 股 |
| 价格超出网格上限（+5 档） | 停止交易，等待尾盘 |
| 价格超出网格下限（-5 档） | 停止交易，等待尾盘 |
| 14:30 尾盘 | 强制平仓，回到昨日收盘持仓 |

### 4.5 T+0 规则

- 每日卖出额度上限 = 底仓股数（`base_shares`）
- 累计卖出不超过底仓，买入量不超过昨日持仓
- 14:30 尾盘强制平仓，回到昨日收盘持仓

---

## 5. T+0 盈亏计算

```
今日总盈亏 = T0盈亏 + 持仓盈亏

T0盈亏 = Σ(卖出成交额) - Σ(买入成交额) - 净空头市值变化
持仓盈亏 = (当前价 - 基准价) × 昨日收盘持仓
```

系统同时追踪：
- **已实现盈亏（realized_pnl）**：T+0 买卖成交产生的盈亏
- **浮动盈亏（float_pnl）**：持仓随价格波动的未实现盈亏
- **总盈亏 = 已实现 + 浮动**

---

## 6. 多周期回测

### 6.1 运行回测

```bash
python3 backtest_grid.py
```

回测默认参数（可在脚本中修改）：
- 股票代码：`159567.SZ`
- 初始持仓：100000 股
- 每格交易量：10000 股
- 回测期间：2026-03-24 ~ 2026-03-31

### 6.2 回测周期

支持多种时间周期：
- `snapshot`：快照（逐笔）
- `1min`、`5min`、`15min`：分钟级
- `daily`：日线

### 6.3 报告示例

```
================================================================================
# 网格交易回测报告

- **股票**: 159567.SZ
- **回测期间**: 20260324-20260331

================================================================================

**无有效回测结果**
```

---

## 7. 状态文件

### 7.1 per-stock 状态文件

每只股票有独立的状态文件：`state_{code}.json`

例如：
- 159567.SZ → `state_159567.SZ.json`
- 512880.SZ → `state_512880.SZ.json`

### 7.2 文件格式

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

### 7.3 字段说明

| 字段 | 说明 |
|------|------|
| `base_price` | 基准价（昨日收盘/开盘价） |
| `base_position` | 初始底仓股数 |
| `current_position` | 当前持仓股数 |
| `position_cost` | 持仓成本价 |
| `today_realized_pnl` | 今日 T+0 已实现盈亏 |
| `today_position_pnl` | 今日持仓盈亏 |
| `today_total_pnl` | 今日总盈亏 |
| `enabled` | 是否允许交易（每日开盘前自动开启） |

### 7.4 手动控制

```bash
# 开启网格（允许交易）
python3 -c "import json; s=json.load(open('state_159567.SZ.json')); s['enabled']=True; json.dump(s,open('state_159567.SZ.json','w'),indent=2)"

# 关闭网格（停止交易）
python3 -c "import json; s=json.load(open('state_159567.SZ.json')); s['enabled']=False; json.dump(s,open('state_159567.SZ.json','w'),indent=2)"
```

---

## 8. 文件结构

```
T0GridTrader/
├── main.py                  # 主入口（自动识别单/多股票模式）
├── config.py                # 参数配置（STOCKS 列表）
├── multi_engine.py          # 多股票管理器
│   ├── GridTraderUnit       # 单只股票网格单元
│   └── GridTraderMulti      # 多只股票管理器
├── grid_engine.py           # 网格引擎核心（单股票模式）
├── market_data.py           # 市场数据获取（前复权 + ATR/布林带）
├── notifier.py              # 飞书通知
├── trade_logger.py          # 交易记录
├── indicators.py            # 技术指标计算
├── backtest_grid.py         # 多周期回测模块
│   ├── GridBacktester       # 单周期回测引擎
│   └── MultiTimeframeBacktester  # 多周期管理器
├── state_{code}.json        # 运行状态（每只股票独立）
└── grid_trader.log          # 运行日志
```

---

## 9. 今日修复的 3 个 Bug

| 编号 | 描述 | 修复内容 |
|------|------|----------|
| Bug 1 | trade_logger.py 硬编码 `state.json`，无法支持多股票独立状态 | `TradeLogger.__init__` 增加 `stock_code` 参数，状态文件改为 `state_{code}.json` |
| Bug 2 | GridNotifier 不接受 stock_code/stock_name 参数，多股票通知时股票信息错误 | `GridNotifier.__init__` 增加 `stock_code/stock_name` 参数，各方法改用实例属性 |
| Bug 3 | GridEngine 初始化缺少 atr_spacing 参数，多股票模式下间距计算错误 | 在 `multi_engine.py` 中增加 `atr_spacing=self.config.get('atr_spacing', 4.0)` |

---

## 10. 更新记录

| 日期 | 版本 | 更新内容 |
|------|------|----------|
| 2026-03-31 | v7 | 完整重写 README，按当前代码逻辑 |
| 2026-03-31 | v6.1 | 修复多股票状态文件、通知器参数、atr_spacing 传递 3 个 bug |
| 2026-03-31 | v6 | 多股票参数化 + multi_engine + 多周期回测 + 前复权修复 |
| 2026-03-26 | v5 | 去除止损逻辑，网格自保护 |
| 2026-03-24 | v4 | T+0 双因子盈亏计算 |
| 2026-03-20 | v3 | 飞书通知 + 状态汇报 |

---

*本系统仅供学习研究，不构成投资建议。*

---

## 系统架构

### 程序入口

```
python3 main.py          ← 唯一起入口
```

`main.py` 自动判断：
- `MULTI_STOCK_MODE = True` → `main_multi()` 多股票模式
- `MULTI_STOCK_MODE = False` → `main_single()` 单股票模式

---

### 执行流程图

```
main.py
│
├── main_single() / main_multi()
│   │
│   ├── GridTraderApp.initialize()
│   │   │
│   │   ├── MarketDataManager.initialize()
│   │   │   └── tushare API → 前复权数据
│   │   │       ├── ATR(14)
│   │   │       └── 布林带(20)
│   │   │
│   │   └── GridEngine(base_price, atr14, atr_spacing, ...)
│   │       └── 读取 state_{code}.json 恢复持仓
│   │
│   └── GridTraderApp.run()  ← 主循环（轮询价格）
│       │
│       └── while True:
│           ├── fetch_current_price()         ← 腾讯财经API
│           │
│           ├── check_grid_signals(price)     ← 【核心条件判断】
│           │   │
│           │   ├── GridEngine.can_buy()      ← 能否买入
│           │   │   ├── ① 价格在布林下轨附近？
│           │   │   ├── ② 今日累计买入量 ≤ base_position？
│           │   │   └── ③ 买卖价差 ≥ base_spacing？
│           │   │
│           │   ├── GridEngine.can_sell()     ← 能否卖出
│           │   │   ├── ① 价格在布林上轨附近？
│           │   │   ├── ② 今日累计卖出量 ≤ base_position？
│           │   │   └── ③ 买卖价差 ≥ base_spacing？
│           │   │
│           │   └── GridEngine.evaluate_position() ← 持仓盈亏计算
│           │
│           ├── send_trade_signal()          ← 飞书推送
│           │
│           └── sleep(POLL_INTERVAL_SEC)       ← 等待下次轮询
│
└── 每日收盘时
    └── GridEngine.reset_day()             ← 重置买入/卖出计数
```

---

### 核心条件判断函数详解

#### GridEngine.can_buy(price) — 能否买入

| 条件 | 说明 |
|------|------|
| 价格触及下轨 | 买入档位触发 |
| 今日买入量 ≤ 底仓 | 防止超买 |
| 买卖价差 ≥ 间距 | 防止网格内对倒 |

#### GridEngine.can_sell(price) — 能否卖出

| 条件 | 说明 |
|------|------|
| 价格触及上轨 | 卖出档位触发 |
| 今日卖出量 ≤ 底仓 | 防止超卖 |
| 买卖价差 ≥ 间距 | 防止网格内对倒 |

---

### 模块职责

| 文件 | 职责 |
|------|------|
| `main.py` | 主入口 + 主循环调度 |
| `config.py` | 参数配置（STOCKS 列表） |
| `grid_engine.py` | 网格核心算法 + 条件判断 |
| `market_data.py` | 数据获取（前复权 + ATR + 布林带） |
| `notifier.py` | 飞书通知推送 |
| `trade_logger.py` | 交易记录 + state 文件读写 |
| `backtest_grid.py` | 多周期回测 |
| `state_{code}.json` | 每日运行状态 |

---

### T+0 规则（三重保护）

```
第①重：价格条件
  买 → 价格 ≤ 布林下轨 + 档位修正
  卖 → 价格 ≥ 布林上轨 + 档位修正

第②重：T+0 数量限制
  每日累计买入量 ≤ base_position（昨仓）
  每日累计卖出量 ≤ base_position（昨仓）

第③重：买卖价差保护
  买完再卖：本次买价 - 上次卖价 ≥ 每格间距
  卖完再买：本次卖价 - 上次买价 ≥ 每格间距
```

---

## 系统架构

### 程序入口

```bash
python3 main.py
```

`main.py` 自动判断运行模式：
- `MULTI_STOCK_MODE = False` → `main_single()` 单股票模式
- `MULTI_STOCK_MODE = True` → `main_multi()` 多股票模式

---

### 单股票模式执行流程

```
main_single()
│
├── GridTraderApp.initialize()
│   │
│   ├── MarketDataManager.initialize()
│   │   └── tushare API
│   │       ├── 前复权日线数据（计算ATR、布林带）
│   │       ├── ATR(14) → 用于计算网格间距 = ATR × atr_spacing
│   │       └── 布林带(20) → 用于显示参考
│   │
│   ├── GridEngine(base_price, atr14, atr_spacing, ...)
│   │   ├── base_price = 昨日收盘（前复权）
│   │   ├── base_spacing = ATR × atr_spacing
│   │   ├── grid_count 档（两侧对称，如10档 = -5到+5）
│   │   └── base_position = 初始底仓股数
│   │
│   └── GridNotifier()
│
└── GridTraderApp.run()
    │
    └── while True:
        ├── fetch_current_price()        ← 腾讯财经实时API
        │
        ├── check_grid_signals(price)    ← 【核心判断】
        │
        └── sleep(POLL_INTERVAL_SEC)    ← 等待下次轮询
```

---

### 网格档位原理（核心）

网格将价格分成若干档，每档间距 = `base_spacing`：

```
档位 -5    档位 -4    档位 -3    档位 -2    档位 -1    档位 0(基准)    档位 +1    档位 +2    ...
价格 0.485    0.593     0.700     0.808     0.915     1.022(基准)    1.129    1.236
                                                         ↑
                                                      基准价
```

基准价 = 昨收，间距 = ATR × atr_spacing（如 0.0269 × 4.0 = 0.1076）

---

### 核心条件判断（grid_engine.py）

#### can_buy(price) — 能否买入

| 条件 | 说明 |
|------|------|
| 价格跌破一档 | 当前价 < 上一档价格（价格下跌一格） |
| 今日累计买入量 ≤ 昨仓 | T+0 数量限制 |
| 买价 - 上次卖价 ≥ 间距 | 买卖价差保护 |

#### can_sell(price) — 能否卖出

| 条件 | 说明 |
|------|------|
| 价格涨超一档 | 当前价 > 下一档价格（价格上涨一格） |
| 今日累计卖出量 ≤ 昨仓 | T+0 数量限制 |
| 上次买价 - 卖价 ≥ 间距 | 买卖价差保护 |

---

### 模块职责

| 文件 | 职责 |
|------|------|
| `main.py` | 主入口 + 主循环调度 |
| `config.py` | 参数配置（STOCKS 列表） |
| `grid_engine.py` | 网格核心算法 + 条件判断 |
| `market_data.py` | tushare 数据（前复权 + ATR + 布林） |
| `notifier.py` | 飞书通知推送 |
| `trade_logger.py` | 交易记录 + 状态文件 |
| `backtest_grid.py` | 多周期回测 |
| `multi_engine.py` | 多股票管理器 |
