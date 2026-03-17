# -*- coding: utf-8 -*-
import sys
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
"""
深圳信息价导入工具

通过agent-browser操控浏览器，从深圳市建设工程造价信息系统提取材料价格数据。
网站有WAF防护，纯HTTP请求被拦截，必须通过真实浏览器环境操作。

原理：
1. agent-browser打开网站，页面用knockout.js渲染数据
2. 用eval从knockout viewModel中提取当前页数据
3. 通过点击分类树+翻页按钮遍历所有数据
4. 提取结果写入主材库

用法：
    python tools/import_shenzhen_browser.py --dry-run
    python tools/import_shenzhen_browser.py
"""

import argparse
import json
import subprocess
import time
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.import_price_excel import (
    normalize_unit, normalize_spec, is_junk_material,
)
from tools.pdf_profiles.base_profile import guess_category

SESSION = "shenzhen_import"
BASE_URL = "https://zjj.sz.gov.cn/szzjxx/web/pc/index"


def _eval_js(js: str, timeout: int = 15) -> str:
    """在浏览器中执行JS并返回结果"""
    js_safe = js.replace('"', '\\"')
    full_cmd = f'agent-browser eval --session-name {SESSION} "{js_safe}"'
    try:
        r = subprocess.run(full_cmd, shell=True, capture_output=True, timeout=timeout)
        out = (r.stdout or b'').decode('utf-8', errors='replace')
        err = (r.stderr or b'').decode('utf-8', errors='replace')
        return (out + err).strip()
    except subprocess.TimeoutExpired:
        return "TIMEOUT"


def _run_ab(cmd: str, timeout: int = 20) -> str:
    """运行agent-browser命令"""
    full_cmd = f'agent-browser {cmd} --session-name {SESSION}'
    try:
        r = subprocess.run(full_cmd, shell=True, capture_output=True, timeout=timeout)
        out = (r.stdout or b'').decode('utf-8', errors='replace')
        err = (r.stderr or b'').decode('utf-8', errors='replace')
        return (out + err).strip()
    except subprocess.TimeoutExpired:
        return "TIMEOUT"


def _eval_js_file(js: str, timeout: int = 15) -> str:
    """通过临时文件执行较长的JS"""
    tmp = PROJECT_ROOT / "output" / "temp" / "_sz_eval.js"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(js, encoding='utf-8')
    result = _run_ab(f'eval "eval(require(\'fs\').readFileSync(\'{tmp.as_posix()}\',\'utf8\'))"', timeout)
    return result.strip()


def open_site() -> bool:
    """打开深圳造价信息网站"""
    print("打开深圳造价信息网站...")
    result = _run_ab(f'open "{BASE_URL}"', 30)
    time.sleep(3)
    if 'zjj.sz.gov.cn' in result or '造价' in result:
        print("  网站打开成功")
        # 等待knockout加载完成
        for _ in range(5):
            check = _eval_js('typeof ko')
            if 'object' in check:
                return True
            time.sleep(2)
        print("  警告: knockout未加载")
        return True
    print(f"  打开失败: {result[:200]}")
    return False


def get_categories() -> list:
    """获取所有材料分类"""
    js = """
(function(){
  var vm = ko.dataFor(document.body);
  if(!vm) return '[]';
  // 从页面DOM提取分类树
  var items = document.querySelectorAll('.ztree li a');
  var cats = [];
  for(var i=0;i<items.length;i++){
    var text = items[i].textContent.trim();
    var id = items[i].getAttribute('id');
    if(text && id) cats.push({name:text, id:id});
  }
  if(cats.length === 0){
    // 备用：从sidebar提取
    var lis = document.querySelectorAll('#sidebar li');
    for(var i=0;i<lis.length;i++){
      var t = lis[i].textContent.trim();
      if(t) cats.push({name:t, id:String(i)});
    }
  }
  return JSON.stringify(cats);
})()
"""
    result = _eval_js(js.replace('\n', ' '))
    try:
        # 从输出中提取JSON
        json_match = re.search(r'\[.*\]', result)
        if json_match:
            return json.loads(json_match.group())
    except:
        pass
    return []


def get_current_page_data() -> list:
    """从knockout viewModel提取当前页数据"""
    js = "(function(){var vm=ko.dataFor(document.body);if(!vm)return '[]';var list=vm.data.noticeList();var rows=[];for(var i=0;i<list.length;i++){var item=ko.toJS(list[i]);rows.push({mc:item.mc,gg:item.gg,dw:item.dw,dj:item.djSq,id:item.id});}return JSON.stringify(rows);})()"
    result = _eval_js(js)
    try:
        json_match = re.search(r'\[.*\]', result, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except:
        pass
    return []


def get_page_info() -> dict:
    """获取分页信息"""
    js = r"(function(){var t=document.body.innerText;var m=t.match(/共(\d+)条数据.*第(\d+)页.*共(\d+)页/);if(m)return JSON.stringify({total:parseInt(m[1]),page:parseInt(m[2]),pages:parseInt(m[3])});return JSON.stringify({total:0,page:0,pages:0});})()"
    result = _eval_js(js)
    try:
        json_match = re.search(r'\{.*\}', result)
        if json_match:
            return json.loads(json_match.group())
    except:
        pass
    return {"total": 0, "page": 0, "pages": 0}


def click_next_page() -> bool:
    """点击下一页"""
    result = _run_ab('click "下一页"', 10)
    time.sleep(2)
    return 'Done' in result


def click_category(name: str) -> bool:
    """点击分类"""
    result = _run_ab(f'click "{name}"', 10)
    time.sleep(2)
    return 'Done' in result


def extract_all_data(verbose: bool = False) -> list:
    """提取所有材料数据"""
    all_records = []
    seen_ids = set()

    # 先获取当前分类的所有数据
    page_info = get_page_info()
    total = page_info['total']
    pages = page_info['pages']
    current_page = page_info['page']

    if verbose:
        print(f"  当前分类: 共{total}条, {pages}页")

    # 遍历所有页
    for p in range(pages):
        rows = get_current_page_data()
        for row in rows:
            rid = row.get('id', '')
            if rid and rid not in seen_ids:
                seen_ids.add(rid)
                all_records.append(row)

        if verbose and (p + 1) % 10 == 0:
            print(f"    第{p+1}/{pages}页, 累计{len(all_records)}条")

        # 翻到下一页
        if p < pages - 1:
            if not click_next_page():
                break
            time.sleep(1)

    return all_records


def to_standard_records(raw_data: list) -> list:
    """转换为标准导入格式"""
    records = []
    for row in raw_data:
        mc = str(row.get('mc', '')).strip()
        gg = str(row.get('gg', '')).strip()
        dw = str(row.get('dw', '')).strip()
        dj = str(row.get('dj', '')).strip()

        if not mc or not dj:
            continue

        try:
            price = float(dj)
        except:
            continue

        if price <= 0:
            continue
        if is_junk_material(mc):
            continue

        # 深圳价格是市区不含税预算价（元），单位可能是t/m/m2等
        records.append({
            "name": mc,
            "spec": normalize_spec(gg),
            "unit": normalize_unit(dw),
            "price": round(price * 1.13, 2),  # 反算含税
            "price_excl_tax": price,
            "tax_included": True,
            "tax_rate": 0.13,
            "city": "深圳",
            "category": guess_category(mc),
        })

    return records


def main():
    parser = argparse.ArgumentParser(description='深圳信息价浏览器导入工具')
    parser.add_argument('--dry-run', action='store_true', help='试运行')
    parser.add_argument('--verbose', '-v', action='store_true', help='详细输出')
    args = parser.parse_args()

    # 1. 打开网站
    if not open_site():
        return

    # 2. 跳过分类检测，直接从当前页面提取（默认加载了第一个分类）
    print("\n提取数据...")

    # 先清除分类筛选，加载全部数据（点击页面上的"清除分类"按钮或直接操作）
    _run_ab('click "建筑材料"', 10)  # 点击大分类
    time.sleep(2)

    page_info = get_page_info()
    print(f"  总条数: {page_info['total']}, 总页数: {page_info['pages']}")

    # 3. 逐页提取
    all_data = extract_all_data(verbose=True)
    print(f"\n提取完成: {len(all_data)}条原始数据")

    # 5. 转换格式
    records = to_standard_records(all_data)
    print(f"转换完成: {len(records)}条有效记录")

    # 6. 打印示例
    for r in records[:5]:
        print(f"  {r['name']} | {r['spec']} | {r['unit']} | "
              f"含税{r['price']} | {r['category']}")

    # 7. 保存或导入
    if args.dry_run:
        print("\n[试运行] 不写库")
        # 保存到JSON
        out_path = PROJECT_ROOT / "output" / "temp" / "shenzhen_data.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        print(f"  已保存到 {out_path}")
    else:
        from tools.import_price_pdf import import_to_db
        result = import_to_db(records, '广东', '2026-02', 'shenzhen_browser', dry_run=False)
        print(f"  导入完成: 导入{result.get('imported', 0)}")


if __name__ == '__main__':
    main()
