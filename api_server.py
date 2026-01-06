"""
API 服务器
=========

使用 FastAPI 构建 RESTful API 和 WebSocket 服务。

功能：
1. 钱包认证（MetaMask 签名）
2. 网格交易 CRUD 接口
3. 实时行情 WebSocket 推送
4. 订单状态实时更新

启动方式：
    uvicorn api_server:app --reload --port 8000
"""

import asyncio
import json
from datetime import datetime
from typing import Dict, List, Optional, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Depends, Header
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from exchanges import create_adapter, StandXAdapter
from database import db, GridConfig, GridMode, GridStatus
from grid_engine import GridEngine
from wallet_connector import wallet_manager


# ========== Pydantic 模型定义 ==========

class WalletConnectRequest(BaseModel):
    """钱包连接请求"""
    address: str = Field(..., description="钱包地址")


class WalletVerifyRequest(BaseModel):
    """钱包验证请求"""
    session_id: str = Field(..., description="会话 ID")
    address: str = Field(..., description="钱包地址")
    message: str = Field(..., description="签名消息")
    signature: str = Field(..., description="签名")


class GridCreateRequest(BaseModel):
    """创建网格请求"""
    symbol: str = Field(default="BTC-USD", description="交易对")
    mode: str = Field(default="neutral", description="网格模式: neutral/long/short")
    lower_price: float = Field(..., description="下限价格")
    upper_price: float = Field(..., description="上限价格")
    grid_count: int = Field(default=10, description="网格数量")
    per_grid_amount: float = Field(default=100, description="每格投资金额")
    leverage: int = Field(default=1, description="杠杆倍数")
    stop_loss_percent: Optional[float] = Field(None, description="止损百分比")
    take_profit_percent: Optional[float] = Field(None, description="止盈百分比")


class ApiConfigRequest(BaseModel):
    """API 配置请求"""
    jwt_token: Optional[str] = Field(None, description="StandX JWT Token")
    simulation_mode: bool = Field(default=True, description="是否使用模拟模式")


class GridResponse(BaseModel):
    """网格响应"""
    id: int
    symbol: str
    mode: str
    status: str
    lower_price: float
    upper_price: float
    grid_count: int
    per_grid_amount: float
    leverage: int
    realized_pnl: float
    unrealized_pnl: float
    total_trades: int
    created_at: str


# ========== 全局变量 ==========

# 交易所适配器（模拟模式）
exchange_adapter: Optional[StandXAdapter] = None

# 网格引擎
grid_engine: Optional[GridEngine] = None

# API 配置（会话级别存储）
# 格式: {session_id: {"jwt_token": str, "simulation_mode": bool}}
api_configs: Dict[str, Dict[str, Any]] = {}

# WebSocket 连接管理
class ConnectionManager:
    """WebSocket 连接管理器"""

    def __init__(self):
        # 活跃连接 {session_id: WebSocket}
        self.active_connections: Dict[str, WebSocket] = {}
        # 行情订阅 {symbol: [session_id, ...]}
        self.ticker_subscriptions: Dict[str, List[str]] = {}

    async def connect(self, websocket: WebSocket, session_id: str):
        """建立连接"""
        await websocket.accept()
        self.active_connections[session_id] = websocket

    def disconnect(self, session_id: str):
        """断开连接"""
        if session_id in self.active_connections:
            del self.active_connections[session_id]
        # 清理订阅
        for symbol in self.ticker_subscriptions:
            if session_id in self.ticker_subscriptions[symbol]:
                self.ticker_subscriptions[symbol].remove(session_id)

    async def send_personal_message(self, message: dict, session_id: str):
        """发送个人消息"""
        if session_id in self.active_connections:
            try:
                await self.active_connections[session_id].send_json(message)
            except:
                self.disconnect(session_id)

    async def broadcast_ticker(self, symbol: str, data: dict):
        """广播行情数据"""
        subscribers = self.ticker_subscriptions.get(symbol, [])
        for session_id in subscribers:
            await self.send_personal_message({
                "type": "ticker",
                "symbol": symbol,
                "data": data
            }, session_id)

    def subscribe_ticker(self, session_id: str, symbol: str):
        """订阅行情"""
        if symbol not in self.ticker_subscriptions:
            self.ticker_subscriptions[symbol] = []
        if session_id not in self.ticker_subscriptions[symbol]:
            self.ticker_subscriptions[symbol].append(session_id)

    def unsubscribe_ticker(self, session_id: str, symbol: str):
        """取消订阅"""
        if symbol in self.ticker_subscriptions:
            if session_id in self.ticker_subscriptions[symbol]:
                self.ticker_subscriptions[symbol].remove(session_id)


ws_manager = ConnectionManager()


# ========== 应用生命周期 ==========

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理

    启动时初始化交易所适配器和网格引擎
    关闭时清理资源
    """
    global exchange_adapter, grid_engine

    print("🚀 正在启动 GridBot API 服务器...")

    # 初始化交易所适配器（模拟模式）
    exchange_adapter = create_adapter('standx', {'simulation': True})
    await exchange_adapter.initialize()

    # 初始化网格引擎
    grid_engine = GridEngine(exchange_adapter, db)

    # 设置回调（用于 WebSocket 推送）
    grid_engine.set_callbacks(
        on_order_filled=lambda grid_id, level, order: asyncio.create_task(
            notify_order_filled(grid_id, level, order)
        ),
        on_grid_updated=lambda grid_id: asyncio.create_task(
            notify_grid_updated(grid_id)
        )
    )

    # 启动行情推送任务
    ticker_task = asyncio.create_task(ticker_broadcast_loop())

    print("✅ GridBot API 服务器已启动")
    print("📡 访问 http://localhost:8000 查看界面")

    yield

    # 清理
    print("🔌 正在关闭服务器...")
    ticker_task.cancel()
    if grid_engine:
        await grid_engine.shutdown()
    if exchange_adapter:
        await exchange_adapter.close()

    print("👋 服务器已关闭")


# 创建 FastAPI 应用
app = FastAPI(
    title="GridBot API",
    description="网格交易机器人 API 服务",
    version="1.0.0",
    lifespan=lifespan
)

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 开发环境允许所有来源
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ========== 依赖注入 ==========

async def get_session_id(x_session_token: Optional[str] = Header(None)) -> Optional[str]:
    """
    从请求头获取会话 ID

    前端需要在请求头中携带 X-Session-Token
    """
    return x_session_token


async def require_auth(session_id: str = Depends(get_session_id)) -> str:
    """
    要求认证

    验证会话是否有效
    """
    if not session_id:
        raise HTTPException(status_code=401, detail="未提供认证信息")

    if not wallet_manager.is_connected(session_id):
        raise HTTPException(status_code=401, detail="会话无效或已过期")

    return session_id


# ========== 静态文件和页面 ==========

# 挂载静态文件目录
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def get_index():
    """返回主页面"""
    return FileResponse("templates/grid.html")


# ========== 钱包认证 API ==========

@app.post("/api/wallet/connect")
async def connect_wallet(request: WalletConnectRequest):
    """
    发起钱包连接

    前端调用此接口获取待签名消息，然后使用 MetaMask 签名

    Returns:
        session_id: 会话 ID
        message: 待签名消息
    """
    result = await wallet_manager.connect_wallet(
        session_id=None,  # 自动生成
        connector_type="metamask"
    )

    # 生成待签名消息
    session_token = result.get("session_token", "")
    message = f"GridBot 登录验证\n\n钱包: {request.address}\n时间: {datetime.now().isoformat()}\nNonce: {session_token[:8]}"

    return {
        "success": True,
        "session_id": session_token,
        "message": message
    }


@app.post("/api/wallet/verify")
async def verify_wallet(request: WalletVerifyRequest):
    """
    验证钱包签名

    前端使用 MetaMask 签名后调用此接口验证

    Returns:
        success: 是否验证成功
        session_id: 会话 ID（用于后续请求）
    """
    # 验证签名
    verified = await wallet_manager.verify_and_activate(
        session_id=request.session_id,
        address=request.address,
        message=request.message,
        signature=request.signature
    )

    if not verified:
        raise HTTPException(status_code=401, detail="签名验证失败")

    return {
        "success": True,
        "session_id": request.session_id,
        "address": request.address
    }


@app.post("/api/wallet/disconnect")
async def disconnect_wallet(session_id: str = Depends(require_auth)):
    """断开钱包连接"""
    await wallet_manager.disconnect_wallet(session_id)
    return {"success": True}


@app.get("/api/wallet/status")
async def get_wallet_status(session_id: str = Depends(get_session_id)):
    """获取钱包连接状态"""
    if not session_id:
        return {"connected": False}

    connected = wallet_manager.is_connected(session_id)
    address = wallet_manager.get_address(session_id) if connected else None

    return {
        "connected": connected,
        "address": address
    }


# ========== 市场数据 API ==========

@app.get("/api/market/ticker/{symbol}")
async def get_ticker(symbol: str):
    """
    获取实时行情

    Args:
        symbol: 交易对，如 BTC-USD
    """
    try:
        ticker = await exchange_adapter.get_ticker(symbol)
        return {
            "symbol": ticker.symbol,
            "last_price": ticker.last_price,
            "bid_price": ticker.bid_price,
            "ask_price": ticker.ask_price,
            "mark_price": ticker.mark_price,
            "timestamp": ticker.timestamp
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/market/orderbook/{symbol}")
async def get_orderbook(symbol: str, depth: int = 20):
    """获取订单簿"""
    try:
        orderbook = await exchange_adapter.get_orderbook(symbol, depth)
        return orderbook
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/market/symbols")
async def get_symbols():
    """获取支持的交易对列表"""
    return {
        "symbols": exchange_adapter.get_supported_symbols()
    }


# ========== 账户 API ==========

@app.get("/api/account/balance")
async def get_balance(session_id: str = Depends(require_auth)):
    """获取账户余额"""
    try:
        balances = await exchange_adapter.get_balance()
        return {
            "balances": [
                {
                    "asset": b.asset,
                    "free": b.free,
                    "locked": b.locked,
                    "total": b.total
                }
                for b in balances
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/account/positions")
async def get_positions(session_id: str = Depends(require_auth)):
    """获取持仓信息"""
    try:
        positions = await exchange_adapter.get_positions()
        return {
            "positions": [
                {
                    "symbol": p.symbol,
                    "side": p.side,
                    "size": p.size,
                    "entry_price": p.entry_price,
                    "mark_price": p.mark_price,
                    "unrealized_pnl": p.unrealized_pnl,
                    "leverage": p.leverage
                }
                for p in positions
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ========== API 配置 ==========

@app.post("/api/config/api")
async def save_api_config(
    request: ApiConfigRequest,
    session_id: str = Depends(get_session_id)
):
    """
    保存 API 配置

    保存用户的 StandX API 配置（JWT Token 和模拟模式设置）
    """
    global exchange_adapter, grid_engine

    # 存储配置（使用会话 ID 或 'default'）
    config_key = session_id or 'default'
    api_configs[config_key] = {
        "jwt_token": request.jwt_token,
        "simulation_mode": request.simulation_mode
    }

    # 如果切换到真实模式且有 JWT Token，重新初始化适配器
    if not request.simulation_mode and request.jwt_token:
        try:
            # 关闭旧适配器
            if exchange_adapter:
                await exchange_adapter.close()

            # 创建新适配器（真实模式）
            exchange_adapter = create_adapter('standx', {
                'simulation': False,
                'jwt_token': request.jwt_token
            })
            await exchange_adapter.initialize()

            # 更新网格引擎的适配器
            if grid_engine:
                grid_engine.exchange = exchange_adapter

            return {
                "success": True,
                "mode": "live",
                "message": "已切换到真实交易模式"
            }
        except Exception as e:
            # 如果失败，回退到模拟模式
            exchange_adapter = create_adapter('standx', {'simulation': True})
            await exchange_adapter.initialize()
            raise HTTPException(status_code=400, detail=f"API 配置失败: {str(e)}")
    else:
        # 模拟模式
        if exchange_adapter and not exchange_adapter.simulation:
            # 切换回模拟模式
            await exchange_adapter.close()
            exchange_adapter = create_adapter('standx', {'simulation': True})
            await exchange_adapter.initialize()

            if grid_engine:
                grid_engine.exchange = exchange_adapter

        return {
            "success": True,
            "mode": "simulation",
            "message": "已保存配置，使用模拟模式"
        }


@app.post("/api/config/test")
async def test_api_connection(request: ApiConfigRequest):
    """
    测试 API 连接

    使用提供的 JWT Token 测试与 StandX API 的连接
    """
    if not request.jwt_token:
        raise HTTPException(status_code=400, detail="请提供 JWT Token")

    try:
        # 创建临时适配器进行测试
        test_adapter = create_adapter('standx', {
            'simulation': False,
            'jwt_token': request.jwt_token
        })
        await test_adapter.initialize()

        # 尝试获取账户余额
        balances = await test_adapter.get_balance()

        # 关闭测试适配器
        await test_adapter.close()

        # 找到 DUSD 余额
        dusd_balance = None
        for balance in balances:
            if balance.asset == 'DUSD':
                dusd_balance = balance.total
                break

        return {
            "success": True,
            "balance": dusd_balance or 0,
            "message": "API 连接成功"
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"连接测试失败: {str(e)}")


@app.get("/api/config/status")
async def get_api_config_status(session_id: str = Depends(get_session_id)):
    """
    获取 API 配置状态

    返回当前的 API 配置模式（模拟/真实）
    """
    config_key = session_id or 'default'
    config = api_configs.get(config_key, {})

    return {
        "simulation_mode": config.get("simulation_mode", True),
        "is_configured": bool(config.get("jwt_token")),
        "adapter_mode": "simulation" if (exchange_adapter and exchange_adapter.simulation) else "live"
    }


# ========== 网格交易 API ==========

@app.post("/api/grid/create")
async def create_grid(request: GridCreateRequest, session_id: str = Depends(require_auth)):
    """
    创建网格交易

    创建并启动一个新的网格交易策略
    """
    # 获取钱包地址
    address = wallet_manager.get_address(session_id)

    # 创建网格配置
    config = GridConfig(
        wallet_address=address,
        exchange="standx",
        symbol=request.symbol,
        mode=request.mode,
        lower_price=request.lower_price,
        upper_price=request.upper_price,
        grid_count=request.grid_count,
        per_grid_amount=request.per_grid_amount,
        leverage=request.leverage,
        stop_loss_percent=request.stop_loss_percent,
        take_profit_percent=request.take_profit_percent
    )

    try:
        # 启动网格
        grid_id = await grid_engine.start_grid(config)

        return {
            "success": True,
            "grid_id": grid_id,
            "message": f"网格交易已启动: {request.symbol}"
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/grid/list")
async def list_grids(session_id: str = Depends(require_auth)):
    """获取用户的网格列表"""
    address = wallet_manager.get_address(session_id)
    grids = db.get_grids_by_wallet(address)

    return {
        "grids": [
            {
                "id": g.id,
                "symbol": g.symbol,
                "mode": g.mode,
                "status": g.status,
                "lower_price": g.lower_price,
                "upper_price": g.upper_price,
                "grid_count": g.grid_count,
                "per_grid_amount": g.per_grid_amount,
                "leverage": g.leverage,
                "realized_pnl": g.realized_pnl,
                "unrealized_pnl": g.unrealized_pnl,
                "total_trades": g.total_trades,
                "created_at": g.created_at
            }
            for g in grids
        ]
    }


@app.get("/api/grid/{grid_id}")
async def get_grid(grid_id: int, session_id: str = Depends(require_auth)):
    """获取网格详情"""
    status = await grid_engine.get_grid_status(grid_id)

    if not status:
        raise HTTPException(status_code=404, detail="网格不存在")

    return status


@app.get("/api/grid/{grid_id}/orders")
async def get_grid_orders(grid_id: int, session_id: str = Depends(require_auth)):
    """获取网格订单列表"""
    orders = await grid_engine.get_grid_orders(grid_id)
    return {"orders": orders}


@app.get("/api/grid/{grid_id}/trades")
async def get_grid_trades(grid_id: int, limit: int = 50,
                          session_id: str = Depends(require_auth)):
    """获取网格成交历史"""
    trades = await grid_engine.get_grid_trades(grid_id, limit)
    return {"trades": trades}


@app.post("/api/grid/{grid_id}/stop")
async def stop_grid(grid_id: int, session_id: str = Depends(require_auth)):
    """停止网格交易"""
    success = await grid_engine.stop_grid(grid_id)

    if not success:
        raise HTTPException(status_code=400, detail="停止网格失败")

    return {"success": True, "message": "网格已停止"}


@app.post("/api/grid/{grid_id}/pause")
async def pause_grid(grid_id: int, session_id: str = Depends(require_auth)):
    """暂停网格交易"""
    success = await grid_engine.pause_grid(grid_id)

    if not success:
        raise HTTPException(status_code=400, detail="暂停网格失败")

    return {"success": True, "message": "网格已暂停"}


@app.post("/api/grid/{grid_id}/resume")
async def resume_grid(grid_id: int, session_id: str = Depends(require_auth)):
    """恢复网格交易"""
    success = await grid_engine.resume_grid(grid_id)

    if not success:
        raise HTTPException(status_code=400, detail="恢复网格失败")

    return {"success": True, "message": "网格已恢复"}


@app.delete("/api/grid/{grid_id}")
async def delete_grid(grid_id: int, session_id: str = Depends(require_auth)):
    """删除网格（仅限已停止的网格）"""
    grid = db.get_grid(grid_id)

    if not grid:
        raise HTTPException(status_code=404, detail="网格不存在")

    if grid.status == GridStatus.RUNNING.value:
        raise HTTPException(status_code=400, detail="请先停止网格")

    db.delete_grid(grid_id)
    return {"success": True, "message": "网格已删除"}


# ========== WebSocket API ==========

@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """
    WebSocket 连接端点

    支持的消息类型：
    - subscribe_ticker: 订阅行情 {"type": "subscribe_ticker", "symbol": "BTC-USD"}
    - unsubscribe_ticker: 取消订阅 {"type": "unsubscribe_ticker", "symbol": "BTC-USD"}
    - subscribe_grid: 订阅网格更新 {"type": "subscribe_grid", "grid_id": 1}
    """
    await ws_manager.connect(websocket, session_id)

    try:
        while True:
            # 接收消息
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "subscribe_ticker":
                symbol = data.get("symbol", "BTC-USD")
                ws_manager.subscribe_ticker(session_id, symbol)
                await websocket.send_json({
                    "type": "subscribed",
                    "channel": "ticker",
                    "symbol": symbol
                })

            elif msg_type == "unsubscribe_ticker":
                symbol = data.get("symbol", "BTC-USD")
                ws_manager.unsubscribe_ticker(session_id, symbol)
                await websocket.send_json({
                    "type": "unsubscribed",
                    "channel": "ticker",
                    "symbol": symbol
                })

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        ws_manager.disconnect(session_id)


# ========== 后台任务 ==========

async def ticker_broadcast_loop():
    """
    行情广播循环

    定期获取行情并推送给订阅者
    """
    while True:
        try:
            # 获取所有订阅的交易对
            symbols = set()
            for symbol, subscribers in ws_manager.ticker_subscriptions.items():
                if subscribers:
                    symbols.add(symbol)

            # 获取并广播行情
            for symbol in symbols:
                try:
                    ticker = await exchange_adapter.get_ticker(symbol)
                    await ws_manager.broadcast_ticker(symbol, {
                        "last_price": ticker.last_price,
                        "bid_price": ticker.bid_price,
                        "ask_price": ticker.ask_price,
                        "mark_price": ticker.mark_price,
                        "timestamp": ticker.timestamp
                    })
                except Exception as e:
                    print(f"获取 {symbol} 行情失败: {e}")

            # 每秒更新一次
            await asyncio.sleep(1)

        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"行情广播错误: {e}")
            await asyncio.sleep(1)


async def notify_order_filled(grid_id: int, level, order):
    """通知订单成交"""
    # 获取订阅该网格的用户并发送通知
    grid = db.get_grid(grid_id)
    if not grid:
        return

    # 找到对应的 session_id
    for session_id in ws_manager.active_connections:
        address = wallet_manager.get_address(session_id)
        if address and address.lower() == grid.wallet_address.lower():
            await ws_manager.send_personal_message({
                "type": "order_filled",
                "grid_id": grid_id,
                "level": level.level,
                "side": level.side,
                "price": level.price,
                "quantity": level.quantity
            }, session_id)


async def notify_grid_updated(grid_id: int):
    """通知网格更新"""
    grid = db.get_grid(grid_id)
    if not grid:
        return

    for session_id in ws_manager.active_connections:
        address = wallet_manager.get_address(session_id)
        if address and address.lower() == grid.wallet_address.lower():
            await ws_manager.send_personal_message({
                "type": "grid_updated",
                "grid_id": grid_id,
                "realized_pnl": grid.realized_pnl,
                "total_trades": grid.total_trades
            }, session_id)


# ========== 入口点 ==========

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
