#!/usr/bin/env python3
"""
StandX JWT Token 获取工具
========================

根据 StandX 官方文档实现：
https://docs.standx.com/standx-api/perps-auth

使用方法:
    python get_jwt_token.py

流程:
    1. 调用 prepare-signin 获取 signedData (JWT Token)
    2. 解析 JWT 获取 payload.message (SIWE 消息)
    3. 使用钱包签名 SIWE 消息
    4. 调用 login 获取 JWT Token
"""

import requests
import json
import base64
from eth_account import Account
from eth_account.messages import encode_defunct


# StandX API 配置
API_BASE = "https://api.standx.com/v1/offchain"
CHAIN = "bsc"  # 或 "solana"


def base64url_decode(data: str) -> bytes:
    """解码 base64url（JWT 使用的编码格式）"""
    # 替换 URL 安全字符为标准 base64 字符
    data = data.replace('-', '+').replace('_', '/')
    # 添加填充
    padding = 4 - len(data) % 4
    if padding != 4:
        data += '=' * padding
    return base64.b64decode(data)


def parse_jwt(token: str) -> dict:
    """解析 JWT Token，提取 payload"""
    parts = token.split('.')
    if len(parts) != 3:
        raise ValueError(f"无效的 JWT 格式")
    payload_bytes = base64url_decode(parts[1])
    return json.loads(payload_bytes.decode('utf-8'))


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
    # 重要：requestId 应该是 UUID，不是钱包地址或 Ed25519 公钥
    # Ed25519 密钥仅用于后续 API 请求签名，与 SIWE 登录无关
    print("步骤 1: 调用 prepare-signin...")
    import uuid
    siwe_request_id = str(uuid.uuid4())  # 使用 UUID，与官方示例一致

    prepare_url = f"{API_BASE}/prepare-signin?chain={CHAIN}"
    prepare_data = {
        "address": address,
        "requestId": siwe_request_id  # UUID，非钱包地址
    }
    print(f"SIWE RequestId (UUID): {siwe_request_id}")

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

    # Step 2: 解析 JWT Token，获取 SIWE 消息
    print()
    print("步骤 2: 解析 JWT 获取 SIWE 消息...")

    try:
        payload = parse_jwt(signed_data)
        print(f"JWT Payload: {json.dumps(payload, indent=2, ensure_ascii=False)}")

        siwe_message = payload.get("message")
        if not siwe_message:
            print("错误: JWT payload 中没有 'message' 字段")
            return None

        print(f"SIWE 消息长度: {len(siwe_message)} 字符")
        print(f"SIWE 消息内容:\n{siwe_message}")
    except Exception as e:
        print(f"JWT 解析错误: {e}")
        return None

    # Step 3: 签名 SIWE 消息（不是整个 JWT Token！）
    print()
    print("步骤 3: 签名 SIWE 消息...")

    # 重要：签名的是 payload.message (SIWE 消息)，而不是整个 signedData (JWT Token)
    message = encode_defunct(text=siwe_message)
    signed_message = account.sign_message(message)
    # 注意：StandX 需要带 0x 前缀的签名
    signature = "0x" + signed_message.signature.hex()

    print(f"签名: {signature[:50]}...")
    print(f"签名长度: {len(signature)}")

    # Step 4: 调用 login 获取 JWT Token
    print()
    print("步骤 4: 调用 login 获取 JWT Token...")
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
