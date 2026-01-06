#!/usr/bin/env python3
"""
StandX JWT Token 获取工具
========================

根据 StandX 官方文档实现：
https://docs.standx.com/standx-api/perps-auth

使用方法:
    python get_jwt_token.py

流程:
    1. 调用 prepare-signin 获取待签名消息
    2. 使用钱包签名消息
    3. 调用 login 获取 JWT Token
"""

import requests
import json
from eth_account import Account
from eth_account.messages import encode_defunct


# StandX API 配置
API_BASE = "https://api.standx.com/v1/offchain"
CHAIN = "bsc"  # 或 "solana"


def get_jwt_token_with_private_key(private_key: str) -> dict:
    """
    使用私钥获取 JWT Token

    Args:
        private_key: 以太坊私钥（以 0x 开头）

    Returns:
        包含 token 的响应
    """
    # 从私钥获取账户
    account = Account.from_key(private_key)
    address = account.address

    print(f"钱包地址: {address}")
    print(f"链: {CHAIN}")
    print()

    # Step 1: 调用 prepare-signin
    print("步骤 1: 调用 prepare-signin...")
    prepare_url = f"{API_BASE}/prepare-signin?chain={CHAIN}"
    prepare_data = {
        "address": address,
        "requestId": address[:20]  # 使用地址前缀作为 requestId
    }

    response = requests.post(prepare_url, json=prepare_data)
    print(f"状态码: {response.status_code}")

    if response.status_code != 200:
        print(f"错误: {response.text}")
        return None

    prepare_result = response.json()
    print(f"响应: {json.dumps(prepare_result, indent=2)}")

    signed_data = prepare_result.get("signedData")
    if not signed_data:
        print("错误: 未获取到 signedData")
        return None

    # Step 2: 签名消息
    print()
    print("步骤 2: 签名消息...")

    # 根据文档，需要签名 signedData
    message = encode_defunct(text=signed_data)
    signed_message = account.sign_message(message)
    signature = signed_message.signature.hex()

    print(f"签名: {signature[:50]}...")

    # Step 3: 调用 login 获取 JWT Token
    print()
    print("步骤 3: 调用 login 获取 JWT Token...")
    login_url = f"{API_BASE}/login?chain={CHAIN}"
    login_data = {
        "signature": signature,
        "signedData": signed_data,
        "expiresSeconds": 604800  # 7 天
    }

    response = requests.post(login_url, json=login_data)
    print(f"状态码: {response.status_code}")

    if response.status_code != 200:
        print(f"错误: {response.text}")
        return None

    login_result = response.json()
    print()
    print("=" * 60)
    print("✅ JWT Token 获取成功!")
    print("=" * 60)
    print()
    print(f"Token: {login_result.get('token', 'N/A')}")
    print()

    return login_result


def main():
    print()
    print("=" * 60)
    print("  StandX JWT Token 获取工具")
    print("  官方文档: https://docs.standx.com/standx-api/perps-auth")
    print("=" * 60)
    print()

    print("⚠️  注意: 此脚本需要你的钱包私钥")
    print("   私钥仅在本地使用，不会发送到任何服务器")
    print("   如果你不信任此脚本，可以手动调用 API")
    print()

    # 方式 1: 从环境变量获取
    import os
    private_key = os.environ.get("PRIVATE_KEY")

    if not private_key:
        # 方式 2: 从命令行输入
        print("请输入你的钱包私钥（以 0x 开头）:")
        private_key = input().strip()

    if not private_key:
        print("错误: 未提供私钥")
        return

    if not private_key.startswith("0x"):
        private_key = "0x" + private_key

    try:
        result = get_jwt_token_with_private_key(private_key)

        if result and result.get("token"):
            print()
            print("📋 复制上面的 Token 到网格交易工具中使用")
            print()
    except Exception as e:
        print(f"错误: {e}")
        print()
        print("如果出错，请检查:")
        print("1. 私钥格式是否正确")
        print("2. 网络连接是否正常")
        print("3. 钱包地址是否已在 StandX 注册")


if __name__ == "__main__":
    main()
