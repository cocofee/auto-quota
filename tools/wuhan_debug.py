"""
调试脚本：分析武汉住建局列表页的HTML结构
查看列表数据是静态还是JS动态加载
"""
import requests
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')

url = 'https://zgj.wuhan.gov.cn/xxgk/xxgkml/sjfb/zyjzcljgjc/index.shtml'
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
resp = requests.get(url, headers=headers, timeout=30)

# 检测编码
charset_match = re.search(rb'charset=["\']?([a-zA-Z0-9_-]+)', resp.content[:2000])
if charset_match:
    charset = charset_match.group(1).decode('ascii')
    text = resp.content.decode(charset)
    print(f"检测到编码: {charset}")
else:
    text = resp.content.decode('utf-8')
    print("使用默认utf-8编码")

# 1. 查找页面中所有.shtml链接
shtml_links = re.findall(r'href=["\']([^"\']*\.shtml)["\']', text)
print(f"\n页面中.shtml链接数: {len(shtml_links)}")
for l in shtml_links[:30]:
    print(f"  {l}")

# 2. 查找script标签中的数据加载逻辑
scripts = re.findall(r'<script[^>]*>(.*?)</script>', text, re.DOTALL)
print(f"\n页面中script标签数: {len(scripts)}")
for i, s in enumerate(scripts):
    s_stripped = s.strip()
    if not s_stripped:
        continue
    if any(kw in s_stripped.lower() for kw in ['list', 'data', 'ajax', 'fetch', 'url', 'load', 'page']):
        print(f"\n--- script [{i}] (前800字) ---")
        print(s_stripped[:800])

# 3. 查找是否有JSON数据或API端点
api_patterns = re.findall(r'["\']([^"\']*(?:api|json|data|list)[^"\']*)["\']', text, re.IGNORECASE)
print(f"\n可能的API/数据端点: {len(api_patterns)}")
for p in api_patterns[:20]:
    print(f"  {p}")

# 4. 查找包含"价格"或"综合"的文本片段
price_sections = re.findall(r'.{0,100}(?:价格|综合).{0,100}', text)
print(f"\n包含'价格'或'综合'的文本片段: {len(price_sections)}")
for p in price_sections[:10]:
    clean = re.sub(r'\s+', ' ', p).strip()
    print(f"  {clean[:150]}")

# 5. 打印页面中部区域（可能是列表区域）
print(f"\n页面总长度: {len(text)} 字符")
# 找到main或content区域
main_match = re.search(r'(class=["\'][^"\']*(?:list|content|main|body)[^"\']*["\'])', text)
if main_match:
    start = main_match.start()
    print(f"\n列表区域附近内容（从位置{start}开始，1500字）：")
    print(text[start:start+1500])
