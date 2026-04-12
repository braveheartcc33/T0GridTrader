# 掘金量化 T0 网格回测框架 - 技术规格

## 概述

本框架基于掘金量化终端的1分钟数据，对A股ETF（如513720纳斯达克ETF、513120标普ETF）进行T+0网格交易策略回测。

## 架构

```
Windows (192.168.50.77:5678)
    └─ juEhua_proxy.py (FastAPI)
         ├─ GET  /ping
         ├─ GET  /batch_current
         ├─ POST /set_token
         └─ POST /history
              ↓
WSL/Linux
    └─ backtest_juejin.py
         ├─ GridSim 网格引擎
         ├─ build_daily_vol() 波动率计算
         └─ run_backtest() 回测主流程
```

## GridSim 核心逻辑

### 初始化参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `grid_count` | int | 网格档位数，默认10 |
| `default_vol_pct` | float | 默认波动率，默认0.06（6%）|
| `base_shares` | int | 底仓股数，默认100000（10万股）|
| `fee_rate` | float | 单边手续费率，默认0.0003（万三）|

### 每日状态变量

| 变量 | 说明 |
|------|------|
| `base_price` | 基准价 = 昨日收盘价 |
| `spacing` | 每格间距 = base_price × 波动率 / grid_count |
| `pos` | 当前持仓（每日开盘=base_shares，收盘前必须补回）|
| `cost` | 持仓成本（用于计算已实现盈亏参考值）|
| `day_buy_vol / day_sell_vol` | 当日累计买卖股数 |
| `day_buy_amt / day_sell_amt` | 当日累计买卖金额 |
| `day_total_fee` | 当日手续费合计 |
| `last_exec_price` | 上次成交价（用于判断是否够一格触发交易）|

## 网格规则

### 仓位限制（核心！）

- **每格股数** = base_shares // grid_count = 10万 // 10 = **1万股/格**
- **向上最大5格**（卖出）：最多卖 5 × 1万 = **5万股**，持仓最低5万股
- **向下最大5格**（买入）：最多买 5 × 1万 = **5万股**，持仓最高15万股

```python
def _can_buy(self, vol):
    return self.day_buy_vol + vol <= self.base_shares // 2  # 5万股上限

def _can_sell(self, vol):
    return self.day_sell_vol + vol <= self.base_shares // 2  # 5万股上限
```

### lv 计算与格限制

```python
lv = int((price - self.base_price) / self.spacing)  # int截断，非round
max_grid = self.grid_count // 2  # 5
lv = max(-max_grid, min(max_grid, lv))  # 限制在[-5, +5]之间
target_pos = self.base_shares - lv * spg  # 目标持仓
```

### 交易触发条件（三重检查）

1. `lv` 发生变化（价格移动跨越了一格）
2. `abs(price - self.last_exec_price) >= self.spacing`（距上次成交够一整格）
3. `_can_buy` / `_can_sell` 通过仓位上限检查

### 尾盘平仓

14:30 之后，`pos != base_shares` 时强制执行平仓，补回至底仓10万股。

## T+0 收益计算（资金流算法）

```python
T+0收益 = 当日卖出总额 - 当日买入总额 - 手续费合计
        = day_sell_amt - day_buy_amt - day_total_fee
```

**与"已实现盈亏"算法的区别**：

| 算法 | 公式 | 含义 |
|------|------|------|
| 已实现盈亏 | Σ(卖出价 - 持仓成本) × 股数 - 手续费 | 每次卖出按持仓成本计算利润 |
| 资金流（正确） | 卖出收到 - 买入付出 - 手续费 | 实际现金收付 |

**资金流算法的优势**：
- 不受持仓成本影响
- 直接反映当日买卖现金结果
- 更符合实盘理解："今天卖了多少钱，又买了多少钱"

### 示例：1月12日资金流

```
卖出（向上5格）：
  1.006 × 1万股 = 10,060元
  1.014 × 1万股 = 10,140元
  1.020 × 1万股 = 10,200元
  1.030 × 1万股 = 10,300元
  1.037 × 1万股 = 10,370元
  卖出合计 = 51,130元

收盘前买回（5万股 @ 1.047）：
  买入合计 = 52,350元

T+0 = 51,130 - 52,350 - 手续费 ≈ -1,381元（亏损）
```

## 动态波动率

```python
def build_daily_vol(df_1m, window=3, default_vol=0.06):
    daily = df_1m.groupby('date').agg(close=('close', 'last'))
    daily['log_ret'] = np.log(daily['close'] / daily['close'].shift(1))
    daily['hv'] = daily['log_ret'].rolling(window).std() * np.sqrt(252)
    daily['hv'] = daily['hv'].fillna(default_vol)
    return daily
```

- 每日根据前3个交易日日对数收益率标准差 × √252 计算年化波动率
- 首日无历史数据时使用 `default_vol = 6%`
- 波动率大 → 间距宽 → 不容易触发交易
- 波动率小 → 间距窄 → 频繁交易

## 回测结果（513720，2026-01-09 ~ 04-10）

| 参数 | 值 |
|------|-----|
| 标的 | SHSE.513720（广发纳斯达克ETF）|
| 档位 | 10 |
| 波动率 | 动态（前3日已实现，年化）|
| 底仓 | 10万股 |
| 手续费 | 单边万三 |

### 汇总

- **T+0收益**：+699元
- **持仓不动**：-20,800元
- **实际账户亏损**：20,800 - 699 = 20,101元（股票从1.000跌至0.792）
- **胜率**：28.8%（17/59天盈利）
- **总交易笔数**：55笔（日均0.9笔）
- **估算手续费**：+201元

### 结论

- 单边下跌行情中（513720从1.000→0.792，跌21%），T+0网格策略整体亏损699元
- 但相比傻持亏损20,800元，T+0帮用户减少了约96.6%的损失
- 波动率极大时（30%~60%），格间距过宽导致很少触发交易（大量0交易天数）
- 资金流T+0算法真实反映每日现金收付，比"已实现盈亏"更符合实盘认知

## 文件清单

| 文件 | 说明 |
|------|------|
| `backtest_juejin.py` | 回测主框架（含GridSim+波动率+回测入口）|
| `README_juejin.md` | 快速使用指南 |
| `docs/backtest_spec.md` | 本技术规格文档 |
