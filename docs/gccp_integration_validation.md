# GCCP 无感组价集成验证方案

目标：验证 AutoQuota 能否借助广联达自己的存档、复用组价、标准组价机制，只改组价层，不改清单母体。

清单母体红线字段：

- 清单编码
- 清单名称
- 项目特征
- 单位
- 工程量
- 层级/归属结构

允许变化的层：

- 定额子目
- 换算信息
- 子目工料机
- 人材机
- 主材名称/主材价格
- 综合单价、合价等由组价重算产生的价格字段

## A. 云存档是否产生本地可写缓存

当前结论：安装目录存在归档模型，但还不能证明真实用户归档库在本地。

已确认模型：

- `D:\广联达\GCCP\7.0-X64\Construction\1\Bin\Config\ArchiveData.GSP`
- `D:\广联达\GCCP\7.0-X64\Bin\Config\ArchiveData.GSP`
- `D:\广联达\GCCP\7.0-X64\Construction\1\Core\GYS\GSPFiles\Job\Common\Query\QueryArchiveData.GSP`

`ArchiveData.GSP` 包含：

- `BQCatalog`
- `BQItem`
- `ConvInfo`
- `NormCatalog`
- `NormItem`
- `LMMDetail`
- `ResCatalog`
- `Resource`
- `MixResLMMDetail`

验证方法：

1. 复制一个测试工程，不在正式投标文件上做。
2. 运行 before 快照。
3. 在 GCCP 里手动执行一次：
   - 云存档 -> 组价方案
   - 云存档 -> 子目
   - 云存档 -> 人材机
4. 运行 after 快照。
5. 运行 compare。
6. 看 diff 中是否出现稳定的本地业务数据文件，而不是只有日志、Temp、Cloud 缓存、RecentFile。

如果只有日志/临时缓存变化，说明云存档更可能是云端存储，不适合作为 AutoQuota 的本地写入口。

## B. AutoReuseData 是否只改组价，不改清单字段

当前结论：从配置看有“只组价”路径，但必须做运行时验证。

安全候选动作：

- `AutoReuseData`：自动复用组价
- `ExtractAlready`：提取已有组价
- `GPS_InsertNorm`：添加组价
- `GPS_ReplaceNorm`：替换组价

危险动作：

- `ExtractAlreadyBQ`：提取已有清单
- `GPS_InsertBQ`：插入清单
- `GPS_ReplaceBQ`：替换清单
- `GPS_InsertBQItem`
- `GPS_ReplaceBQItem`

验证方法：

1. 复制当前工程为测试工程。
2. 在 GCCP 内做一个只读导出或报表，记录清单红线字段。
3. 运行 before 快照，带上测试工程路径。
4. 在 GCCP 中执行 `自动复用组价` 或 `复用组价` 中的“添加组价/替换组价”，不要点“插入清单/替换清单”。
5. 保存测试工程。
6. 再做一次只读导出或报表，记录清单红线字段。
7. 运行 after 快照和 compare。
8. 对比红线字段必须完全一致；价格字段允许变化。

仅看工程包 hash 不够，因为组价变化会导致 GSP 文件变化；最终证明必须落到清单字段级别。

## C. 构造历史工程/标准组价工程供 GCCP 自动复用

推荐方向：优先验证“历史工程/标准组价工程”路线，而不是直接写云存档。

原因：

- GCCP 自己负责匹配当前清单。
- AutoQuota 不直接改正式工程清单。
- 可避开反复导入导出清单造成的编码、特征、单位、工程量风险。
- 标准组价场景天然适合多个单位工程合并后的 100 项组价方案。

目标流程：

1. 从当前工程或标准组价界面取出待组价清单特征。
2. AutoQuota 只生成组价结果。
3. 生成一个“历史组价工程/标准组价工程”。
4. 在 GCCP 中通过 `AutoReuseData` 把组价匹配回当前工程。
5. 用户在 GCCP 内检查、调整定额。
6. AutoQuota 再处理主材名称和上主材。
7. GCCP 内检查出成果。

关键验证：

- AutoQuota 生成的历史工程是否能被 GCCP 作为历史工程选择。
- `AutoReuseData` 匹配条件是否可只用编码/名称/项目特征/单位。
- 复用时是否可以只执行添加/替换组价。
- 多单位工程标准组价回填时，是否按照合并后的相同清单稳定分发。

## 快照工具

工具路径：

`C:\Users\Administrator\Documents\trae_projects\auto-quota\tools\gccp_validation_snapshot.ps1`

示例：

```powershell
powershell -ExecutionPolicy Bypass -File C:\Users\Administrator\Documents\trae_projects\auto-quota\tools\gccp_validation_snapshot.ps1 -Mode before -Name archive_test

powershell -ExecutionPolicy Bypass -File C:\Users\Administrator\Documents\trae_projects\auto-quota\tools\gccp_validation_snapshot.ps1 -Mode after -Name archive_test

powershell -ExecutionPolicy Bypass -File C:\Users\Administrator\Documents\trae_projects\auto-quota\tools\gccp_validation_snapshot.ps1 -Mode compare -Name archive_test
```

带工程文件：

```powershell
powershell -ExecutionPolicy Bypass -File C:\Users\Administrator\Documents\trae_projects\auto-quota\tools\gccp_validation_snapshot.ps1 -Mode before -Name autoreuse_test -ProjectPath "C:\Users\Administrator\Documents\Glodon\GCCP6\WorkCopy\测试工程.GBQ6"

powershell -ExecutionPolicy Bypass -File C:\Users\Administrator\Documents\trae_projects\auto-quota\tools\gccp_validation_snapshot.ps1 -Mode after -Name autoreuse_test -ProjectPath "C:\Users\Administrator\Documents\Glodon\GCCP6\WorkCopy\测试工程.GBQ6"

powershell -ExecutionPolicy Bypass -File C:\Users\Administrator\Documents\trae_projects\auto-quota\tools\gccp_validation_snapshot.ps1 -Mode compare -Name autoreuse_test
```

输出目录：

`C:\Users\Administrator\Documents\trae_projects\auto-quota\reports\gccp_validation`
