/**
 * 从定额库全名中提取省份/地区名
 *
 * 例如：
 *   "北京市建设工程施工消耗量标准(2024)" → "北京"
 *   "广东省通用安装工程综合定额(2018)"   → "广东"
 *   "宁夏回族自治区装配式钢结构..."       → "宁夏"
 *   "佛山市海绵城市..."                  → "广东"（佛山归入广东）
 */

// 地级市 → 所属省份（这些城市有自己的定额标准，但分组时应归入省份）
const CITY_TO_PROVINCE: Record<string, string> = {
  '佛山': '广东',
  '深圳': '广东',
  '广州': '广东',
  '东莞': '广东',
  '珠海': '广东',
  '中山': '广东',
  '惠州': '广东',
};

export function extractRegion(name: string): string {
  let region = name.slice(0, 2); // 默认取前2个字符

  // 找"省"、"市"位置，取其前面部分
  for (let i = 0; i < name.length && i < 10; i++) {
    if (name[i] === '省' || name[i] === '市') {
      region = name.slice(0, i);
      break;
    }
    // "宁夏回族自治区" → 取"回"前面的部分
    if (name.slice(i, i + 2) === '回族') {
      region = name.slice(0, i);
      break;
    }
  }

  // 地级市归入所属省份
  return CITY_TO_PROVINCE[region] || region;
}
