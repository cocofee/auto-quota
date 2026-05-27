# Internal Rules

## 编码前思考
陈述你的假设。不确定时提问。绝不猜测。

## 简洁优先
写出解决这个问题的最少代码。不要添加没人要求的抽象。

## 做精准的修改
不要修改与请求无关的代码。每行更改都必须追溯到所要求的内容。

## 以目标为导向的执行
将模糊的指令转化为可验证的成功标准，然后再编写代码。

## OSS 数据前提
OSS 数据是造价人员人工套完的定额结果，虽然只覆盖部分省份，且不同省份的定额编号、章节和表述会有差异，但定额体系大体相通。即使个别样本可能有错，OSS 仍是高价值训练和参考数据。当前项目里的 `data/goal_search/oss_samples*.jsonl` 只是抽样/评估视图，不代表 OSS 母库规模；本地 OSS XML 母库在 `D:\广联达临时文件\oss_samples`，约 14GB、数千个 XML，可由现有 XML parser 抽取清单-定额对。本地资产中还存在百万级定额/清单数据，例如 `national_index.sqlite` 的 `national_quotas` 和 `bill_library.db` 的 `bill_items`。后续 reranker、parser、taxonomy、recall 相关工作应把 OSS/定额母库作为高信任训练资产处理，同时用 source/province/source_family 切分和审计防止同源自证或单省过拟合。
