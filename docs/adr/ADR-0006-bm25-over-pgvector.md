# ADR-0006：BM25 优先于向量检索（bm25-over-pgvector）

- 状态：已接受
- 日期：2026-08-03
- 关联：ARCHITECTURE §7.4、TASK_BREAKDOWN §7.3.3

## 背景

记忆检索需要支持中英混排（日志 + 报错栈 + 代码片段 + 中文计划）。
候选方案：pgvector 向量检索、外部全文搜索引擎、纯 Python BM25。

## 决策

1. **默认检索器 = 纯 Python BM25**，零外部依赖；
2. 倒排表持久化到 `inverted_index` 表（SQLite/PostgreSQL 通用，
   不依赖任何全文扩展）；
3. 分词：中文 bigram + 代码标识符拆分（`getUserName` → 多个词元）；
4. `k1=1.2, b=0.75` 可配；**增量索引**（重复 doc_id 覆盖）；
5. EmbeddingRetriever 作为**可选增强**：默认关闭，开启时用
   RRF（`score = Σ 1/(60 + rank)`）与 BM25 融合，不引入向量数据库。

## 理由

- **零依赖**：自托管部署不要求外部搜索服务；
- **可审计**：倒排表落库，词项/词频可检查；
- **够用**：Agent 记忆的查询模式是「精确命中标识符/路径/错误码」，
  这正是 BM25 + 代码感知分词的强项；语义相似度收益有限；
- **降级**：embedding 不可用时优雅回到 BM25。

## 替代方案

- pgvector：需要 PostgreSQL 扩展，与「SQLite 可跑通」冲突；
- elasticsearch：重型外部依赖，违反零依赖约束。

## 后果

- 正向：`Bm25Retriever` 可直接索引 10 万条记忆，P95 检索 < 100ms；
- 负向：中文 bigram 对超长中文查询召回略宽，靠 top_k + 重排缓解。

## 测试

`tests/unit/memory/test_bm25.py`：排序正确性、增量索引、层过滤、
1000 条索引 < 1s。
