"""
StandX 交易所适配器
实现 StandX API 的统一接口
"""

import aiohttp
from typing import Dict, List, Any, Optional
from datetime import datetime

from .base import (
    ExchangeAdapter, Balance, Position, Order, Ticker,
    OrderSide, OrderType, OrderStatus
)


class StandXAdapter(ExchangeAdapter):
    """StandX 交易所适配器"""
    
    BASE_URL = "https://perps.standx.com"
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.session: Optional[aiohttp.ClientSession] = None
        self.api_key = config.get('api_key')
        self.api_secret = config.get('api_secret')
    
    async def initialize(self) -> bool:
        """初始化连接"""
        self.session = aiohttp.ClientSession()
        # StandX 使用签名认证，这里不需要暴露私钥
        return True
    
    async def close(self):
        """关闭连接"""
        if self.session:
            await self.session.close()
    
    def _get_headers(self) -> Dict[str, str]:
        """获取请求头"""
        headers = {
            "Content-Type": "application/json"
        }
        
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        
        return headers
    
    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        data: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """发送请求"""
        url = f"{self.BASE_URL}{endpoint}"
        headers = self._get_headers()
        
        async with self.session.request(
            method,
            url,
            headers=headers,
            params=params,
            json=data
        ) as response:
            response.raise_for_status()
            return await response.json()
    
    # ===== 市场数据接口实现 =====
    
    async def get_ticker(self, symbol: str) -> Ticker:
        """获取行情数据"""
        data = await self._request("GET", "/api/query_symbol_price", params={"symbol": symbol})
        
        return Ticker(
            symbol=symbol,
            last_price=float(data.get('last_price', 0)),
            bid_price=float(data.get('spread_bid', 0)),
            ask_price=float(data.get('spread_ask', 0)),
            mark_price=float(data.get('mark_price', 0)),
            index_price=float(data.get('index_price', 0)),
            timestamp=int(datetime.now().timestamp() * 1000)
        )
    
    async def get_orderbook(self, symbol: str, depth: int = 20) -> Dict[str, Any]:
        """获取订单簿"""
        data = await self._request("GET", "/api/query_depth_book", params={"symbol": symbol})
        
        return {
            "symbol": symbol,
            "bids": data.get('bids', [])[:depth],
            "asks": data.get('asks', [])[:depth]
        }
    
    # ===== 账户接口实现 =====
    
    async def get_balance(self, asset: Optional[str] = None) -> List[Balance]:
        """获取账户余额"""
        data = await self._request("GET", "/api/query_balance")
        
        # StandX 返回的是统一余额
        balances = []
        
        balance = Balance(
            asset="DUSD",
            free=float(data.get('cross_available', 0)),
            locked=float(data.get('locked', 0)),
            total=float(data.get('balance', 0))
        )
        balances.append(balance)
        
        if asset and asset != "DUSD":
            return []
        
        return balances if not asset else [b for b in balances if b.asset == asset]
    
    async def get_positions(self, symbol: Optional[str] = None) -> List[Position]:
        """获取持仓"""
        params = {"symbol": symbol} if symbol else {}
        data = await self._request("GET", "/api/query_positions", params=params)
        
        positions = []
        for pos_data in data if isinstance(data, list) else [data]:
            position = Position(
                symbol=pos_data['symbol'],
                side="long" if float(pos_data['qty']) > 0 else "short",
                size=abs(float(pos_data['qty'])),
                entry_price=float(pos_data['entry_price']),
                mark_price=float(pos_data['mark_price']),
                unrealized_pnl=float(pos_data['upnl']),
                leverage=int(pos_data['leverage'])
            )
            positions.append(position)
        
        return positions
    
    # ===== 交易接口实现 =====
    
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
        order_data = {
            "symbol": symbol,
            "side": side.value,
            "order_type": order_type.value,
            "qty": self.format_quantity(quantity, symbol),
            "time_in_force": kwargs.get("time_in_force", "gtc"),
            "reduce_only": kwargs.get("reduce_only", False)
        }
        
        if price:
            order_data["price"] = self.format_price(price, symbol)
        
        response = await self._request("POST", "/api/new_order", data=order_data)
        
        # StandX 返回的是确认信息，需要查询订单详情
        # 这里简化处理
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
        """取消订单"""
        try:
            await self._request("POST", "/api/cancel_order", data={"order_id": int(order_id)})
            return True
        except Exception:
            return False
    
    async def cancel_all_orders(self, symbol: str) -> int:
        """取消所有订单"""
        # 获取所有活跃订单
        orders = await self.get_open_orders(symbol)
        order_ids = [int(order.order_id) for order in orders]
        
        if not order_ids:
            return 0
        
        await self._request("POST", "/api/cancel_orders", data={"order_id_list": order_ids})
        return len(order_ids)
    
    async def get_order(self, symbol: str, order_id: str) -> Order:
        """查询订单"""
        data = await self._request("GET", "/api/query_order", params={"order_id": int(order_id)})
        
        return self._parse_order(data)
    
    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """获取活跃订单"""
        params = {"symbol": symbol} if symbol else {}
        data = await self._request("GET", "/api/query_open_orders", params=params)
        
        orders = []
        for order_data in data.get('result', []):
            orders.append(self._parse_order(order_data))
        
        return orders
    
    def _parse_order(self, data: Dict) -> Order:
        """解析订单数据"""
        status_map = {
            "open": OrderStatus.OPEN,
            "filled": OrderStatus.FILLED,
            "canceled": OrderStatus.CANCELLED,
            "rejected": OrderStatus.REJECTED
        }
        
        return Order(
            order_id=str(data['id']),
            symbol=data['symbol'],
            side=OrderSide.BUY if data['side'] == 'buy' else OrderSide.SELL,
            order_type=OrderType.LIMIT if data['order_type'] == 'limit' else OrderType.MARKET,
            price=float(data['price']),
            quantity=float(data['qty']),
            filled_quantity=float(data.get('fill_qty', 0)),
            status=status_map.get(data['status'], OrderStatus.PENDING),
            timestamp=int(datetime.fromisoformat(data['created_at'].replace('Z', '+00:00')).timestamp() * 1000)
        )
    
    def get_supported_symbols(self) -> List[str]:
        """获取支持的交易对列表"""
        return ["BTC-USD", "ETH-USD", "SOL-USD"]
