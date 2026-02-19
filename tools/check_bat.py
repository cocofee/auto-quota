# -*- coding: utf-8 -*-
"""检查bat文件内容"""
with open("运行匹配.bat", "rb") as f:
    raw = f.read()

# 检查连续两个反斜杠字节
bs = b"\x5c\x5c"  # 两个反斜杠的字节
count = raw.count(bs)
print(f"双反斜杠出现次数: {count}")
print(f"文件大小: {len(raw)} bytes")

# 用GBK解码后逐行检查
text = raw.decode("gbk")
for i, line in enumerate(text.split("\n"), 1):
    if "\x5c\x5c" in line:
        print(f"  行{i}: {line.rstrip()}")
