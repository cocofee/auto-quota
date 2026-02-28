"""在服务器上重建指定省份的向量索引（解决chromadb版本不兼容问题）

用法：python tools/rebuild_vector_index.py <省份名>
示例：python tools/rebuild_vector_index.py "重庆市通用安装工程计价定额(2018)"
      python tools/rebuild_vector_index.py --all  # 重建所有省份
"""
import sys
import os

# 确保在项目根目录下运行
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.vector_engine import VectorEngine
import config


def rebuild_one(province: str):
    """重建单个省份的向量索引"""
    print(f"重建向量索引: {province}")
    try:
        ve = VectorEngine(province)
        ve.build_index()
        print(f"  完成: {province}")
    except Exception as e:
        print(f"  失败: {province} → {e}")


def rebuild_all():
    """重建所有已安装省份的向量索引"""
    provinces_dir = config.PROVINCES_DB_DIR
    if not provinces_dir.exists():
        print("provinces目录不存在")
        return
    provinces = [d.name for d in provinces_dir.iterdir() if d.is_dir() and (d / "quota.db").exists()]
    print(f"找到 {len(provinces)} 个省份定额库")
    for p in sorted(provinces):
        rebuild_one(p)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    if sys.argv[1] == "--all":
        rebuild_all()
    else:
        rebuild_one(sys.argv[1])
