"""
广材网连通性测试（不需要Excel，直接搜一条材料看看）

用法：
    python tools/test_gldjc.py --cookie "你的cookie字符串"
    python tools/test_gldjc.py --cookie "token=bearer xxx; other=yyy"
"""

import sys
import re
import argparse
from pathlib import Path

# 加载项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.gldjc_price import search_material_web, _get_headers, load_cache, check_cache
import requests


def main():
    parser = argparse.ArgumentParser(description="广材网连通性测试")
    parser.add_argument("--cookie", required=True, help="广材网cookie字符串")
    parser.add_argument("--keyword", default="镀锌钢管", help="搜索关键词（默认：镀锌钢管）")
    args = parser.parse_args()

    # 创建session
    session = requests.Session()
    for part in re.split(r";\s*", args.cookie):
        if "=" in part:
            key, value = part.split("=", 1)
            session.cookies.set(key.strip(), value.strip())

    print(f"测试搜索: {args.keyword}")
    print(f"请求头UA: {_get_headers()['User-Agent'][:60]}...")
    print()

    results = search_material_web(session, args.keyword)

    if results:
        print(f"搜到 {len(results)} 条结果:")
        for i, r in enumerate(results[:5]):
            print(f"  [{i+1}] {r.get('spec', '')[:40]}  {r.get('market_price', '?')} {r.get('unit', '')}")
        print()
        print("连通正常，防封措施有效")
    else:
        print("返回0条结果，可能原因：")
        print("  1. Cookie已过期 → 重新登录广材网复制Cookie")
        print("  2. 被限制 → 等一会儿再试")
        print("  3. 关键词无结果 → 换个关键词试试")


if __name__ == "__main__":
    main()
