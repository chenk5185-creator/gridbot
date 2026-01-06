"""
StandX 交易所适配器
==================

StandX 是一个去中心化永续合约交易平台，使用 DUSD（生息稳定币）作为保证金。

API 文档: https://docs.standx.com/standx-api/perps-http

认证方式:
- 公开接口：无需认证
- 私有接口：需要 JWT Token（通过钱包签名获取）

费率:
- Maker: 0.01%
- Taker: 0.04%
"""

import aiohttp
import hashlib
import hmac
import time
import uuid
import json
from typing import Dict, List, Any, Optional
from datetime import datetime

from .base import (
    ExchangeAdapter, Balance, Position, Order, Ticker,
    OrderSide, OrderType, OrderStatus
)


class StandXAdapter(ExchangeAdapter):
    """
    StandX 交易所适配器

    支持功能：
    - 获取实时行情（公开）
    - 获取订单簿深度（公开）
    - 查询账户余额（需认证）
    - 查询持仓信息（需认证）
    - 创建/取消订单（需认证+签名）

    使用示例：
        config = {
            'jwt_token': 'your_jwt_token',  # 通过钱包签名获取
        }
        adapter = StandXAdapter(config)
        await adapter.initialize()

        # 获取 BTC 行情
        ticker = await adapter.get_ticker('BTC-USD')
        print(f"BTC 价格: {ticker.last_price}")
    """

    # ========== API 基础配置 ==========
    BASE_URL = "https://perps.standx.com"           # REST API 基础 URL
    WS_URL = "wss://perps.standx.com/ws-stream/v1"  # WebSocket URL

    # 支持的交易对
    SUPPORTED_SYMBOLS = ["BTC-USD", "ETH-USD", "SOL-USD"]

    # 价格和数量精度配置
    SYMBOL_CONFIG = {
        "BTC-USD": {"price_precision": 1, "qty_precision": 4},
        "ETH-USD": {"price_precision": 2, "qty_precision": 3},
        "SOL-USD": {"price_precision": 3, "qty_precision": 2},
    }

    def __init__(self, config: Dict[str, Any]):
        """
        初始化 StandX 适配器

        Args:
            config: 配置字典
                - jwt_token: JWT 认证令牌（通过钱包签名获取）
                - simulation: 是否为模拟模式（默认 True）
        """
        super().__init__(config)

        # HTTP 会话
        self.session: Optional[aiohttp.ClientSession] = None

        # JWT 认证令牌（通过 MetaMask 签名获取）
        self.jwt_token: Optional[str] = config.get('jwt_token')

        # 模拟模式标志（开发阶段使用模拟数据）
        self.simulation = config.get('simulation', True)

        # 模拟数据（开发测试用）
        self._mock_balance = 10000.0  # 模拟余额 10000 DUSD
        self._mock_positions: Dict[str, Dict] = {}  # 模拟持仓

    async def initialize(self) -> bool:
        """
        初始化连接

        创建 HTTP 会话，准备进行 API 调用。
        注意：StandX 使用 JWT 认证，私钥不会传到服务器。

        Returns:
            bool: 初始化是否成功
        """
        # 创建 HTTP 会话，配置连接池
        connector = aiohttp.TCPConnector(
            limit=100,           # 总连接数限制
            limit_per_host=30,   # 每个主机连接数限制
        )
        self.session = aiohttp.ClientSession(connector=connector)
        return True

    async def close(self):
        """关闭连接，释放资源"""
        if self.session:
            await self.session.close()
            self.session = None

    # ========== 认证和请求签名 ==========

    def _get_headers(self, need_auth: bool = False, need_sign: bool = False,
                     body: Optional[Dict] = None) -> Dict[str, str]:
        """
        构建请求头

        Args:
            need_auth: 是否需要 JWT 认证
            need_sign: 是否需要请求签名
            body: 请求体（签名时需要）

        Returns:
            Dict[str, str]: 请求头字典
        """
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        # 添加 JWT 认证头
        if need_auth and self.jwt_token:
            headers["Authorization"] = f"Bearer {self.jwt_token}"

        # 添加请求签名（交易相关接口需要）
        if need_sign:
            request_id = str(uuid.uuid4())      # 唯一请求 ID
            timestamp = str(int(time.time()))   # 时间戳

            headers.update({
                "x-request-sign-version": "v1",
                "x-request-id": request_id,
                "x-request-timestamp": timestamp,
            })

            # 计算签名（如果有签名密钥）
            # 注意：StandX 使用 JWT 认证，这里的签名是可选的
            # 实际签名逻辑需要根据官方文档实现

        return headers

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        data: Optional[Dict] = None,
        need_auth: bool = False,
        need_sign: bool = False
    ) -> Dict[str, Any]:
        """
        发送 HTTP 请求

        Args:
            method: HTTP 方法 (GET, POST, DELETE)
            endpoint: API 端点路径
            params: URL 查询参数
            data: 请求体数据
            need_auth: 是否需要认证
            need_sign: 是否需要签名

        Returns:
            Dict[str, Any]: API 响应数据

        Raises:
            aiohttp.ClientError: 网络请求失败
            ValueError: API 返回错误
        """
        url = f"{self.BASE_URL}{endpoint}"
        headers = self._get_headers(need_auth, need_sign, data)

        try:
            async with self.session.request(
                method,
                url,
                headers=headers,
                params=params,
                json=data
            ) as response:
                # 检查 HTTP 状态码
                if response.status >= 400:
                    error_text = await response.text()
                    raise ValueError(f"API 错误 [{response.status}]: {error_text}")

                return await response.json()

        except aiohttp.ClientError as e:
            raise ValueError(f"网络请求失败: {str(e)}")

    # ========== 市场数据接口（公开，无需认证） ==========

    async def get_ticker(self, symbol: str) -> Ticker:
        """
        获取实时行情

        Args:
            symbol: 交易对符号，如 "BTC-USD"

        Returns:
            Ticker: 行情数据，包含最新价、买卖价、标记价等

        Example:
            >>> ticker = await adapter.get_ticker("BTC-USD")
            >>> print(f"BTC 最新价: ${ticker.last_price:,.2f}")
        """
        # 模拟模式：返回模拟数据
        if self.simulation:
            return self._get_mock_ticker(symbol)

        # 实际 API 调用
        data = await self._request(
            "GET",
            "/api/query_symbol_price",
            params={"symbol": symbol}
        )

        return Ticker(
            symbol=symbol,
            last_price=float(data.get('last_price', 0)),
            bid_price=float(data.get('spread_bid', 0)),      # 买一价
            ask_price=float(data.get('spread_ask', 0)),      # 卖一价
            mark_price=float(data.get('mark_price', 0)),     # 标记价格
            index_price=float(data.get('index_price', 0)),   # 指数价格
            timestamp=int(datetime.now().timestamp() * 1000)
        )

    async def get_orderbook(self, symbol: str, depth: int = 20) -> Dict[str, Any]:
        """
        获取订单簿深度

        Args:
            symbol: 交易对符号
            depth: 深度层数，默认 20

        Returns:
            Dict: 订单簿数据
                - symbol: 交易对
                - bids: 买单列表 [[price, qty], ...]
                - asks: 卖单列表 [[price, qty], ...]
        """
        # 模拟模式
        if self.simulation:
            return self._get_mock_orderbook(symbol, depth)

        data = await self._request(
            "GET",
            "/api/query_depth_book",
            params={"symbol": symbol}
        )

        return {
            "symbol": symbol,
            "bids": data.get('bids', [])[:depth],  # 买单（价格从高到低）
            "asks": data.get('asks', [])[:depth],  # 卖单（价格从低到高）
        }

    # ========== 账户接口（需要认证） ==========

    async def get_balance(self, asset: Optional[str] = None) -> List[Balance]:
        """
        获取账户余额

        StandX 使用 DUSD 作为统一保证金。

        Args:
            asset: 资产名称，None 表示所有资产

        Returns:
            List[Balance]: 余额列表

        Note:
            - free: 可用余额（可用于开仓）
            - locked: 锁定余额（已用于保证金）
            - total: 总余额
        """
        # 模拟模式
        if self.simulation:
            return self._get_mock_balance(asset)

        data = await self._request(
            "GET",
            "/api/query_balance",
            need_auth=True
        )

        # StandX 返回的余额字段说明：
        # - balance: 账户总余额
        # - cross_available: 全仓可用余额
        # - locked: 锁定保证金
        # - equity: 账户权益（含未实现盈亏）
        balance = Balance(
            asset="DUSD",
            free=float(data.get('cross_available', 0)),
            locked=float(data.get('locked', 0)),
            total=float(data.get('balance', 0))
        )

        if asset and asset.upper() != "DUSD":
            return []

        return [balance]

    async def get_positions(self, symbol: Optional[str] = None) -> List[Position]:
        """
        获取持仓信息

        Args:
            symbol: 交易对符号，None 表示所有持仓

        Returns:
            List[Position]: 持仓列表

        Note:
            返回的持仓信息包含：
            - 持仓方向（多/空）
            - 持仓数量
            - 开仓均价
            - 标记价格
            - 未实现盈亏
            - 杠杆倍数
        """
        # 模拟模式
        if self.simulation:
            return self._get_mock_positions(symbol)

        params = {"symbol": symbol} if symbol else {}
        data = await self._request(
            "GET",
            "/api/query_positions",
            params=params,
            need_auth=True
        )

        positions = []
        position_list = data if isinstance(data, list) else [data]

        for pos_data in position_list:
            qty = float(pos_data.get('qty', 0))
            if qty == 0:  # 跳过空仓位
                continue

            position = Position(
                symbol=pos_data['symbol'],
                side="long" if qty > 0 else "short",  # 正数为多，负数为空
                size=abs(qty),
                entry_price=float(pos_data.get('entry_price', 0)),
                mark_price=float(pos_data.get('mark_price', 0)),
                unrealized_pnl=float(pos_data.get('upnl', 0)),
                leverage=int(pos_data.get('leverage', 1))
            )
            positions.append(position)

        return positions

    # ========== 交易接口（需要认证 + 签名） ==========

    async def create_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: Optional[float] = None,
        **kwargs
    ) -> Order:
        """
        创建订单

        Args:
            symbol: 交易对符号
            side: 订单方向 (BUY/SELL)
            order_type: 订单类型 (LIMIT/MARKET)
            quantity: 下单数量
            price: 限价单价格（市价单可不填）
            **kwargs: 额外参数
                - leverage: 杠杆倍数（默认 1）
                - time_in_force: 有效期（gtc/ioc/fok）
                - reduce_only: 是否只减仓

        Returns:
            Order: 创建的订单信息

        Example:
            >>> # 创建 BTC 限价买单
            >>> order = await adapter.create_order(
            ...     symbol="BTC-USD",
            ...     side=OrderSide.BUY,
            ...     order_type=OrderType.LIMIT,
            ...     quantity=0.01,
            ...     price=50000.0,
            ...     leverage=5
            ... )
        """
        # 模拟模式
        if self.simulation:
            return self._create_mock_order(symbol, side, order_type, quantity, price, **kwargs)

        # 构建订单请求体
        order_data = {
            "symbol": symbol,
            "side": side.value,
            "order_type": order_type.value,
            "qty": self.format_quantity(quantity, symbol),
            "leverage": kwargs.get("leverage", 1),
            "time_in_force": kwargs.get("time_in_force", "gtc"),
            "reduce_only": kwargs.get("reduce_only", False),
        }

        # 限价单需要价格
        if order_type == OrderType.LIMIT:
            if price is None:
                raise ValueError("限价单必须指定价格")
            order_data["price"] = self.format_price(price, symbol)

        # 发送订单请求
        response = await self._request(
            "POST",
            "/api/new_order",
            data=order_data,
            need_auth=True,
            need_sign=True
        )

        return Order(
            order_id=str(response.get('order_id', '')),
            symbol=symbol,
            side=side,
            order_type=order_type,
            price=price or 0,
            quantity=quantity,
            filled_quantity=0,
            status=OrderStatus.PENDING,
            timestamp=int(datetime.now().timestamp() * 1000)
        )

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """
        取消订单

        Args:
            symbol: 交易对符号
            order_id: 订单 ID

        Returns:
            bool: 是否取消成功
        """
        if self.simulation:
            return True  # 模拟模式直接返回成功

        try:
            await self._request(
                "POST",
                "/api/cancel_order",
                data={"order_id": int(order_id)},
                need_auth=True,
                need_sign=True
            )
            return True
        except Exception as e:
            print(f"取消订单失败: {e}")
            return False

    async def cancel_all_orders(self, symbol: str) -> int:
        """
        取消指定交易对的所有订单

        Args:
            symbol: 交易对符号

        Returns:
            int: 取消的订单数量
        """
        if self.simulation:
            return 0  # 模拟模式

        # 先获取所有活跃订单
        orders = await self.get_open_orders(symbol)
        if not orders:
            return 0

        order_ids = [int(order.order_id) for order in orders]

        await self._request(
            "POST",
            "/api/cancel_orders",
            data={"order_id_list": order_ids},
            need_auth=True,
            need_sign=True
        )

        return len(order_ids)

    async def get_order(self, symbol: str, order_id: str) -> Order:
        """
        查询订单详情

        Args:
            symbol: 交易对符号
            order_id: 订单 ID

        Returns:
            Order: 订单信息
        """
        if self.simulation:
            # 模拟模式返回一个已成交的订单
            return Order(
                order_id=order_id,
                symbol=symbol,
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                price=50000.0,
                quantity=0.01,
                filled_quantity=0.01,
                status=OrderStatus.FILLED,
                timestamp=int(datetime.now().timestamp() * 1000)
            )

        data = await self._request(
            "GET",
            "/api/query_order",
            params={"order_id": int(order_id)},
            need_auth=True
        )

        return self._parse_order(data)

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """
        获取活跃订单列表

        Args:
            symbol: 交易对符号，None 表示所有交易对

        Returns:
            List[Order]: 活跃订单列表
        """
        if self.simulation:
            return []  # 模拟模式

        params = {"symbol": symbol} if symbol else {}
        data = await self._request(
            "GET",
            "/api/query_open_orders",
            params=params,
            need_auth=True
        )

        orders = []
        for order_data in data.get('result', []):
            orders.append(self._parse_order(order_data))

        return orders

    def _parse_order(self, data: Dict) -> Order:
        """
        解析订单数据

        将 API 返回的订单数据转换为标准 Order 对象
        """
        # 订单状态映射
        status_map = {
            "open": OrderStatus.OPEN,
            "filled": OrderStatus.FILLED,
            "canceled": OrderStatus.CANCELLED,
            "cancelled": OrderStatus.CANCELLED,
            "rejected": OrderStatus.REJECTED,
            "pending": OrderStatus.PENDING,
        }

        # 解析时间戳
        created_at = data.get('created_at', '')
        if created_at:
            try:
                timestamp = int(datetime.fromisoformat(
                    created_at.replace('Z', '+00:00')
                ).timestamp() * 1000)
            except:
                timestamp = int(datetime.now().timestamp() * 1000)
        else:
            timestamp = int(datetime.now().timestamp() * 1000)

        return Order(
            order_id=str(data.get('id', data.get('order_id', ''))),
            symbol=data.get('symbol', ''),
            side=OrderSide.BUY if data.get('side') == 'buy' else OrderSide.SELL,
            order_type=OrderType.LIMIT if data.get('order_type') == 'limit' else OrderType.MARKET,
            price=float(data.get('price', 0)),
            quantity=float(data.get('qty', 0)),
            filled_quantity=float(data.get('fill_qty', 0)),
            status=status_map.get(data.get('status', 'pending'), OrderStatus.PENDING),
            timestamp=timestamp
        )

    # ========== 工具方法 ==========

    def get_supported_symbols(self) -> List[str]:
        """获取支持的交易对列表"""
        return self.SUPPORTED_SYMBOLS.copy()

    def format_price(self, price: float, symbol: str) -> str:
        """
        格式化价格到交易所要求的精度

        Args:
            price: 原始价格
            symbol: 交易对符号

        Returns:
            str: 格式化后的价格字符串
        """
        config = self.SYMBOL_CONFIG.get(symbol, {"price_precision": 2})
        precision = config["price_precision"]
        return f"{price:.{precision}f}"

    def format_quantity(self, quantity: float, symbol: str) -> str:
        """
        格式化数量到交易所要求的精度

        Args:
            quantity: 原始数量
            symbol: 交易对符号

        Returns:
            str: 格式化后的数量字符串
        """
        config = self.SYMBOL_CONFIG.get(symbol, {"qty_precision": 4})
        precision = config["qty_precision"]
        return f"{quantity:.{precision}f}"

    # ========== 模拟数据方法（开发测试用） ==========

    def _get_mock_ticker(self, symbol: str) -> Ticker:
        """生成模拟行情数据"""
        import random

        # 基础价格
        base_prices = {
            "BTC-USD": 95000.0,
            "ETH-USD": 3400.0,
            "SOL-USD": 180.0,
        }
        base_price = base_prices.get(symbol, 100.0)

        # 添加随机波动 (±0.5%)
        price_change = base_price * random.uniform(-0.005, 0.005)
        last_price = base_price + price_change

        # 买卖价差 (0.01%)
        spread = last_price * 0.0001

        return Ticker(
            symbol=symbol,
            last_price=last_price,
            bid_price=last_price - spread,
            ask_price=last_price + spread,
            mark_price=last_price,
            index_price=last_price,
            timestamp=int(datetime.now().timestamp() * 1000)
        )

    def _get_mock_orderbook(self, symbol: str, depth: int) -> Dict[str, Any]:
        """生成模拟订单簿数据"""
        ticker = self._get_mock_ticker(symbol)
        mid_price = ticker.last_price

        bids = []
        asks = []

        # 生成买卖盘
        for i in range(depth):
            # 买单（价格递减）
            bid_price = mid_price * (1 - 0.0001 * (i + 1))
            bid_qty = 0.1 * (i + 1)
            bids.append([bid_price, bid_qty])

            # 卖单（价格递增）
            ask_price = mid_price * (1 + 0.0001 * (i + 1))
            ask_qty = 0.1 * (i + 1)
            asks.append([ask_price, ask_qty])

        return {
            "symbol": symbol,
            "bids": bids,
            "asks": asks,
        }

    def _get_mock_balance(self, asset: Optional[str]) -> List[Balance]:
        """生成模拟余额数据"""
        if asset and asset.upper() != "DUSD":
            return []

        return [Balance(
            asset="DUSD",
            free=self._mock_balance * 0.8,  # 80% 可用
            locked=self._mock_balance * 0.2,  # 20% 锁定
            total=self._mock_balance
        )]

    def _get_mock_positions(self, symbol: Optional[str]) -> List[Position]:
        """生成模拟持仓数据"""
        positions = []

        for sym, pos_data in self._mock_positions.items():
            if symbol and sym != symbol:
                continue

            positions.append(Position(
                symbol=sym,
                side=pos_data['side'],
                size=pos_data['size'],
                entry_price=pos_data['entry_price'],
                mark_price=pos_data.get('mark_price', pos_data['entry_price']),
                unrealized_pnl=pos_data.get('unrealized_pnl', 0),
                leverage=pos_data.get('leverage', 1)
            ))

        return positions

    def _create_mock_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: Optional[float],
        **kwargs
    ) -> Order:
        """创建模拟订单"""
        import random

        order_id = str(random.randint(100000, 999999))

        # 获取当前价格
        ticker = self._get_mock_ticker(symbol)
        exec_price = price if price else ticker.last_price

        return Order(
            order_id=order_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            price=exec_price,
            quantity=quantity,
            filled_quantity=0,
            status=OrderStatus.OPEN,
            timestamp=int(datetime.now().timestamp() * 1000)
        )
