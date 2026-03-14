"""
路由模型训练：用清单库41.6万条数据训练轻量文本分类器。

原理：
- 清单库每条数据有 code(9位编码) + name(名称)
- 从编码前缀可以直接推断专业（如0310=K给排水，0304=D电气）
- 用名称文本训练分类器：给定清单名称 → 预测专业
- 替代现有的规则路由（关键词匹配/TF-IDF），更准确

模型选择：TF-IDF + LinearSVC（轻量、快速、不需要GPU）

用法：
  python tools/train_route_model.py           # 训练模型
  python tools/train_route_model.py --eval    # 在benchmark上评测
"""

import json
import re
import sys
import pickle
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# 编码前缀 → 路由标签（和bill_code_matcher的索引标签一致）
CODE_PREFIX_TO_LABEL = {
    # 安装工程(03) → 附录字母
    "0301": "A", "0302": "B", "0303": "C", "0304": "D",
    "0305": "E", "0306": "F", "0307": "G", "0308": "H",
    "0309": "J", "0310": "K", "0311": "L", "0312": "M",
    "0313": "N", "0314": "P",
    # 非安装 → 大类编码
    "0101": "01", "0102": "01", "0103": "01", "0104": "01",
    "0105": "01", "0106": "01", "0107": "01", "0108": "01",
    "0201": "01", "0202": "01", "0203": "01", "0204": "01",
    "0205": "01", "0206": "01",
    "0401": "04", "0402": "04", "0403": "04", "0404": "04",
    "0405": "04", "0406": "04", "0407": "04",
    "0501": "05", "0502": "05", "0503": "05", "0504": "05",
}


def _extract_core_name(name: str) -> str:
    """提取核心名称（和bill_code_matcher一致）。"""
    core = name.strip()
    if " " in core:
        core = core.split()[0]
    core = re.sub(r"[（(][^）)]*[）)]$", "", core)
    return core.strip()


def load_training_data():
    """从清单库加载训练数据。

    返回: [(text, label), ...]
    """
    lib_path = ROOT / "data" / "bill_library_all.json"
    if not lib_path.exists():
        print("清单库不存在")
        return []

    with open(lib_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    samples = []
    for lib_name, lib_data in data.get("libraries", {}).items():
        for item in lib_data.get("items", []):
            code = item.get("code", "")
            name = item.get("name", "")
            if len(code) < 9 or not name:
                continue
            if not re.match(r"^0[1-9]\d{7}", code):
                continue

            prefix4 = code[:4]
            label = CODE_PREFIX_TO_LABEL.get(prefix4)
            if not label:
                continue

            core = _extract_core_name(name)
            if core and len(core) >= 2:
                samples.append((core, label))

    return samples


def train_model(samples: list) -> dict:
    """训练路由分类器。

    用 TF-IDF + LinearSVC，对中文字符做 char n-gram。
    """
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.svm import LinearSVC
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import classification_report

    texts = [s[0] for s in samples]
    labels = [s[1] for s in samples]

    print(f"训练数据: {len(texts)}条, {len(set(labels))}个类别")

    # 统计标签分布
    label_counts = Counter(labels)
    print("标签分布:")
    for label, count in label_counts.most_common():
        print(f"  {label}: {count}")

    # 分训练集/测试集（80/20）
    X_train, X_test, y_train, y_test = train_test_split(
        texts, labels, test_size=0.2, random_state=42, stratify=labels
    )

    # TF-IDF：用字符级n-gram（中文不需要分词，直接切字符）
    # analyzer='char_wb' 在字边界切分，ngram_range=(1,3) 取1~3个字的组合
    vectorizer = TfidfVectorizer(
        analyzer='char_wb',
        ngram_range=(1, 3),
        max_features=50000,
        sublinear_tf=True,
    )

    X_train_tfidf = vectorizer.fit_transform(X_train)
    X_test_tfidf = vectorizer.transform(X_test)

    print(f"\n特征维度: {X_train_tfidf.shape[1]}")

    # LinearSVC：快速线性分类器
    clf = LinearSVC(
        C=1.0,
        max_iter=5000,
        class_weight='balanced',  # 处理类别不平衡
    )
    clf.fit(X_train_tfidf, y_train)

    # 测试集评测
    y_pred = clf.predict(X_test_tfidf)
    from sklearn.metrics import accuracy_score
    test_acc = accuracy_score(y_test, y_pred)
    print(f"\n测试集准确率: {test_acc:.1%}")

    # 详细报告（只打印主要类别）
    print("\n分类报告（测试集）:")
    print(classification_report(y_test, y_pred, zero_division=0))

    return {
        "vectorizer": vectorizer,
        "classifier": clf,
        "test_accuracy": test_acc,
        "label_counts": dict(label_counts),
    }


def save_model(model_data: dict):
    """保存模型到文件。"""
    model_path = ROOT / "data" / "route_model.pkl"
    with open(model_path, "wb") as f:
        pickle.dump({
            "vectorizer": model_data["vectorizer"],
            "classifier": model_data["classifier"],
        }, f)
    print(f"\n模型已保存: {model_path}")

    # 保存元数据（JSON格式，方便查看）
    meta_path = ROOT / "data" / "route_model_meta.json"
    meta = {
        "test_accuracy": model_data["test_accuracy"],
        "label_counts": model_data["label_counts"],
        "features": model_data["vectorizer"].get_feature_names_out().shape[0],
        "model_type": "TF-IDF + LinearSVC",
        "ngram_range": [1, 3],
        "analyzer": "char_wb",
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"元数据已保存: {meta_path}")


def eval_on_benchmark(model_path: Path = None):
    """在benchmark试卷上评测路由模型。"""
    if model_path is None:
        model_path = ROOT / "data" / "route_model.pkl"

    if not model_path.exists():
        print(f"模型文件不存在: {model_path}")
        return

    with open(model_path, "rb") as f:
        model_data = pickle.load(f)

    vectorizer = model_data["vectorizer"]
    clf = model_data["classifier"]

    # 加载benchmark试卷
    papers_dir = ROOT / "tests" / "benchmark_papers"
    if not papers_dir.exists():
        print("试卷目录不存在")
        return

    # 安装册号 → 附录字母
    QUOTA_TO_APPENDIX = {
        'C1': 'A', 'C2': 'B', 'C3': 'C', 'C4': 'D', 'C5': 'E',
        'C6': 'F', 'C7': 'G', 'C8': 'H', 'C9': 'J', 'C10': 'K',
        'C11': 'L', 'C12': 'M', 'C13': 'N',
    }

    total = 0
    correct = 0
    install_total = install_correct = 0
    noninst_total = noninst_correct = 0

    for pf in sorted(papers_dir.glob("*.json")):
        with open(pf, "r", encoding="utf-8") as f:
            paper = json.load(f)

        items = paper.get("items", [])
        # 判断试卷类型
        c_prefix = sum(1 for it in items for qid in it.get('quota_ids', []) if qid.startswith('C'))
        non_c = sum(1 for it in items for qid in it.get('quota_ids', []) if qid and not qid.startswith('C'))
        is_install = c_prefix > non_c

        pname = pf.stem
        expected_major = ''
        if not is_install:
            if '房' in pname or '建筑' in pname or '装饰' in pname:
                expected_major = '01'
            elif '市政' in pname:
                expected_major = '04'
            elif '园林' in pname:
                expected_major = '05'

        for item in items:
            bill_name = item.get("bill_name", "").strip()
            specialty = item.get("specialty", "").strip()
            if not bill_name or not specialty or not specialty.startswith("C"):
                continue

            core = _extract_core_name(bill_name)
            if not core:
                continue

            # 模型预测
            X = vectorizer.transform([core])
            predicted = clf.predict(X)[0]

            total += 1

            if is_install:
                expected = QUOTA_TO_APPENDIX.get(specialty, "")
                if not expected:
                    continue
                install_total += 1
                if predicted == expected:
                    install_correct += 1
                    correct += 1
            else:
                if not expected_major:
                    continue
                noninst_total += 1
                if predicted == expected_major:
                    noninst_correct += 1
                    correct += 1

    print(f"\n=== Benchmark评测（路由模型单独） ===")
    print(f"总准确率: {correct}/{total} = {correct*100/max(total,1):.1f}%")
    if install_total:
        print(f"安装({install_total}条): {install_correct*100/install_total:.1f}%")
    if noninst_total:
        print(f"非安装({noninst_total}条): {noninst_correct*100/noninst_total:.1f}%")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="路由模型训练")
    parser.add_argument("--eval", action="store_true", help="只评测不训练")
    args = parser.parse_args()

    if args.eval:
        eval_on_benchmark()
        return

    # 训练
    print("=" * 60)
    print("加载清单库训练数据...")
    samples = load_training_data()
    if not samples:
        return

    print(f"\n{'=' * 60}")
    print("训练路由模型...")
    model_data = train_model(samples)

    save_model(model_data)

    # 训练完直接在benchmark上评测
    print(f"\n{'=' * 60}")
    eval_on_benchmark()


if __name__ == "__main__":
    main()
