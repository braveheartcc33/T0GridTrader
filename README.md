# T0GridTrader - A股 T+0 网格交易系统

> 文档版本：v7 | 最后更新：2026-03-31

---

## 1. 项目简介

T0GridTrader 是一个针对 **A股 T+0 交易规则** 设计的网格交易系统。

### 核心特性

- **网格交易**：价格划分档位，跌档买入、涨档卖出，高抛低吸
- **T+0 策略**：底仓先行，当日可无限次买卖（每日卖出额度不超过底仓数量）
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
- 累计卖出不超过底仓，可无限买入
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
