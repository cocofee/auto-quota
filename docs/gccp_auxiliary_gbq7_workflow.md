# AutoQuota + 广联达辅助 GBQ7 组价源执行方案

## 目标边界

这条路线的核心不是让 AutoQuota 直接改正式工程，而是把正式工程和自动组价隔离开：

```text
正式工程 GBQ7：只在广联达里复用组价，不被 AutoQuota 写入
辅助组价源 GBQ7：由 AutoQuota 维护组价草稿，人工在广联达里调整
复用组价：由广联达自己的 AutoReuseData / ReuseHistoricalData 执行
```

正式工程红线字段必须保持不变：

- 清单编码
- 清单名称
- 项目特征
- 单位
- 工程量
- 单位工程/分部/清单层级

允许变化的是组价层：

- 定额子目
- 换算信息
- 子目工料机
- 主材名称、规格、价格
- 综合单价、合价等由组价重算产生的价格字段

## 当前落地形态

当前已实现的是安全的第一阶段：

```text
清单/标准组价清单 Excel
  -> AutoQuota 套定额
  -> 生成“辅助 GBQ7 导入包” Excel + manifest
  -> 只导入辅助 GBQ7
  -> 在辅助 GBQ7 里人工调整定额和主材
  -> 正式工程从辅助 GBQ7 复用组价
  -> 快照校验正式工程清单身份字段
```

也就是说，中间的 Excel 不再被当成正式工程交换文件，而是被明确降级为“辅助组价源维护包”。正式工程不反复导入导出，避免清单编码、特征、单位、工程量被 Excel 往返破坏。

## 标准组价场景

多单位工程时，推荐在广联达标准组价界面先把相同清单合并。例如 10 个单位工程 1000 项合并成 100 项后，只处理这 100 项：

1. 从标准组价/合并清单导出待组价 Excel。
2. 用 AutoQuota 生成辅助源导入包。
3. 把导入包只导入辅助 GBQ7。
4. 在辅助 GBQ7 里检查、调整定额。
5. 保存辅助 GBQ7。
6. 正式工程或标准组价界面从辅助 GBQ7 自动复用组价。
7. 在广联达内检查结果。

## 执行命令

生成辅助源导入包：

```powershell
python C:\Users\Administrator\Documents\trae_projects\auto-quota\tools\gccp_aux_workflow.py build `
  --bill "D:\项目\标准组价清单.xlsx" `
  --aux-gbq7 "D:\项目\AutoQuota辅助组价源.GBQ7" `
  --formal-gbq7 "D:\项目\正式投标工程.GBQ7" `
  --province "北京2024"
```

也可以用批处理：

```powershell
C:\Users\Administrator\Documents\trae_projects\auto-quota\tools\生成GCCP辅助组价源.bat "D:\项目\标准组价清单.xlsx" "D:\项目\AutoQuota辅助组价源.GBQ7" "D:\项目\正式投标工程.GBQ7" "北京2024"
```

输出目录：

```text
C:\Users\Administrator\Documents\trae_projects\auto-quota\output\gccp_aux
```

每次运行会生成：

- `*_aux_import.xlsx`：只用于导入辅助 GBQ7 的组价草稿。
- `*_match.json`：本次匹配明细，后续可用于复盘准确率。
- `*_manifest.json`：正式工程、辅助工程、生成文件和安全策略记录。

## 正式工程复用前后校验

复用前：

```powershell
python C:\Users\Administrator\Documents\trae_projects\auto-quota\tools\gccp_aux_workflow.py snapshot `
  --mode before `
  --name reuse_test_001 `
  --formal-gbq7 "D:\项目\正式投标工程.GBQ7"
```

然后在广联达正式工程里执行“复用组价/自动复用组价”，来源选择辅助 GBQ7，只做添加/替换组价，不做插入/替换清单。

复用后：

```powershell
python C:\Users\Administrator\Documents\trae_projects\auto-quota\tools\gccp_aux_workflow.py snapshot `
  --mode after `
  --name reuse_test_001 `
  --formal-gbq7 "D:\项目\正式投标工程.GBQ7"

python C:\Users\Administrator\Documents\trae_projects\auto-quota\tools\gccp_aux_workflow.py snapshot `
  --mode compare `
  --name reuse_test_001
```

报告目录：

```text
C:\Users\Administrator\Documents\trae_projects\auto-quota\reports\gccp_validation
```

## 后续真正“无感”的位置

现在还没安全证明 GBQ7 可直接写，所以不能承诺直接生成完整 GBQ7。后续要去掉“导入辅助 GBQ7”这一步，有两个候选方向：

1. 辅助 GBQ7 自动导入：用广联达自己的导入功能维护辅助源，但后台化执行，正式工程仍不被写。
2. 辅助 GBQ7 直接写入：只在辅助源上写 NormItem / LMMDetail / Resource，不写正式工程，也不写正式清单 BQItem。

代码里已经预留了 `AuxProjectUpdater`：

- 当前实现：`ExcelImportPackageUpdater`
- 未来替换点：`DirectGbq7UpdaterPlaceholder`

只要后续验证出 GBQ7 存储结构或广联达内部接口，替换这个 updater 即可，正式工程保护策略不变。
