# 网格交易工具 - 架构设计 V2

## 🏗️ 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                        前端 (浏览器)                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │  grid.html   │  │   grid.css   │  │   grid.js    │      │
│  │  界面布局     │  │   样式设计    │  │  业务逻辑     │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
│           │                │                 │              │
│           └────────────────┴─────────────────┘              │
│                          │                                  │
│                    MetaMask 集成                             │
│                  (私钥不离开浏览器)                           │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTPS
                           │ 传输: 地址、签名、Token
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    后端 API 服务器                           │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  wallet_connector.py - 钱包连接管理                   │  │
│  │  - MetaMask 连接器                                    │  │
│  │  - API Key 连接器                                     │  │
│  │  - Session 管理                                       │  │
│  └──────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  exchanges/ - 交易所适配器层                          │  │
│  │  ├── base.py          - 统一接口定义                  │  │
│  │  ├── standx.py        - StandX 实现                  │  │
│  │  ├── binance.py       - Binance 实现 (待开发)         │  │
│  │  └── bybit.py         - Bybit 实现 (待开发)          │  │
│  └──────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  grid_engine.py - 网格交易引擎                        │  │
│  │  - 统一的网格逻辑                                     │  │
│  │  - 适配所有交易所                                     │  │
│  └──────────────────────────────────────────────────────┘  │
└──────────────────────────┬──────────────────────────────────┘
                           │ API Key/Secret (不是私钥)
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                      交易所 API                              │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐             │
│  │  StandX  │    │ Binance  │    │  Bybit   │             │
│  └──────────┘    └──────────┘    └──────────┘             │
└─────────────────────────────────────────────────────────────┘
```

## 📁 文件结构

```
standx-grid-bot/
├── 前端文件
│   ├── grid.html              # 新版主界面
│   ├── grid.css               # 样式文件
│   └── grid.js                # 前端逻辑
│
├── 后端核心
│   ├── wallet_connector.py    # 🔐 钱包连接管理
│   ├── grid_engine.py         # ⚙️ 网格交易引擎
│   └── api_server.py          # 🌐 API 服务器
│
├── 交易所适配器
│   └── exchanges/
│       ├── __init__.py
│       ├── base.py            # 📋 基类定义
│       ├── standx.py          # StandX 实现
│       ├── binance.py         # Binance (待开发)
│       └── bybit.py           # Bybit (待开发)
│
├── 配置和文档
│   ├── SECURITY.md            # 🔐 安全架构文档
│   ├── ARCHITECTURE.md        # 🏗️ 本文档
│   ├── API.md                 # 📡 API 文档
│   └── config.sample.yaml     # 配置示例
│
└── 部署相关
    ├── Dockerfile
    ├── docker-compose.yml
    └── deploy.sh
```

## 🔑 核心组件

### 1. 交易所适配器层

#### 设计理念
- **统一接口**: 所有交易所实现相同的接口
- **易扩展**: 添加新交易所只需实现适配器
- **类型安全**: 使用数据类定义标准结构

#### 基类接口 (base.py)
```python
class ExchangeAdapter(ABC):
    # 市场数据
    async def get_ticker(symbol) -> Ticker
    async def get_orderbook(symbol) -> Dict
    
    # 账户管理
    async def get_balance() -> List[Balance]
    async def get_positions() -> List[Position]
    
    # 交易操作
    async def create_order(...) -> Order
    async def cancel_order(...) -> bool
    async def get_open_orders() -> List[Order]
```

#### 添加新交易所步骤
```python
# 1. 创建新文件 exchanges/newexchange.py
from .base import ExchangeAdapter

class NewExchangeAdapter(ExchangeAdapter):
    # 2. 实现所有抽象方法
    async def get_ticker(self, symbol):
        # 调用交易所 API
        # 转换为标准 Ticker 格式
        pass
    
    # 3. 实现其他方法...
```

### 2. 钱包连接管理

#### MetaMask 连接流程
```
1. 前端请求连接
   └─> POST /api/wallet/connect

2. 后端生成签名消息
   └─> 返回 { sessionId, message }

3. 前端 MetaMask 签名
   └─> window.ethereum.request('personal_sign')

4. 前端提交签名
   └─> POST /api/wallet/verify { sessionId, signature }

5. 后端验证签名
   └─> 验证通过，创建 Session

6. 前端存储 Session Token
   └─> localStorage.setItem('sessionToken', sessionId)

7. 后续请求携带 Token
   └─> headers: { 'X-Session-Token': sessionId }
```

#### Session 生命周期
```python
Session {
    id: uuid,
    address: "0x...",
    verified: bool,
    created_at: timestamp,
    expires_at: timestamp + 3600,  # 1小时
    last_activity: timestamp
}

# 自动清理过期 session
@scheduler.task('interval', hours=1)
def cleanup_sessions():
    wallet_manager.cleanup_expired_sessions()
```

### 3. 网格交易引擎

#### 与交易所解耦
```python
class GridEngine:
    def __init__(self, exchange: ExchangeAdapter):
        self.exchange = exchange  # 依赖注入
    
    async def start_grid(self, config):
        # 使用统一接口，不依赖具体交易所
        ticker = await self.exchange.get_ticker(config.symbol)
        orders = await self.create_grid_orders(config)
        # ...
```

#### 支持多种网格模式
```python
class GridMode(Enum):
    NEUTRAL = "neutral"  # 中性网格
    LONG = "long"        # 做多网格
    SHORT = "short"      # 做空网格

# 根据模式调整策略
if mode == GridMode.LONG:
    # 只在价格下跌时买入
    # 在上涨时卖出部分
```

## 🔄 数据流

### 启动网格交易
```
前端                    后端                    交易所
  │                      │                       │
  ├─ 提交配置 ──────────>│                       │
  │                      ├─ 验证 Session          │
  │                      ├─ 创建 GridEngine       │
  │                      ├─ 获取行情 ──────────>│
  │                      │<────────── 返回价格 ──┤
  │                      ├─ 计算网格层级           │
  │                      ├─ 批量下单 ──────────>│
  │                      │<────────── 订单确认 ──┤
  │<──── 返回网格ID ──────┤                       │
  │                      │                       │
  ├─ 轮询状态 ────────────>│                       │
  │<──── 网格状态 ─────────┤                       │
```

### 订单监控
```
GridEngine (后台任务)
  │
  ├─ 每5秒检查
  │   ├─> 获取活跃订单
  │   ├─> 检查成交状态
  │   └─> 自动补单
  │
  └─ WebSocket (未来)
      └─> 实时订单推送
```

## 🔐 安全措施

### 1. 零私钥暴露
```
✅ 正确做法：
- MetaMask 签名（私钥在浏览器）
- API Key 认证（不是私钥）
- Session Token（临时凭证）

❌ 错误做法：
- 配置文件存私钥
- API 传输私钥
- 数据库存私钥
```

### 2. 认证层级
```
Level 1: 公开 API
  - 获取行情
  - 获取市场数据
  └─> 无需认证

Level 2: 只读 API
  - 查询余额
  - 查询持仓
  └─> Session Token 认证

Level 3: 交易 API
  - 创建订单
  - 取消订单
  └─> Session Token + 权限验证
```

### 3. 请求签名
```python
# 每个请求生成唯一签名
def sign_request(session_token, timestamp, body):
    message = f"{session_token}{timestamp}{json.dumps(body)}"
    return hmac.sha256(message)

# 防重放攻击
if abs(time.time() - request_timestamp) > 60:
    raise InvalidRequestError("Request expired")
```

## 🎯 扩展点

### 添加新交易所
```python
# 1. 实现适配器
class BinanceAdapter(ExchangeAdapter):
    BASE_URL = "https://api.binance.com"
    # 实现所有方法...

# 2. 注册适配器
EXCHANGE_ADAPTERS = {
    'standx': StandXAdapter,
    'binance': BinanceAdapter,
    'bybit': BybitAdapter,
}

# 3. 前端选择
<select id="exchangeSelect">
    <option value="standx">StandX</option>
    <option value="binance">Binance</option>
    <option value="bybit">Bybit</option>
</select>
```

### 添加新策略
```python
class GridStrategy(ABC):
    @abstractmethod
    def calculate_grid_levels(self):
        pass
    
    @abstractmethod
    def should_buy(self, price, level):
        pass

class NeutralStrategy(GridStrategy):
    # 中性网格实现

class TrendStrategy(GridStrategy):
    # 趋势网格实现
```

## 📊 性能优化

### 1. 连接池
```python
# 复用 HTTP 连接
session = aiohttp.ClientSession(
    connector=aiohttp.TCPConnector(
        limit=100,
        limit_per_host=30
    )
)
```

### 2. 缓存
```python
# 缓存市场数据
@lru_cache(maxsize=100)
@ttl_cache(ttl=10)
async def get_ticker_cached(symbol):
    return await exchange.get_ticker(symbol)
```

### 3. 批量操作
```python
# 批量下单
async def create_grid_orders(levels):
    tasks = [
        create_order(level)
        for level in levels
    ]
    return await asyncio.gather(*tasks)
```

## 🧪 测试策略

### 单元测试
```python
# 测试适配器
async def test_standx_adapter():
    adapter = StandXAdapter(config)
    ticker = await adapter.get_ticker("BTC-USD")
    assert ticker.symbol == "BTC-USD"
    assert ticker.last_price > 0
```

### 集成测试
```python
# 测试完整流程
async def test_grid_workflow():
    # 1. 连接钱包
    session = await wallet_manager.connect(...)
    
    # 2. 创建网格
    grid = await create_grid(session, config)
    
    # 3. 验证订单
    orders = await exchange.get_open_orders()
    assert len(orders) == config.grid_count
```

## 📈 监控指标

```python
# 关键指标
metrics = {
    'active_sessions': len(sessions),
    'active_grids': len(grids),
    'total_orders': order_count,
    'api_calls_per_minute': api_rate,
    'error_rate': errors / total_requests
}
```

## 🚀 部署建议

### 开发环境
```bash
# 本地运行
python api_server.py
# 前端使用 Live Server
```

### 生产环境
```bash
# Docker 部署
docker-compose up -d

# 或 Railway
railway up
```

---

**架构设计原则：安全第一、可扩展性、简单清晰**
