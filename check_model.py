import os
# 检查模型缓存路径
paths = [
    "/root/.cache/huggingface/hub/models--BAAI--bge-large-zh-v1.5",
    "/root/.cache/torch/sentence_transformers/BAAI_bge-large-zh-v1.5",
]
for p in paths:
    exists = os.path.exists(p)
    if exists:
        size = sum(os.path.getsize(os.path.join(dp,f)) for dp,dn,fn in os.walk(p) for f in fn)
        print(f"{p}: EXISTS ({size/1024/1024:.0f}MB)")
    else:
        print(f"{p}: NOT FOUND")

# 检查环境变量
for k in ["HF_HOME", "TRANSFORMERS_CACHE", "SENTENCE_TRANSFORMERS_HOME", "HF_HUB_CACHE"]:
    print(f"{k}={os.environ.get(k, 'not set')}")

# 列出/root/.cache内容
for item in os.listdir("/root/.cache"):
    print(f"/root/.cache/{item}")
