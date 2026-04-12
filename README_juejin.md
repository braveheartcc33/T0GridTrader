# 掘金量化 T0 网格回测框架

基于掘金量化终端1分钟数据的A股T+0网格交易回测系统。

## 快速开始

```bash
# 默认回测（513720，10档，动态波动率）
python3 backtest_juejin.py

# 指定参数
python3 backtest_juejin.py --symbol SHSE.513720 --grid-count 10 --default-vol 0.06

# 输出CSV
python3 backtest_juejin.py --out-csv ./result.csv
```

## 核心参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--symbol` | SHSE.513720 | 掘金标的代码 |
| `--csv` | .../513720_1m_2026.csv | 1分钟数据文件路径 |
| `--grid-count` | 10 | 网格档位数 |
| `--default-vol` | 0.06 | 默认波动率（首日无历史数据时用）|
| `--base-shares` | 100000 | 底仓股数（10万股）|

## 规则说明

### 网格仓位
- 每格1万股，10档共10万股底仓
- 向上最多卖5格（5万股），向下最多买5格（5万股）
- 14:30尾盘强制平仓补回底仓

### T+0 计算
```
T+0收益 = 卖出总额 - 买入总额 - 手续费
```

## 数据准备

需要在 Windows (192.168.50.77:5678) 运行掘金转发服务：

```bash
python juEhua_proxy.py
```

拉取数据后保存为CSV格式（time,open,high,low,close,volume）。

## 输出文件

- 回测结果CSV：`backtest_513720_dynvol_gc10.csv`
- 字段：date, close, base_price, hv_display, spacing, base_pos, final_pos, buy_vol, sell_vol, trade_count, t0_realized, hold_pnl, total_pnl

## 文档

- [技术规格](docs/backtest_spec.md) - 详细算法和规则说明
