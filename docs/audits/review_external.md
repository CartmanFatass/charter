# 1. Executive Summary

## **Overall Verdict：Changes Requested**

**置信度：98%**

本轮审查锁定远端 `main` 最新提交：

```text
1c336c31436fe7ddb079c2517d8ff4edd179851b
parent: 14fab6d47dca364980d69410dddec4c9c39ef14e
```

提交信息与用户描述一致，主要修改集中在 causal ordering、event-specific schema、timeline privacy、watch crash boundary、inflight read-only 和 CLI 完整性。

相较上一轮，绝大多数显式问题已经得到实质修复：

* orphan MCP test 已移除；
* 合法的同时间戳 `return → intake → resolve` 路径现在可以通过；
* root authority bypass 已消失；
* dispatch metadata 不再从任意 actor declaration 回填；
* relation schema、timeline privacy、统一 filter、actor 双栏和互斥 flags 都已经落地；
* `inflight.live()`、`read_records()`、`live_records()` 默认不再 prune；
* statusline 的双 marker 已修复。

但是，当前实现仍有三个足以阻止批准的问题：

1. **生产 Event ID 实际没有包含 `source_ordinal`。**
   `make_event_id()` 虽然新增了 `ordinal` 参数，但 collector 中 rollout、Hook 和 inflight 的调用都没有传入该参数，因此自审文档中的“source ordinals incorporated in SHA-256 IDs”并不成立。

2. **mirror dedupe 仍然过宽，会吞掉同一 session 内的不同 relation declarations。**
   relation 没有 `work_id`，而 user-message declaration key 使用 `(schema, event, work_id, sid)`；因此同一 session 中第二条 `peer/reports_to/owner` declaration 会被当作第一条的 mirror，仅合并 evidence，第二条关系本身消失。

3. **同时间戳排序会“修复”源中本来无效的 declaration 顺序。**
   固定的 `return=80 / intake=82 / resolve=85` 可以修复跨文件 coarse timestamp 的合法路径，但也会把同一源中原本写在 intake 前面的 resolve，强行重排到 intake 后并接受。这与“观察 exact events，不替参与者修正 workflow”存在冲突。

此外，GitHub connector 没有返回该提交的 combined status 或关联 workflow run。仓库自审只报告四个定向模块中的 82 个测试，而正式 workflow 实际会在 Python 3.11–3.14 上运行整个 `unittest discover`。

还有一个资料问题：`docs/review_external.md` 在 `1c336c` 上仍不存在。因此下面是按照上一轮实际审查结论和本轮提交差异逐项复核，而不是依据该文件内容复核。

---

# 2. Verification of Addressed Items

## 2.1 移除 orphan `test_openai_developer_mcp.py`

**状态：PASS**

精确文件路径在最新提交上返回 404，`14fab6d..1c336c` 的 commit compare 也将其标记为 removed。

这一轮确实消除了上一版必然破坏全量 `unittest discover` 的孤立测试。

**残余限制：** 没有远端 CI status，所以只能确认文件层面的阻断已经消失，不能确认整个仓库测试已经执行通过。

---

## 2.2 Fine-grained causal lifecycle ordering 与 Event ID ordinal

**状态：PARTIAL — lifecycle ordering 已修复，Event ID ordinal 未修复**

现在 declaration subtype 的优先级已经变成：

```text
dispatch declaration   20
actor_started          30
actor_returned         80
intake declaration     82
resolve declaration    85
actor_stopped          90
```

实现不再给所有 `workflow_declared` 使用统一优先级，而是根据 declaration event 返回不同 priority。

新增测试也覆盖了相同 timestamp 下：

```text
dispatch → start → return → intake → resolve
```

最终 phase 为 `resolved` 且没有 unbound event。

### 未完成部分：生产 ID 未传 ordinal

函数现在支持：

```python
def make_event_id(..., ordinal: int | None = None) -> str:
    ...
    if ordinal is not None:
        parts.append(str(ordinal))
```

但所有实际调用仍类似：

```python
make_event_id(
    "rollout_event",
    file_path,
    line_number,
    raw_kind,
    session_id,
    actor_id=...,
    discriminator=...,
)
```

没有：

```python
ordinal=ordinal_counter
```

Hook 和 inflight 路径也同样没有传入。

因此目前：

```text
ObservedEvent.ordinal
```

只参与排序，不参与：

```text
ObservedEvent.id
```

自审文档把 causal-order tests 当作 ID ordinal 的证据，但这些测试只验证 event order 和最终 phase，没有验证两个不同 source ordinal 是否产生不同 SHA-256 ID。

### 修正时需要注意

不要直接把当前全局 `ordinal_counter` 填入 ID。它会因为：

* root rollout 尾部追加一条记录；
* 新增一个更早排序的 child session；
* descendant traversal 变化；

而让未改变的后续事件 ID 整体漂移。

应使用稳定的 **source-local ordinal**，例如：

```text
rollout: file_path + line_number + emitted_subevent_index
hook:    trace_file + line_number
inflight: token 或 (timestamp, token)
```

---

## 2.3 Event-specific declaration schemas

**状态：MOSTLY PASS**

目前已经实现：

* `dispatch` 禁止 `relation`、`related_actor_id`；
* `intake`、`resolve` 要求非空 `work_id`；
* `intake`、`resolve` 禁止 relation 和 dispatch metadata 字段；
* `relation` 要求合法 `relation`；
* `relation` 要求非空 `related_actor_id`；
* `relation` 禁止 work lifecycle metadata；
* 不再使用缺省 `peer`。

这直接修复了上一轮：

```python
decl.relation or "peer"
```

导致的未声明 peer inference。Projector 现在只在 `rel_kind` 实际存在时创建关系。

### 残余严格性问题：显式 `null` 仍可穿过 forbidden-field 检查

目前使用：

```python
if data.get(forbidden) is not None:
    reject
```

所以以下声明仍会被接受：

```json
{
  "schema": 1,
  "event": "dispatch",
  "relation": null
}
```

以及：

```json
{
  "schema": 1,
  "event": "relation",
  "relation": "peer",
  "related_actor_id": "B",
  "work_id": null
}
```

严格 event schema 应检查：

```python
if forbidden in data:
```

而不是检查字段值是否非 `None`。

这不会制造 peer inference，但与“forbidden field”字面约束不完全一致。

---

## 2.4 Timeline JSON privacy

**状态：PASS**

新增的：

```python
sanitize_event_for_public_view()
```

默认清洗：

```text
content
prompt
arguments
output
raw_message
```

只有 `include_content=True` 才返回原内容。

`cmd_observe_timeline --json` 已统一调用该 serializer，不再只隐藏 `content` 而泄露 `prompt`。

相应测试同时验证：

* 默认输出不包含 dispatch prompt；
* 默认输出不包含 message body；
* `--include-content` 恢复两者。

Gate 要求的 prompt、content、arguments、output 默认 elision 已满足。

---

## 2.5 删除 `declarations_by_actor` fallback

**状态：PASS**

Dispatch reducer 现在只接受：

1. `dispatch_sent.attributes["declaration"]`；
2. 或重新解析这个 exact dispatch event 自己的 prompt。

它不再从整个 snapshot 中任意一个 actor declaration 回填 metadata。

因此后来的 relation declaration 不会再反向污染之前的 dispatch，多个 assignment 也不会因为 actor 相同而共享错误 title/direction/role。

`declarations_by_work_id` 仍被构建但没有使用，可以清理，但不再造成语义错误。

---

## 2.6 统一 timeline filtering

**状态：PARTIAL**

text、JSON 和 watch 现在都共享：

```python
filter_timeline_events()
```

JSON path 不再拥有一份独立 filter，watch 也通过 renderer 间接使用同一实现。

证据匹配也从错误的：

```text
event.id == evidence.source_id
```

扩展成：

```python
any(ref.source_id in matched_source_ids for ref in event.evidence)
```

这是正确修正。

### 仍存在 work cross-contamination

代码还加入了：

```python
e.actor_id in matched_actor_ids
and e.kind in (
    "dispatch_sent",
    "actor_started",
    "actor_returned",
    "tool_started",
    "tool_finished",
)
```

如果同一个 child session 先后承担：

```text
WORK-A
WORK-B
```

那么：

```bash
charter observe timeline --work WORK-A
```

可能把 WORK-B 的 dispatch/tool/return 一并纳入，因为两项 work 使用相同 actor ID。

现有测试让两个 work 分别属于 Gauss 和 Euler，因此没有覆盖同一 actor 的多 assignment。

此外，work filter 没有把 attached incident basis 加入 `matched_source_ids`，因此 work-specific timeline 可能漏掉该 work 的 incident。

统一函数已经完成，但 work binding 还不是严格的 event-to-work binding。

---

## 2.7 Watch anomaly degradation

**状态：MOSTLY PASS**

主 watch loop 现在将以下步骤包在内部异常边界中：

* filesystem watcher；
* snapshot collection；
* projection；
* filtering；
* rendering；
* repaint。

发生普通异常时，它会显示：

```text
[WARN] Observation update degraded: ...
```

并继续循环，不逃逸到顶层 crash reporter。

测试也覆盖了 collector 第一次失败、下一次抛出 `KeyboardInterrupt` 的场景，确认 watch 返回 0 且输出 degradation warning。

### 残余边界

以下初始化仍发生在保护区之外：

```python
effective_root = _resolve_session_root_id(...)
s_dir = subagent.get_sessions_dir()
watcher = SubagentEventWatcher(...)
```

如果这里发生异常，它仍会逃到通用 CLI handler；通用 handler 会调用 `_record_crash()`，在本地写 crash draft。

同时，cursor restoration 位于 `except KeyboardInterrupt`，而不是 `finally`。正常 Ctrl+C 没问题，但非预期退出仍可能留下隐藏 cursor。

因此“正常运行中的 transient observation error”已修复；“整个 watch command 绝不触发 crash draft”还没有形成完整结构性保证。

---

## 2.8 Actor rendering、relations 和 mutually exclusive flags

**状态：PASS**

CLI 已用真正的 mutually exclusive group 注册：

```text
--runtime-tree
--declared-relations
```

二者不能同时传入。

Renderer 现在：

* 真实并列显示 declared workflow 和 runtime topology；
* 使用 `tui.width()` 和 `tui.truncate()` 处理宽字符；
* 可视化 `peer`；
* 可视化 `reports_to`；
* 可视化 `owner`；
* JSON path 也根据 flags 过滤 relation。

这一项主体已经完成。

---

## 2.9 Inflight read-only defaults 与稳定排序

**状态：PASS**

目前：

```python
live(..., prune=False)
read_records(..., prune=False)
live_records(...)
```

均默认只读。

stale deletion 只存在于：

```python
prune_stale_records()
```

或显式 `prune=True` 路径。

Structured records 也改为：

```python
sorted(out, key=lambda r: (r["ts"], token))
```

避免同 timestamp 时依赖 filesystem iteration order。

Raw subagent tree 仍通过 `tree_scope` 排除其他 root 的 schema-2 inflight records。

### 非阻断维护问题

代码中没有看到 production path 显式调用：

```python
prune_stale_records()
```

如果 Hook 也继续使用默认 `live()`, stale 文件虽然不会影响显示，但可能永久积累。应明确维护调用点，而不是让 prune API 成为未使用代码。

---

## 2.10 Statusline marker 与 top-level command integrity

**状态：PASS，文档计数不准确**

Workflow header 已从：

```text
_HEAD_PAD + "▪ workflow"
```

改成：

```text
_HEAD_PAD + "workflow"
```

因此只显示一个：

```text
▪ workflow
```

`secret` 注册仍存在，top-level characterization test 也由 subset containment 改为 exact equality。

不过测试中的 expected set 实际有 **32 个 parser choice keys**，包括 alias 和 internal command：

```text
workspace / ws
worktree / wt
subagent / subagents
observe / obs
_version-check
...
```

不是文档所称的“25 个”。这不影响功能，但审计文档和用户描述应改成精确数量。

---

# 3. New Identified Risks

## HIGH 1 — Distinct relation declarations 会被错误去重

这是本轮最重要的新发现。

对于 child rollout 中的 `user_message`，dedupe key 是：

```python
(
    declaration.schema,
    declaration.event,
    declaration.work_id,
    session_id,
)
```

relation declaration 没有 `work_id`，所以同一 session 中：

```text
A reports_to B
C owner D
E peer F
```

三条 declaration 的 key 全部相同：

```text
(1, "relation", None, session_id)
```

第二、第三条不会产生新 event，而只会把 evidence 合并到第一条 event；后两条 relation payload 永久丢失。

这违背：

> records exact structured declarations

也会让 actor view 和 relation explain 缺失真实声明。

### 最小修正

Mirror key 应包含完整 canonical declaration identity，例如：

```python
decl_key = (
    schema,
    event,
    work_id,
    actor_id,
    relation,
    related_actor_id,
    target_session_id,
)
```

更稳妥的是：

> 只对 `spawn_agent.prompt` 与该 child 的 mirrored first `user_message` 做 dispatch declaration dedupe；不要对任意 declaration 做全局语义去重。

---

## HIGH 2 — Semantic priority 会把无效源顺序“修正”为有效生命周期

当前排序在相同 timestamp 时，始终强制：

```text
return before intake
intake before resolve
```

这解决了合法跨文件事件 timestamp 粗粒度的问题。

但以下 exact source：

```text
line 2: resolve W1
line 3: intake W1
```

若 timestamp 相同，排序后会变成：

```text
intake W1
resolve W1
```

于是本来应当产生：

```text
resolve → invalid_phase_for_resolve
```

的 declaration 会被接受。

类似地：

```text
line 2: intake W1
line 3: task_complete W1
```

也可能被重排成 return 后 intake，从而接受本来过早的 intake。

这是“topological normalization”而不是纯 observation。

### 推荐模型

* 同一 source file/session 中：严格保留 line/source ordinal；
* 不同 source 间 timestamp 相同时：只应用有证据支持的跨 source causal edges；
* 无法确定时：保留 ambiguity，而不是把声明重新排成一条成功路径。

至少应新增两个 negative tests：

```text
resolve source-order before intake, same timestamp → resolve remains unbound
intake source-order before return, same timestamp → intake remains unbound
```

---

## HIGH 3 — Event ID ordinal contract 仍然是空实现

这是本轮明确声称已经修复、但代码没有真正完成的一项。

当前状态是：

```text
API supports ordinal
ObservedEvent stores ordinal
sort key uses ordinal
event ID calls omit ordinal
```

因此 Gate 3 的字面要求仍失败。

还需要增加两个不同维度的测试：

```python
same source metadata + different local ordinal
→ different IDs
```

以及：

```python
append an unrelated later event
→ all pre-existing event IDs remain unchanged
```

第二个测试可以防止使用不稳定的全局 collection ordinal。

---

## MEDIUM 1 — Work timeline 同一 actor 多 assignment 时串线

如前述，actor fallback 会把同一个 actor 的其他 assignment 事件带入 work-specific timeline。

应在 projection 阶段建立：

```text
event_id → work_item_id | ambiguous | unbound
```

timeline filter 只依据该 binding，而不是 actor identity 猜测 work membership。

---

## MEDIUM 2 — Watch 初始化异常仍可能生成 crash draft

Watch 的循环保护已经正确，但整个函数的初始化还没有被同一 guard 包裹。

严格修正应是：

```python
try:
    resolve root
    construct watcher
    enter loop
except KeyboardInterrupt:
    return 0
except Exception:
    render degraded warning
    return 0
finally:
    restore cursor
```

这样才能真正保证：

```text
no watch anomaly → generic crash reporter
```

---

## MEDIUM 3 — Statusline 仍存在重复 filesystem scans

本轮修复没有改变 statusline collection architecture。

每次 render 仍会：

1. `find_root_session_id()` 扫描 rollout；
2. `collect_observation_snapshot()` 再构建 SessionIndex；
3. 如果没有 work item，`_subagent_section()` 又调用：

   * `find_root_session_id()`；

   * `build_subagent_tree()`；

   * `extract_subagent_exchanges()`。

所以：

```text
one snapshot + one projection
```

成立，但：

```text
avoids duplicate filesystem scans
```

仍不成立。

这在 rollout 变大后会成为 statusline hot-path 成本问题。

---

## LOW 1 — Forbidden fields with JSON null

如前述，严格 schema 应按 key presence 拒绝，而不是仅拒绝非-null value。

---

## LOW 2 — 一个测试仍存在日期时效问题

大多数 `write_test_rollout()` 已使用当前日期目录，因此没有问题。

但 `test_duplicate_rollout_files_newest_modified_at_wins` 仍手工创建：

```text
sessions/2026/08/19/
```

然后调用只扫描“当前日期往回 N 天”的 `build_session_index(max_days_back=3)`。几天后，该测试目录会落出扫描窗口。

应改成当前 UTC 日期，或向 scanner 注入 clock。

---

## Evidence limitation

仓库自审只声明：

```text
Ran 82 tests across four selected modules
```

它没有声明运行整个仓库。正式 CI 配置则运行所有 `tests/`。

本次 connector 查询未发现该 commit 的 status checks 或关联 workflow run，因此不能将“82 个定向测试通过”升级为“全量 CI 已通过”。

---

# 4. Gate Pass/Fail Scorecard

| Gate                                                      |                        Verdict | 审计结论                                                                                                                                                                 |
| --------------------------------------------------------- | -----------------------------: | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Gate 1 — Authority Boundary & Lifecycle Orthogonality** |                       **PASS** | runtime/work 坐标独立；合法 same-timestamp return/intake 路径通过；coordinator authority 严格；dispatch metadata 只来自 exact dispatch。反向同时间戳 source-order normalization 记为 Gate 4 风险。 |
| **Gate 2 — Exact Metadata & Parser Strictness**           |                       **FAIL** | 指定 parser checks 均通过，但 declaration collection 的过宽 dedupe 会吞掉不同 relation declarations，因此“exact structured declarations 被忠实记录”不成立。                                     |
| **Gate 3 — Read-Only & Deterministic Identity**           |                       **FAIL** | 正常 observer/inflight read path 和 timeline privacy 通过；生产 Event ID 没有传 source ordinal，watch 初始化异常也仍可能进入写 crash draft 的通用路径。                                            |
| **Gate 4 — Session Isolation & Causal Ordering**          |                       **FAIL** | tree-scope isolation 通过，合法 full lifecycle test 通过；但固定 subtype priority 会覆盖同一 source 的真实 declaration 顺序，并可能把无效顺序重排成有效 lifecycle。                                      |
| **Gate 5 — Dashboard & Crash Resilience**                 |           **PASS with caveat** | 单一 `▪ workflow` header 已修复；loop 内 transient anomaly 会 warning 并继续。初始化阶段和 cursor-finally 仍需硬化。                                                                        |
| **Gate 6 — Backward Compatibility & CLI Integrity**       | **PASS, execution unverified** | orphan test 已删除；CLI exact-set test、`secret`、aliases、legacy `status`、additive `runtime_state` 和 neutral stopped rendering 均保留。尚无远端全量 CI 结果。                           |

## Score

```text
3 PASS
3 FAIL
```

---

# 5. Final Recommendation

## **Changes Requested**

当前提交已经非常接近可批准状态，不需要重新设计主体架构。下一轮应集中做以下最小修正：

1. **真正将稳定的 source-local ordinal 传入所有 `make_event_id()` 调用。**
2. **将 declaration dedupe 限定为真正的 dispatch mirror，或把完整 relation identity 纳入 key。**
3. **避免 semantic priority 覆盖同一 source 的 line order；增加 reverse-order negative tests。**
4. **为 timeline 建立明确的 event-to-work binding，消除同 actor 多 assignment 串线，并纳入 incident。**
5. **将 watch 初始化、循环和 cursor restoration 统一包进 `try/except/finally`。**
6. **把 forbidden-field 检查改为 key-presence 检查。**
7. **修复固定日期测试。**
8. **运行并提供 Python 3.11–3.14 的完整 `unittest discover` CI 结果。**

完成前 3 项和完整 CI 后，核心 Gate 才能闭合；其余项目可以在同一小型修复提交中一起完成。
