#!/usr/bin/env python3
"""
StandX API 测试脚本
==================

用于验证 StandX API 连接是否正常工作。

使用方法:
    python test_standx_api.py YOUR_JWT_TOKEN

测试内容:
    1. 获取账户余额 (/api/query_balance)
    2. 获取持仓信息 (/api/query_positions)
    3. 获取交易对价格 (通过 WebSocket)
"""

import asyncio
import aiohttp
import json
import sys
from datetime import datetime


# StandX API 配置
BASE_URL = "https://perps.standx.com"
WS_URL = "wss://perps.standx.com/ws-stream/v1"


async def test_balance(session: aiohttp.ClientSession, jwt_token: str):
    """测试获取账户余额"""
    print("\n" + "=" * 50)
    print("📊 测试 1: 获取账户余额")
    print("=" * 50)

    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Content-Type": "application/json"
    }

    try:
        async with session.get(
            f"{BASE_URL}/api/query_balance",
            headers=headers
        ) as response:
            print(f"状态码: {response.status}")
            data = await response.text()

            if response.status == 200:
                result = json.loads(data)
                print("✅ 成功!")
                print(f"响应数据: {json.dumps(result, indent=2, ensure_ascii=False)}")
                return True, result
            else:
                print(f"❌ 失败: {data}")
                return False, data

    except Exception as e:
        print(f"❌ 错误: {e}")
        return False, str(e)


async def test_positions(session: aiohttp.ClientSession, jwt_token: str):
    """测试获取持仓信息"""
    print("\n" + "=" * 50)
    print("📈 测试 2: 获取持仓信息")
    print("=" * 50)

    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Content-Type": "application/json"
    }

    try:
        async with session.get(
            f"{BASE_URL}/api/query_positions",
            headers=headers
        ) as response:
            print(f"状态码: {response.status}")
            data = await response.text()

            if response.status == 200:
                result = json.loads(data)
                print("✅ 成功!")
                print(f"响应数据: {json.dumps(result, indent=2, ensure_ascii=False)}")
                return True, result
            else:
                print(f"❌ 失败: {data}")
                return False, data

    except Exception as e:
        print(f"❌ 错误: {e}")
        return False, str(e)


async def test_websocket_price():
    """测试 WebSocket 获取价格"""
    print("\n" + "=" * 50)
    print("💹 测试 3: WebSocket 获取 BTC 价格")
    print("=" * 50)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(WS_URL) as ws:
                # 订阅 BTC-USD 价格
                subscribe_msg = {
                    "method": "subscribe",
                    "params": {
                        "channel": "price",
                        "symbol": "BTC-USD"
                    }
                }
                await ws.send_json(subscribe_msg)
                print("已发送订阅请求...")

                # 等待接收消息（最多 5 秒）
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=5.0)
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        print("✅ 成功!")
                        print(f"响应数据: {json.dumps(data, indent=2, ensure_ascii=False)}")
                        return True, data
                    else:
                        print(f"收到非文本消息: {msg.type}")
                        return False, str(msg.type)
                except asyncio.TimeoutError:
                    print("⚠️ 超时，未收到价格数据（可能需要等待更长时间）")
                    return False, "timeout"

    except Exception as e:
        print(f"❌ 错误: {e}")
        return False, str(e)


async def test_public_endpoints(session: aiohttp.ClientSession):
    """测试公开端点（不需要认证）"""
    print("\n" + "=" * 50)
    print("🌐 测试 4: 公开端点")
    print("=" * 50)

    # 尝试不同的可能端点
    endpoints = [
        "/api/symbols",
        "/api/query_symbol_price?symbol=BTC-USD",
        "/api/query_depth_book?symbol=BTC-USD",
        "/api/v1/symbols",
        "/v1/symbols",
    ]

    for endpoint in endpoints:
        try:
            async with session.get(f"{BASE_URL}{endpoint}") as response:
                data = await response.text()
                print(f"\n{endpoint}:")
                print(f"  状态码: {response.status}")
                if response.status == 200:
                    try:
                        result = json.loads(data)
                        print(f"  ✅ 成功: {json.dumps(result, indent=2, ensure_ascii=False)[:200]}...")
                    except:
                        print(f"  响应: {data[:200]}...")
                else:
                    print(f"  响应: {data[:100]}...")
        except Exception as e:
            print(f"\n{endpoint}: ❌ {e}")


async def main():
    """主函数"""
    print("\n" + "🚀 " + "=" * 46 + " 🚀")
    print("       StandX API 测试工具")
    print("🚀 " + "=" * 46 + " 🚀")

    # 检查命令行参数
    if len(sys.argv) < 2:
        print("\n⚠️  使用方法: python test_standx_api.py YOUR_JWT_TOKEN")
        print("\n如何获取 JWT Token:")
        print("  1. 打开 https://standx.com")
        print("  2. 连接 MetaMask 钱包")
        print("  3. 按 F12 打开开发者工具")
        print("  4. 在 Network 标签中找到 login 请求")
        print("  5. 复制响应中的 token 字段")
        print("\n现在将只测试公开端点...")
        jwt_token = None
    else:
        jwt_token = sys.argv[1]
        print(f"\nJWT Token: {jwt_token[:20]}...{jwt_token[-10:]}")

    print(f"测试时间: {datetime.now().isoformat()}")

    # 创建 HTTP 会话
    async with aiohttp.ClientSession() as session:
        # 测试公开端点
        await test_public_endpoints(session)

        if jwt_token:
            # 测试认证端点
            await test_balance(session, jwt_token)
            await test_positions(session, jwt_token)

    # 测试 WebSocket
    await test_websocket_price()

    print("\n" + "=" * 50)
    print("📋 测试完成!")
    print("=" * 50)
    print("\n请将上面的输出结果发给我，我会根据实际 API 响应调整代码。")


if __name__ == "__main__":
    asyncio.run(main())
