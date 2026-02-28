"""临时脚本：在服务器上重建重庆向量索引"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.vector_engine import VectorEngine
province = "\u91cd\u5e86\u5e02\u901a\u7528\u5b89\u88c5\u5de5\u7a0b\u8ba1\u4ef7\u5b9a\u989d(2018)"
print(f"Rebuilding: {province}")
ve = VectorEngine(province)
ve.build_index()
print("Done!")
