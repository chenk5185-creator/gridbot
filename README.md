# GridBot - 网格交易工具

基于 StandX 的网格交易机器人，支持 MetaMask 钱包连接。

## 功能特性

- 🔐 **安全认证**: MetaMask 钱包签名，私钥不离开浏览器
- 📊 **三种网格模式**: 中性网格、做多网格、做空网格
- ⚡ **实时行情**: WebSocket 推送实时价格
- 📈 **盈亏统计**: 自动计算已实现和未实现盈亏
- 💾 **数据持久化**: SQLite 存储网格配置和交易历史

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动服务器

```bash
python api_server.py
```

或使用 uvicorn:

```bash
uvicorn api_server:app --reload --port 8000
```

### 3. 访问界面

打开浏览器访问: http://localhost:8000

## 项目结构

```
gridbot/
├── exchanges/              # 交易所适配器
│   ├── __init__.py        # 模块初始化
│   ├── base.py            # 基类定义
│   └── standx.py          # StandX 实现
├── static/                 # 静态资源
│   ├── css/grid.css       # 样式
│   └── js/grid.js         # 前端逻辑
├── templates/              # HTML 模板
│   └── grid.html          # 主界面
├── api_server.py          # API 服务器
├── database.py            # 数据库模型
├── grid_engine.py         # 网格交易引擎
├── wallet_connector.py    # 钱包连接管理
└── requirements.txt       # 依赖列表
```

## API 文档

启动服务器后访问: http://localhost:8000/docs

## 网格交易原理

1. 设定价格区间 [下限, 上限]
2. 将区间划分为 N 个网格
3. 在每个网格价位挂买单或卖单
4. 买单成交后 → 在上方挂卖单
5. 卖单成交后 → 在下方挂买单
6. 循环往复，赚取价格波动利润

## 注意事项

- 目前使用模拟模式进行开发测试
- 正式交易前请确保理解网格策略风险
- 网格交易适合震荡市场，单边行情可能亏损
