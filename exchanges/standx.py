"""
StandX 交易所适配器
==================

StandX 是一个去中心化永续合约交易平台，使用 DUSD（生息稳定币）作为保证金。

API 文档: https://docs.standx.com/standx-api/perps-http

认证方式:
- 公开接口：无需认证
- 私有接口：需要 JWT Token + Ed25519 请求签名

费率:
- Maker: 0.01%
- Taker: 0.04%
"""

import aiohttp
import time
import uuid
import json
import base64
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
            'jwt_token': 'your_jwt_token',
            'signing_key': 'base64_encoded_ed25519_private_key',
        }
        adapter = StandXAdapter(config)
        await adapter.initialize()
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
                - jwt_token: JWT 认证令牌
                - signing_key: Ed25519 私钥（base64 编码）
                - request_id: 请求 ID（base58 编码的公钥）
                - simulation: 是否为模拟模式（默认 False）
        """
        super().__init__(config)

        # HTTP 会话
        self.session: Optional[aiohttp.ClientSession] = None

        # JWT 认证令牌
        self.jwt_token: Optional[str] = config.get('jwt_token')

        # Ed25519 签名密钥
        self.signing_key: Optional[bytes] = None
        self.request_id: Optional[str] = config.get('request_id')

        # 解析 signing_key（如果提供）
        signing_key_b64 = config.get('signing_key')
        if signing_key_b64:
            try:
                self.signing_key = base64.b64decode(signing_key_b64)
            except Exception as e:
                print(f"解析 signing_key 失败: {e}")

        # 模拟模式标志
        self.simulation = config.get('simulation', False)

        # 模拟数据（开发测试用）
        self._mock_balance = 10000.0
        self._mock_positions: Dict[str, Dict] = {}

    @staticmethod
    def generate_keypair() -> tuple:
        """
        生成 Ed25519 密钥对

        Returns:
            tuple: (private_key_base64, public_key_base58, request_id)
        """
        try:
            from nacl.signing import SigningKey
            import base58

            # 生成密钥对
            signing_key = SigningKey.generate()
            private_key = signing_key.encode()
            public_key = signing_key.verify_key.encode()

            # 编码
            private_key_b64 = base64.b64encode(private_key).decode()
            public_key_b58 = base58.b58encode(public_key).decode()

            return private_key_b64, public_key_b58, public_key_b58
        except ImportError:
            print("请安装 pynacl 和 base58: pip install pynacl base58")
            return None, None, None

    def _sign_request(self, version: str, request_id: str,
                      timestamp: int, payload: str) -> Optional[str]:
        """
        使用 Ed25519 签名请求

        签名消息格式: "{version},{request_id},{timestamp},{payload}"

        Args:
            version: 签名版本（v1）
            request_id: 请求 ID
            timestamp: 时间戳（毫秒）
            payload: 请求体 JSON 字符串

        Returns:
            str: Base64 编码的签名，失败返回 None
        """
        if not self.signing_key:
            return None

        try:
            from nacl.signing import SigningKey

            # 构建签名消息
            sign_msg = f"{version},{request_id},{timestamp},{payload}"
            message_bytes = sign_msg.encode('utf-8')

            # 使用私钥签名
            signing_key = SigningKey(self.signing_key)
            signed = signing_key.sign(message_bytes)

            # Base64 编码签名（只取签名部分，不包含消息）
            signature = base64.b64encode(signed.signature).decode()
            return signature

        except Exception as e:
            print(f"签名失败: {e}")
            return None

    async def initialize(self) -> bool:
        """
        初始化连接

        Returns:
            bool: 初始化是否成功
        """
        connector = aiohttp.TCPConnector(
            limit=100,
            limit_per_host=30,
        )
        self.session = aiohttp.ClientSession(connector=connector)
        return True

    async def close(self):
        """关闭连接"""
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

        # 添加请求签名
        # 参考官方文档: https://docs.standx.com/standx-api/perps-auth
        if need_sign and self.signing_key:
            # x-request-id 是每次请求生成的唯一 UUID，不是 Ed25519 公钥
            request_id = str(uuid.uuid4())
            timestamp = int(time.time() * 1000)  # 毫秒时间戳
            payload = json.dumps(body, separators=(',', ':')) if body else ""

            # 计算签名: sign("{version},{request_id},{timestamp},{payload}")
            signature = self._sign_request("v1", request_id, timestamp, payload)

            if signature:
                headers.update({
                    "x-request-sign-version": "v1",
                    "x-request-id": request_id,
                    "x-request-timestamp": str(timestamp),
                    "x-request-signature": signature,
                })

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
            method: HTTP 方法
            endpoint: API 端点
            params: URL 查询参数
            data: 请求体数据
            need_auth: 是否需要认证
            need_sign: 是否需要签名

        Returns:
            Dict: API 响应

        Raises:
            ValueError: 请求失败
        """
        url = f"{self.BASE_URL}{endpoint}"
        headers = self._get_headers(need_auth, need_sign, data)

        try:
            async with self.session.request(
                method,
                url,
                headers=headers,
                params=params,
                json=data,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                response_text = await response.text()

                if response.status >= 400:
                    raise ValueError(f"API 错误 [{response.status}]: {response_text}")

                if response_text:
                    return json.loads(response_text)
                return {}

        except aiohttp.ClientError as e:
            raise ValueError(f"网络请求失败: {str(e)}")
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON 解析失败: {str(e)}")

    # ========== 市场数据接口 ==========

    async def get_ticker(self, symbol: str) -> Ticker:
        """获取实时行情"""
        if self.simulation:
            return self._get_mock_ticker(symbol)

        try:
            data = await self._request(
                "GET",
                "/api/query_symbol_price",
                params={"symbol": symbol}
            )

            return Ticker(
                symbol=symbol,
                last_price=float(data.get('last_price', 0)),
                bid_price=float(data.get('spread_bid', 0)),
                ask_price=float(data.get('spread_ask', 0)),
                mark_price=float(data.get('mark_price', 0)),
                index_price=float(data.get('index_price', 0)),
                timestamp=int(datetime.now().timestamp() * 1000)
            )
        except Exception as e:
            print(f"获取行情失败: {e}")
            # 返回默认值而不是崩溃
            return self._get_mock_ticker(symbol)

    async def get_orderbook(self, symbol: str, depth: int = 20) -> Dict[str, Any]:
        """获取订单簿深度"""
        if self.simulation:
            return self._get_mock_orderbook(symbol, depth)

        try:
            data = await self._request(
                "GET",
                "/api/query_depth_book",
                params={"symbol": symbol}
            )

            return {
                "symbol": symbol,
                "bids": data.get('bids', [])[:depth],
                "asks": data.get('asks', [])[:depth],
            }
        except Exception as e:
            print(f"获取订单簿失败: {e}")
            return self._get_mock_orderbook(symbol, depth)

    # ========== 账户接口 ==========

    async def get_balance(self, asset: Optional[str] = None) -> List[Balance]:
        """获取账户余额"""
        if self.simulation:
            return self._get_mock_balance(asset)

        try:
            data = await self._request(
                "GET",
                "/api/query_balance",
                need_auth=True
            )

            balance = Balance(
                asset="DUSD",
                free=float(data.get('cross_available', 0)),
                locked=float(data.get('locked', 0)),
                total=float(data.get('balance', 0))
            )

            if asset and asset.upper() != "DUSD":
                return []

            return [balance]
        except Exception as e:
            print(f"获取余额失败: {e}")
            return self._get_mock_balance(asset)

    async def get_positions(self, symbol: Optional[str] = None) -> List[Position]:
        """获取持仓信息"""
        if self.simulation:
            return self._get_mock_positions(symbol)

        try:
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
                if qty == 0:
                    continue

                position = Position(
                    symbol=pos_data['symbol'],
                    side="long" if qty > 0 else "short",
                    size=abs(qty),
                    entry_price=float(pos_data.get('entry_price', 0)),
                    mark_price=float(pos_data.get('mark_price', 0)),
                    unrealized_pnl=float(pos_data.get('upnl', 0)),
                    leverage=int(pos_data.get('leverage', 1))
                )
                positions.append(position)

            return positions
        except Exception as e:
            print(f"获取持仓失败: {e}")
            return []

    # ========== 交易接口 ==========

    async def create_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: Optional[float] = None,
        **kwargs
    ) -> Order:
        """创建订单"""
        if self.simulation:
            return self._create_mock_order(symbol, side, order_type, quantity, price, **kwargs)

        # 检查签名密钥
        if not self.signing_key:
            raise ValueError("需要 Ed25519 签名密钥才能下单。请重新获取 JWT Token。")

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

        if order_type == OrderType.LIMIT:
            if price is None:
                raise ValueError("限价单必须指定价格")
            order_data["price"] = self.format_price(price, symbol)

        try:
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
        except Exception as e:
            raise ValueError(f"创建订单失败: {e}")

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """取消订单"""
        if self.simulation:
            return True

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
        """取消所有订单"""
        if self.simulation:
            return 0

        try:
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
        except Exception as e:
            print(f"取消所有订单失败: {e}")
            return 0

    async def get_order(self, symbol: str, order_id: str) -> Order:
        """查询订单详情"""
        if self.simulation:
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

        try:
            data = await self._request(
                "GET",
                "/api/query_order",
                params={"order_id": int(order_id)},
                need_auth=True
            )

            return self._parse_order(data)
        except Exception as e:
            raise ValueError(f"查询订单失败: {e}")

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """获取活跃订单"""
        if self.simulation:
            return []

        try:
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
        except Exception as e:
            print(f"获取活跃订单失败: {e}")
            return []

    def _parse_order(self, data: Dict) -> Order:
        """解析订单数据"""
        status_map = {
            "open": OrderStatus.OPEN,
            "filled": OrderStatus.FILLED,
            "canceled": OrderStatus.CANCELLED,
            "cancelled": OrderStatus.CANCELLED,
            "rejected": OrderStatus.REJECTED,
            "pending": OrderStatus.PENDING,
        }

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
        """格式化价格"""
        config = self.SYMBOL_CONFIG.get(symbol, {"price_precision": 2})
        precision = config["price_precision"]
        return f"{price:.{precision}f}"

    def format_quantity(self, quantity: float, symbol: str) -> str:
        """格式化数量"""
        config = self.SYMBOL_CONFIG.get(symbol, {"qty_precision": 4})
        precision = config["qty_precision"]
        return f"{quantity:.{precision}f}"

    # ========== 模拟数据方法 ==========

    def _get_mock_ticker(self, symbol: str) -> Ticker:
        """生成模拟行情"""
        import random

        base_prices = {
            "BTC-USD": 95000.0,
            "ETH-USD": 3400.0,
            "SOL-USD": 180.0,
        }
        base_price = base_prices.get(symbol, 100.0)
        price_change = base_price * random.uniform(-0.005, 0.005)
        last_price = base_price + price_change
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
        """生成模拟订单簿"""
        ticker = self._get_mock_ticker(symbol)
        mid_price = ticker.last_price

        bids = []
        asks = []

        for i in range(depth):
            bid_price = mid_price * (1 - 0.0001 * (i + 1))
            bid_qty = 0.1 * (i + 1)
            bids.append([bid_price, bid_qty])

            ask_price = mid_price * (1 + 0.0001 * (i + 1))
            ask_qty = 0.1 * (i + 1)
            asks.append([ask_price, ask_qty])

        return {"symbol": symbol, "bids": bids, "asks": asks}

    def _get_mock_balance(self, asset: Optional[str]) -> List[Balance]:
        """生成模拟余额"""
        if asset and asset.upper() != "DUSD":
            return []

        return [Balance(
            asset="DUSD",
            free=self._mock_balance * 0.8,
            locked=self._mock_balance * 0.2,
            total=self._mock_balance
        )]

    def _get_mock_positions(self, symbol: Optional[str]) -> List[Position]:
        """生成模拟持仓"""
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
