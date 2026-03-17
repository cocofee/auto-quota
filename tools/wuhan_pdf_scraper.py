"""
武汉住建局"建设工程综合价格信息"PDF链接批量抓取
网站: https://zgj.wuhan.gov.cn/xxgk/xxgkml/sjfb/zyjzcljgjc/
共6页，每页约20条记录

关键修复：用a标签的title属性匹配（页面文本可能被CSS截断）
"""
import requests
from bs4 import BeautifulSoup
import re
import time
import sys

sys.stdout.reconfigure(encoding='utf-8')

BASE_URL = "https://zgj.wuhan.gov.cn"
LIST_BASE = "/xxgk/xxgkml/sjfb/zyjzcljgjc/"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

# 排除关键词（标题中含有这些就跳过）
EXCLUDE_KEYWORDS = ['监测简报', '造价指标', '人工成本', '苗木', '租赁', '监测情况']
# 目标关键词
TARGET_KEYWORD = '建设工程综合价格信息'

def get_page_url(page_num):
    """生成分页URL"""
    if page_num == 1:
        return f"{BASE_URL}{LIST_BASE}index.shtml"
    else:
        return f"{BASE_URL}{LIST_BASE}index_{page_num - 1}.shtml"

def smart_decode(resp):
    """智能解码响应内容"""
    content = resp.content
    charset_match = re.search(rb'charset=["\']?([a-zA-Z0-9_-]+)', content[:2000])
    if charset_match:
        charset = charset_match.group(1).decode('ascii').lower()
        try:
            return content.decode(charset)
        except:
            pass
    for enc in ['utf-8', 'gb18030', 'gbk']:
        try:
            text = content.decode(enc)
            if re.search(r'[\u4e00-\u9fff]', text):
                return text
        except:
            continue
    return content.decode('utf-8', errors='replace')

def fetch_list_page(page_num):
    """抓取列表页，返回符合条件的条目"""
    url = get_page_url(page_num)
    print(f"\n--- 第 {page_num} 页: {url} ---")

    resp = requests.get(url, headers=HEADERS, timeout=30)
    text = smart_decode(resp)
    soup = BeautifulSoup(text, 'html.parser')

    results = []
    for a_tag in soup.find_all('a'):
        href = a_tag.get('href', '')
        # 用title属性获取完整标题（页面文本可能被CSS截断显示不全）
        title = a_tag.get('title', '') or a_tag.get_text(strip=True)

        if not title or not href:
            continue

        # 只看详情页链接（.shtml格式，在对应目录下）
        if 'zyjzcljgjc' not in href:
            continue

        # 必须包含目标关键词
        if TARGET_KEYWORD not in title:
            continue

        # 排除不需要的类型
        if any(kw in title for kw in EXCLUDE_KEYWORDS):
            continue

        # 构建完整URL
        if href.startswith('/'):
            detail_url = BASE_URL + href
        elif href.startswith('http'):
            detail_url = href
        elif href.startswith('./'):
            detail_url = BASE_URL + LIST_BASE + href[2:]
        else:
            detail_url = BASE_URL + LIST_BASE + href

        print(f"  [命中] {title}")
        results.append((title, detail_url))

    return results

def fetch_pdf_link(title, detail_url):
    """进入详情页，找PDF附件下载链接"""
    try:
        resp = requests.get(detail_url, headers=HEADERS, timeout=30)
        text = smart_decode(resp)
        soup = BeautifulSoup(text, 'html.parser')

        pdf_links = []

        # 方法1：直接找所有PDF链接
        for a_tag in soup.find_all('a'):
            href = a_tag.get('href', '')
            link_text = a_tag.get_text(strip=True)
            if not href:
                continue
            if '.pdf' in href.lower():
                if href.startswith('/'):
                    full_url = BASE_URL + href
                elif href.startswith('http'):
                    full_url = href
                else:
                    base_dir = detail_url.rsplit('/', 1)[0] + '/'
                    full_url = base_dir + href
                pdf_links.append((link_text or '附件', full_url))

        # 方法2：查找iframe/embed
        if not pdf_links:
            for tag in soup.find_all(['iframe', 'embed', 'object']):
                src = tag.get('src', '') or tag.get('data', '')
                if src and '.pdf' in src.lower():
                    if src.startswith('/'):
                        full_url = BASE_URL + src
                    elif src.startswith('http'):
                        full_url = src
                    else:
                        base_dir = detail_url.rsplit('/', 1)[0] + '/'
                        full_url = base_dir + src
                    pdf_links.append(('嵌入PDF', full_url))

        # 方法3：正则搜索所有PDF链接（包括动态生成的）
        if not pdf_links:
            for pdf_url in re.findall(r'(https?://[^\s"\'<>]+\.pdf)', text, re.IGNORECASE):
                pdf_links.append(('正则匹配', pdf_url))
            for rel_url in re.findall(r'href=["\']([^"\']+\.pdf)["\']', text, re.IGNORECASE):
                if rel_url.startswith('/'):
                    full_url = BASE_URL + rel_url
                elif rel_url.startswith('http'):
                    full_url = rel_url
                else:
                    base_dir = detail_url.rsplit('/', 1)[0] + '/'
                    full_url = base_dir + rel_url
                if not any(u == full_url for _, u in pdf_links):
                    pdf_links.append(('正则匹配', full_url))

        # 方法4：查找附件下载区域（政府网站常用class名）
        if not pdf_links:
            # 查找所有可能的附件链接（.doc/.docx/.xls/.xlsx也记录下来）
            for a_tag in soup.find_all('a'):
                href = a_tag.get('href', '')
                if href and re.search(r'\.(pdf|doc|docx|xls|xlsx|zip|rar)', href, re.IGNORECASE):
                    link_text = a_tag.get_text(strip=True)
                    if href.startswith('/'):
                        full_url = BASE_URL + href
                    elif href.startswith('http'):
                        full_url = href
                    else:
                        base_dir = detail_url.rsplit('/', 1)[0] + '/'
                        full_url = base_dir + href
                    pdf_links.append((f'附件({link_text})', full_url))

        return pdf_links

    except Exception as e:
        print(f"  [错误] 访问详情页失败: {e}")
        return []

def extract_period(title):
    """从标题中提取期次（年月）"""
    m = re.search(r'(\d{4})年(\d{1,2})月', title)
    if m:
        return f"{m.group(1)}年{m.group(2)}月"
    return title

def main():
    all_results = []  # [(期次, pdf_url, detail_url), ...]

    for page in range(1, 7):
        items = fetch_list_page(page)
        print(f"  第{page}页共 {len(items)} 条匹配")

        for title, detail_url in items:
            period = extract_period(title)

            # 2026年2月已下载，跳过
            if '2026年2月' in period:
                print(f"  [跳过] {period} (已下载)")
                continue

            # 获取PDF链接
            pdf_links = fetch_pdf_link(title, detail_url)

            if pdf_links:
                for pdf_name, pdf_url in pdf_links:
                    all_results.append((period, pdf_url, detail_url))
                    print(f"  [PDF] {period}: {pdf_url}")
            else:
                all_results.append((period, '未找到PDF', detail_url))
                print(f"  [无PDF] {period} 详情页: {detail_url}")

            time.sleep(0.5)

        time.sleep(1)

    # 汇总输出
    print("\n" + "=" * 100)
    print("汇总：武汉市建设工程综合价格信息 PDF下载链接")
    print("=" * 100)
    print(f"{'期次':<15} | PDF URL")
    print("-" * 100)

    for period, pdf_url, detail_url in all_results:
        print(f"{period:<15} | {pdf_url}")

    print(f"\n共找到 {len(all_results)} 条记录")

    no_pdf = [(p, d) for p, u, d in all_results if u == '未找到PDF']
    if no_pdf:
        print(f"\n以下 {len(no_pdf)} 条未找到PDF链接，需手动检查详情页：")
        for period, detail_url in no_pdf:
            print(f"  {period}: {detail_url}")

if __name__ == '__main__':
    main()
