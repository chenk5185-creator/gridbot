#!/usr/bin/env python3
"""
SIWE 签名调试工具
================

用于调试 StandX JWT Token 获取流程中的 SIWE 签名问题。

分析两种签名方式：
1. 签名整个 signedData (JWT Token) - get_jwt_token.py 当前的实现
2. 签名 payload.message (SIWE 消息) - 官方文档的实现

使用方法:
    python debug_siwe.py <wallet_address>
"""

import requests
import json
import base64
import sys


API_BASE = "https://api.standx.com/v1/offchain"
CHAIN = "bsc"


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
        raise ValueError(f"无效的 JWT 格式，应有 3 部分，实际有 {len(parts)} 部分")

    payload_b64 = parts[1]
    payload_bytes = base64url_decode(payload_b64)
    payload_str = payload_bytes.decode('utf-8')
    return json.loads(payload_str)


def analyze_siwe_message(message: str) -> dict:
    """分析 SIWE 消息格式"""
    result = {
        "raw_message": message,
        "length": len(message),
        "byte_length": len(message.encode('utf-8')),
        "lines": message.split('\n'),
        "line_count": len(message.split('\n')),
    }

    # 检查 SIWE 标准字段
    siwe_fields = ['URI:', 'Version:', 'Chain ID:', 'Nonce:', 'Issued At:']
    for field in siwe_fields:
        result[f"has_{field.lower().replace(':', '').replace(' ', '_')}"] = field in message

    return result


def main():
    print("=" * 70)
    print("  SIWE 签名调试工具")
    print("=" * 70)
    print()

    # 获取钱包地址
    if len(sys.argv) > 1:
        wallet_address = sys.argv[1]
    else:
        print("请输入钱包地址:")
        wallet_address = input().strip()

    if not wallet_address:
        print("错误: 需要钱包地址")
        return

    print(f"钱包地址: {wallet_address}")
    print(f"链: {CHAIN}")
    print()

    # Step 1: 调用 prepare-signin 获取 signedData
    print("-" * 70)
    print("步骤 1: 调用 prepare-signin")
    print("-" * 70)

    # 使用地址前缀作为 requestId（与 get_jwt_token.py 一致）
    request_id = wallet_address[:20]

    prepare_url = f"{API_BASE}/prepare-signin?chain={CHAIN}"
    prepare_data = {
        "address": wallet_address,
        "requestId": request_id
    }

    print(f"请求 URL: {prepare_url}")
    print(f"请求体: {json.dumps(prepare_data, indent=2)}")
    print()

    try:
        response = requests.post(prepare_url, json=prepare_data, timeout=30)
        print(f"状态码: {response.status_code}")

        if response.status_code != 200:
            print(f"错误响应: {response.text}")
            return

        prepare_result = response.json()
        signed_data = prepare_result.get("signedData")

        if not signed_data:
            print("错误: 响应中没有 signedData")
            print(f"完整响应: {json.dumps(prepare_result, indent=2)}")
            return

        print(f"signedData (JWT Token):")
        print(f"  前 50 字符: {signed_data[:50]}...")
        print(f"  长度: {len(signed_data)}")
        print()

    except Exception as e:
        print(f"请求失败: {e}")
        return

    # Step 2: 解析 JWT Token
    print("-" * 70)
    print("步骤 2: 解析 JWT Token")
    print("-" * 70)

    try:
        payload = parse_jwt(signed_data)
        print("JWT Payload 内容:")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        print()

        message = payload.get("message")
        if not message:
            print("警告: JWT payload 中没有 'message' 字段！")
            print("可用字段:", list(payload.keys()))
            return

    except Exception as e:
        print(f"JWT 解析失败: {e}")
        return

    # Step 3: 分析 SIWE 消息
    print("-" * 70)
    print("步骤 3: 分析 SIWE 消息 (payload.message)")
    print("-" * 70)

    siwe_analysis = analyze_siwe_message(message)

    print(f"消息长度: {siwe_analysis['length']} 字符")
    print(f"消息字节长度: {siwe_analysis['byte_length']} 字节")
    print(f"行数: {siwe_analysis['line_count']}")
    print()

    print("SIWE 标准字段检查:")
    for key, value in siwe_analysis.items():
        if key.startswith('has_'):
            field_name = key.replace('has_', '').replace('_', ' ').title()
            status = "✅" if value else "❌"
            print(f"  {status} {field_name}")
    print()

    print("完整 SIWE 消息:")
    print("-" * 40)
    print(message)
    print("-" * 40)
    print()

    # Step 4: 比较两种签名方式
    print("-" * 70)
    print("步骤 4: 签名内容对比")
    print("-" * 70)

    print("方式 1 - 签名整个 signedData (get_jwt_token.py 的做法):")
    print(f"  内容: JWT Token 字符串")
    print(f"  长度: {len(signed_data)} 字符")
    print(f"  前 50 字符: {signed_data[:50]}...")
    print()

    print("方式 2 - 签名 payload.message (官方文档的做法):")
    print(f"  内容: SIWE 格式的消息")
    print(f"  长度: {len(message)} 字符")
    print(f"  前 100 字符: {message[:100]}...")
    print()

    # Step 5: 输出结论
    print("-" * 70)
    print("结论")
    print("-" * 70)

    print("""
根据 SIWE (Sign-In with Ethereum) 标准 (EIP-4361):
- 签名的内容应该是 SIWE 格式的消息（包含 domain, chainId, nonce 等）
- 而不是 JWT Token 本身

从上面的分析可以看出:
- signedData 是一个 JWT Token (格式: xxx.xxx.xxx)
- payload.message 是 SIWE 格式的消息 (包含 URI, Version, Chain ID 等)

正确的签名流程应该是:
1. 调用 prepare-signin 获取 signedData (JWT Token)
2. 解析 JWT 获取 payload.message (SIWE 消息)
3. 用钱包签名 payload.message
4. 发送 {signature, signedData, expiresSeconds} 到 login

get_jwt_token.py 的实现是错误的，它签名的是整个 JWT Token。
""")

    # Step 6: 提供修复建议
    print("-" * 70)
    print("修复建议")
    print("-" * 70)

    print("""
修复 get_jwt_token.py 中的签名逻辑:

原代码 (错误):
    message = encode_defunct(text=signed_data)  # 签名整个 JWT
    signed_message = account.sign_message(message)

修复后 (正确):
    # 解析 JWT 获取 SIWE 消息
    payload = parse_jwt(signed_data)
    siwe_message = payload["message"]

    # 签名 SIWE 消息
    message = encode_defunct(text=siwe_message)
    signed_message = account.sign_message(message)
""")

    print()
    print("调试完成！")


if __name__ == "__main__":
    main()
