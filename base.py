"""
交易所适配器基类
支持多个交易平台的统一接口
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from enum import Enum


class OrderSide(Enum):
    """订单方向"""
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    """订单类型"""
    LIMIT = "limit"
    MARKET = "market"


class OrderStatus(Enum):
    """订单状态"""
    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class Balance:
    """账户余额"""
    asset: str
    free: float
    locked: float
    total: float


@dataclass
class Position:
    """持仓信息"""
    symbol: str
    side: str
    size: float
    entry_price: float
    mark_price: float
    unrealized_pnl: float
    leverage: int


@dataclass
class Order:
    """订单信息"""
    order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    price: float
    quantity: float
    filled_quantity: float
    status: OrderStatus
    timestamp: int


@dataclass
class Ticker:
    """行情信息"""
    symbol: str
    last_price: float
    bid_price: float
    ask_price: float
    mark_price: float
    index_price: float
    timestamp: int


class ExchangeAdapter(ABC):
    """交易所适配器基类"""
    
    def __init__(self, config: Dict[str, Any]):
        """
        初始化适配器
        
        Args:
            config: 配置字典，包含 API 相关配置
        """
        self.config = config
        self.exchange_name = self.__class__.__name__.replace('Adapter', '')
    
    @abstractmethod
    async def initialize(self) -> bool:
        """初始化连接"""
        pass
    
    @abstractmethod
    async def close(self):
        """关闭连接"""
        pass
    
    # ===== 市场数据接口 =====
    
    @abstractmethod
    async def get_ticker(self, symbol: str) -> Ticker:
        """
        获取行情数据
        
        Args:
            symbol: 交易对符号
            
        Returns:
            Ticker: 行情信息
        """
        pass
    
    @abstractmethod
    async def get_orderbook(self, symbol: str, depth: int = 20) -> Dict[str, Any]:
        """
        获取订单簿
        
        Args:
            symbol: 交易对符号
            depth: 深度
            
        Returns:
            Dict: 订单簿数据 {bids: [[price, qty], ...], asks: [[price, qty], ...]}
        """
        pass
    
    # ===== 账户接口 =====
    
    @abstractmethod
    async def get_balance(self, asset: Optional[str] = None) -> List[Balance]:
        """
        获取账户余额
        
        Args:
            asset: 资产名称，None 表示所有资产
            
        Returns:
            List[Balance]: 余额列表
        """
        pass
    
    @abstractmethod
    async def get_positions(self, symbol: Optional[str] = None) -> List[Position]:
        """
        获取持仓
        
        Args:
            symbol: 交易对符号，None 表示所有持仓
            
        Returns:
            List[Position]: 持仓列表
        """
        pass
    
    # ===== 交易接口 =====
    
    @abstractmethod
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
            side: 订单方向
            order_type: 订单类型
            quantity: 数量
            price: 价格（限价单必需）
            **kwargs: 其他参数（如 reduce_only, time_in_force 等）
            
        Returns:
            Order: 订单信息
        """
        pass
    
    @abstractmethod
    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """
        取消订单
        
        Args:
            symbol: 交易对符号
            order_id: 订单 ID
            
        Returns:
            bool: 是否成功
        """
        pass
    
    @abstractmethod
    async def cancel_all_orders(self, symbol: str) -> int:
        """
        取消所有订单
        
        Args:
            symbol: 交易对符号
            
        Returns:
            int: 取消的订单数量
        """
        pass
    
    @abstractmethod
    async def get_order(self, symbol: str, order_id: str) -> Order:
        """
        查询订单
        
        Args:
            symbol: 交易对符号
            order_id: 订单 ID
            
        Returns:
            Order: 订单信息
        """
        pass
    
    @abstractmethod
    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """
        获取活跃订单
        
        Args:
            symbol: 交易对符号，None 表示所有交易对
            
        Returns:
            List[Order]: 订单列表
        """
        pass
    
    # ===== 工具方法 =====
    
    def get_supported_symbols(self) -> List[str]:
        """获取支持的交易对列表"""
        return []
    
    def validate_symbol(self, symbol: str) -> bool:
        """验证交易对是否支持"""
        supported = self.get_supported_symbols()
        if not supported:
            return True  # 如果未实现，默认允许
        return symbol in supported
    
    def format_price(self, price: float, symbol: str) -> str:
        """格式化价格到交易所要求的精度"""
        # 子类可以重写此方法
        return f"{price:.8f}".rstrip('0').rstrip('.')
    
    def format_quantity(self, quantity: float, symbol: str) -> str:
        """格式化数量到交易所要求的精度"""
        # 子类可以重写此方法
        return f"{quantity:.8f}".rstrip('0').rstrip('.')
