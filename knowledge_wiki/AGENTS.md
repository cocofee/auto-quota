---
title: "Knowledge Wiki Agent Rules"
type: "guide"
status: "reviewed"
province: ""
specialty: ""
source_refs: []
source_kind: "system"
created_at: "2026-04-07"
updated_at: "2026-04-07"
confidence: 100
owner: "codex"
tags:
  - "wiki"
  - "agent"
  - "rules"
related: []
---
# Knowledge Wiki Agent Rules

## 鐩爣

缁存姢 `knowledge_wiki/` 涓殑 Markdown Wiki锛屼娇鍏舵垚涓哄師濮嬭祫鏂欎笌姝ｅ紡鐭ヨ瘑灞備箣闂寸殑浜虹被鍙缁煎悎灞傘€?
## 鏍稿績娴佺▼

1. ingest
2. compile
3. link
4. lint
5. promote

## ingest 瑙勫垯

- 鍙厑璁镐粠鍘熷璧勬枡銆乻ource pack 鎴?knowledge staging 鐢熸垚鐭ヨ瘑椤?- 涓嶅厑璁稿嚟绌鸿ˉ浜嬪疄
- 姣忎釜鏂伴〉闈㈠繀椤诲甫 `source_refs`

## compile 瑙勫垯

- 浼樺厛鏇存柊宸叉湁椤甸潰
- 鍙湁褰撴蹇垫垨涓婚纭疄涓嶅瓨鍦ㄦ椂锛屾墠鍒涘缓鏂伴〉
- 鏇存柊瑙勫垯椤垫椂蹇呴』鏄惧紡鍖哄垎浜嬪疄銆佹帹鏂€佸缓璁?
## link 瑙勫垯

- 椤甸潰鏇存柊鍚庡簲琛ュ厖 `related`
- `index.md` 蹇呴』缁存姢涓€绾у鑸?- `log.md` 蹇呴』璁板綍鏂板鍜岄噸澶ф洿鏂?
## lint 瑙勫垯

- 妫€鏌ュ宀涢〉
- 妫€鏌ョ己澶?frontmatter
- 妫€鏌ョ己澶?`source_refs`
- 妫€鏌ュ啿绐佺粨璁?- 妫€鏌?stale 椤甸潰

## promote 瑙勫垯

- `draft` 涓嶈兘鐩存帴杩涘叆姝ｅ紡鐭ヨ瘑灞?- 鍙湁 `reviewed` 鍐呭鎵嶈兘杩涘叆鏅嬪崌鍊欓€?- 姝ｅ紡瑙勫垯椤靛繀椤讳汉宸ョ‘璁
