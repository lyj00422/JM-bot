# Ranking and Layered Help Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add official JM daily, weekly, monthly, and all-time rankings with category/keyword filters and replace the oversized help response with topic-based help.

**Architecture:** Parse commands into canonical ranking fields, execute a dedicated catalog query that chooses category ranking or ranked site search, and normalize results into five-item Bot pages. Reuse the current query isolation, Pillow card layout, cover sender, and superuser gate; keep all user text in pure formatter functions.

**Tech Stack:** Python 3.10+, NoneBot2, OneBot v11, jmcomic 2.7.0, Pillow, asyncio, pytest/unittest

---

## File Map

- `parser.py` owns deterministic command parsing only.
- `catalog_service.py` owns ranking models, JM parameter mappings, endpoint selection, and five-item pagination.
- `query_executor.py` owns child-process timeout isolation.
- `formatter.py` owns ranking/help text and canonical follow-up commands.
- `command_service.py` owns async query concurrency and safe failure conversion.
- `search_card.py` owns the reusable bitmap layout.
- `bot.py` owns QQ routing, cover retrieval, delivery, fallback, and cleanup.
- Matching tests live under `tests/`; `README.md` remains the complete user reference.

### Task 1: Parse Ranking and Help Commands

**Files:**
- Modify: `tests/test_parser.py`
- Modify: `tests/test_command_feedback.py`
- Modify: `src/jmbot/core/parser.py`
- Modify: `src/jmbot/core/command_feedback.py`

- [ ] **Step 1: Write failing parser tests**

Add tests covering these exact assertions:

```python
def test_parse_ranking_defaults_and_free_filter_order(self):
    default = parse_jm_message("/jm 排行")
    first = parse_jm_message("/jm 排行 全彩 韩漫 月榜 2")
    second = parse_jm_message("/jm 排行 韩漫 全彩 月榜 2")
    assert (default.action, default.ranking_period, default.ranking_category, default.page) == (
        "ranking", "all", "all", 1,
    )
    assert first == second
    assert (first.keyword, first.ranking_category, first.ranking_period, first.page) == (
        "全彩", "hanman", "month", 2,
    )

def test_parse_ranking_aliases_views_and_errors(self):
    assert parse_jm_message("/jm 排行 日").ranking_period == "day"
    assert parse_jm_message("/jm 排行 COSPLAY 周榜").ranking_category == "doujin_cosplay"
    assert parse_jm_message("/jm 排行 3d 总").ranking_category == "3d"
    assert parse_jm_message("/jm 排行 文本 韩漫 周榜").search_view == "text"
    assert parse_jm_message("/jm 排行 类别").action == "ranking_categories"
    nonleading_text = parse_jm_message("/jm 排行 韩漫 文本 周榜")
    assert (nonleading_text.search_view, nonleading_text.keyword) == ("default", "文本")
    for message in ("/jm 排行 日榜 周榜", "/jm 排行 韩漫 美漫", "/jm 排行 0"):
        assert parse_jm_message(message).action == "ranking_invalid"

def test_parse_layered_and_admin_help(self):
    assert parse_jm_message("/jm help").help_topic is None
    assert parse_jm_message("/jm 帮助 排行").help_topic == "排行"
    assert parse_jm_message("/jm 帮助 unknown").help_topic == "unknown"
    assert parse_jm_admin_message("/jmg 帮助").action == "admin_help"
    assert parse_jm_admin_message("/jmg help").action == "admin_help"
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_parser.py -v`

Expected: FAIL on missing ranking actions/fields and `help_topic`.

- [ ] **Step 3: Implement parser fields and canonical maps**

Add actions `ranking`, `ranking_categories`, `ranking_invalid`, `admin_help`; add fields:

```python
ranking_period: str = "all"
ranking_category: str = "all"
help_topic: str | None = None
```

Add:

```python
_RANKING_PERIOD_ALIASES = {
    "日": "day", "日榜": "day", "周": "week", "周榜": "week",
    "月": "month", "月榜": "month", "总": "all", "总榜": "all",
}
_RANKING_CATEGORY_ALIASES = {
    "全部": "all", "同人": "doujin", "单本": "single", "短篇": "short",
    "其他": "another", "韩漫": "hanman", "美漫": "meiman",
    "cosplay": "doujin_cosplay", "3d": "3d", "英文站": "english_site",
}

def _parse_ranking(value: str) -> JmMessage:
    tokens = value.split()
    view = "default"
    if tokens and tokens[0].casefold() == "文本":
        view = "text"
        tokens.pop(0)
    if tokens == ["类别"]:
        return JmMessage(action="ranking_categories")
    page = 1
    if tokens and tokens[-1].isdigit():
        page = int(tokens.pop())
        if page < 1:
            return JmMessage(action="ranking_invalid")
    period, category = "all", "all"
    period_seen = category_seen = False
    keyword_parts: list[str] = []
    for token in tokens:
        period_value = _RANKING_PERIOD_ALIASES.get(token.casefold())
        category_value = _RANKING_CATEGORY_ALIASES.get(token.casefold())
        if period_value is not None:
            if period_seen:
                return JmMessage(action="ranking_invalid")
            period, period_seen = period_value, True
        elif category_value is not None:
            if category_seen:
                return JmMessage(action="ranking_invalid")
            category, category_seen = category_value, True
        else:
            keyword_parts.append(token)
    return JmMessage(
        action="ranking", search_view=view, keyword=" ".join(keyword_parts) or None,
        page=page, ranking_period=period, ranking_category=category,
    )
```

Match `排行` before search, replace exact public-help matching with optional `help_topic`, and match `/jmg 帮助|help` at the start of `parse_jm_admin_message`.

- [ ] **Step 4: Add processing acknowledgment**

Test and implement:

```python
if command.action == "ranking":
    return "收到，正在查询排行榜。"
```

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_parser.py tests/test_command_feedback.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/jmbot/core/parser.py src/jmbot/core/command_feedback.py tests/test_parser.py tests/test_command_feedback.py
git commit -m "feat: parse ranking and layered help commands"
```

### Task 2: Query Official Rankings in the Isolated Catalog Layer

**Files:**
- Modify: `tests/test_catalog_service.py`
- Create: `tests/test_query_executor.py`
- Modify: `src/jmbot/core/catalog_service.py`
- Modify: `src/jmbot/core/query_executor.py`

- [ ] **Step 1: Write failing endpoint and pagination tests**

Extend the fake catalog client with four-item source pages (`total=12`, `page_size=4`) and record calls. Assert:

```python
result = get_ranking_page(RankingQuery(period="week", category="hanman", page=2), client=client)
assert [item.album_id for item in result.items] == [
    "000006", "000007", "000008", "000009", "000010",
]
assert (result.page, result.page_count, result.total) == (2, 3, 12)
assert client.ranking_calls == [
    ("category", 1, "w", "hanman", "mv"),
    ("category", 2, "w", "hanman", "mv"),
    ("category", 3, "w", "hanman", "mv"),
]

get_ranking_page(RankingQuery(keyword="全彩", period="month", category="doujin"), client=client)
assert client.ranking_calls[-1] == (
    "search", "全彩", 1, {"order_by": "mv", "time": "m", "category": "doujin"},
)
```

Also assert `ValueError` for page `0`, period `year`, and category `bad`.

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_catalog_service.py -v`

Expected: FAIL because ranking models/query do not exist.

- [ ] **Step 2: Implement ranking domain types and mappings**

Add:

```python
RankingPeriod = Literal["day", "week", "month", "all"]
RankingCategory = Literal[
    "all", "doujin", "single", "short", "another", "hanman", "meiman",
    "doujin_cosplay", "3d", "english_site",
]
RANKING_PERIOD_LABELS = {"day": "日榜", "week": "周榜", "month": "月榜", "all": "总榜"}
RANKING_CATEGORY_LABELS = {
    "all": "全部", "doujin": "同人", "single": "单本", "short": "短篇",
    "another": "其他", "hanman": "韩漫", "meiman": "美漫",
    "doujin_cosplay": "Cosplay", "3d": "3D", "english_site": "英文站",
}
_RANKING_PERIOD_PARAMS = {"day": "t", "week": "w", "month": "m", "all": "a"}
_RANKING_CATEGORY_PARAMS = {
    "all": "0", "doujin": "doujin", "single": "single", "short": "short",
    "another": "another", "hanman": "hanman", "meiman": "meiman",
    "doujin_cosplay": "doujin_cosplay", "3d": "3D", "english_site": "english_site",
}

@dataclass(frozen=True)
class RankingQuery:
    keyword: str | None = None
    period: RankingPeriod = "all"
    category: RankingCategory = "all"
    page: int = 1
    page_size: int = 5

@dataclass(frozen=True)
class RankingResult:
    query: RankingQuery
    page: int
    page_count: int
    total: int
    items: tuple[SearchItem, ...]
```

- [ ] **Step 3: Implement endpoint selection and five-item repagination**

Implement `get_ranking_page(query, client=None)` with this core:

```python
catalog_client = client or create_catalog_client()
keyword = query.keyword.strip() if query.keyword else None
time_value = _RANKING_PERIOD_PARAMS[query.period]
category_value = _RANKING_CATEGORY_PARAMS[query.category]

def fetch(source_page: int):
    if keyword:
        return catalog_client.search_site(
            keyword, page=source_page, order_by="mv", time=time_value, category=category_value,
        )
    return catalog_client.categories_filter(
        page=source_page, time=time_value, category=category_value, order_by="mv",
    )

first_page = fetch(1)
total = _non_negative_int(getattr(first_page, "total", 0))
source_size = _non_negative_int(getattr(first_page, "page_size", 0))
if source_size == 0:
    source_size = max(len(getattr(first_page, "content", ()) or ()), 1)
start = (query.page - 1) * query.page_size
page_count = math.ceil(total / query.page_size) if total else 0
if total == 0 or start >= total:
    return RankingResult(query, query.page, page_count, total, ())
end = start + query.page_size
first_source = start // source_size + 1
last_source = (end - 1) // source_size + 1
source_items = []
for source_page in range(first_source, last_source + 1):
    raw_page = first_page if source_page == 1 else fetch(source_page)
    source_items.extend(_search_items(getattr(raw_page, "content", ())))
local_start = start - (first_source - 1) * source_size
return RankingResult(
    query, query.page, page_count, total,
    tuple(source_items[local_start:local_start + query.page_size]),
)
```

Before this core, validate page/page size and membership in both parameter maps.

- [ ] **Step 4: Test and add timeout wrapper**

Create `tests/test_query_executor.py` that patches `run_query_with_timeout` and asserts:

```python
query_album_ranking(query, timeout_seconds=20)
run_query.assert_called_once_with(get_ranking_page, query, timeout_seconds=20)
```

Implement:

```python
def query_album_ranking(query: RankingQuery, *, timeout_seconds: float) -> RankingResult:
    return run_query_with_timeout(get_ranking_page, query, timeout_seconds=timeout_seconds)
```

Run focused tests; expected PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/jmbot/core/catalog_service.py src/jmbot/core/query_executor.py tests/test_catalog_service.py tests/test_query_executor.py
git commit -m "feat: query official manga rankings"
```

### Task 3: Format Rankings and Layered Help

**Files:**
- Modify: `tests/test_formatter.py`
- Modify: `src/jmbot/core/formatter.py`

- [ ] **Step 1: Write failing bounded-help tests**

Replace current help tests. Assert the index has at most 7 non-empty lines, each public topic has at most 10, unknown topics list `查找｜排行｜下载｜收藏｜任务`, and `format_admin_help("/jmg")` contains admin commands but public help does not.

- [ ] **Step 2: Implement bounded help**

Use this signature and exact topic keys:

```python
def format_help(command_prefix="/jm", admin_command_prefix="/jmg", *, topic=None) -> str:
    topics = {
        "查找": ["查找漫画：", f"{command_prefix} 搜索 关键词", f"{command_prefix} 作者 作者名",
                 f"{command_prefix} 作者 文本 作者名", f"{command_prefix} 详情 123456", f"{command_prefix} 封面 123456"],
        "排行": ["排行榜：", f"{command_prefix} 排行 [文本] [关键词和/或类别] [榜单] [页码]",
                 "榜单：日榜｜周榜｜月榜｜总榜（默认总榜）", f"{command_prefix} 排行 韩漫 周榜 2",
                 f"{command_prefix} 排行 全彩 韩漫 月榜", f"{command_prefix} 排行 类别"],
        "下载": ["下载与预览：", f"{command_prefix} 123456", f"{command_prefix} 123456 zip",
                 f"{command_prefix} 123456 章节 1,3-5", f"{command_prefix} 预览 123456"],
        "收藏": ["收藏：", f"{command_prefix} 收藏 123456", f"{command_prefix} 取消收藏 123456", f"{command_prefix} 我的收藏"],
        "任务": ["任务：", f"{command_prefix} 进度 [123456]", f"{command_prefix} 撤回 123456",
                 f"{command_prefix} 队列", f"{command_prefix} 我的", f"{command_prefix} 历史 5", f"{command_prefix} 历史 失败 5"],
    }
    if topic is not None:
        return "\n".join(topics[topic]) if topic in topics else "可用帮助主题：查找｜排行｜下载｜收藏｜任务"
    return "\n".join([
        "JMbot 帮助：", f"{command_prefix} 帮助 查找  - 搜索、作者、详情、封面",
        f"{command_prefix} 帮助 排行  - 排行榜、分类和筛选", f"{command_prefix} 帮助 下载  - PDF、ZIP、章节、预览",
        f"{command_prefix} 帮助 收藏  - 收藏管理", f"{command_prefix} 帮助 任务  - 进度、队列、撤回、历史",
        f"{admin_command_prefix} 帮助      - 管理员命令",
    ])
```

Add the separate admin formatter:

```python
def format_admin_help(admin_command_prefix: str = "/jmg") -> str:
    prefix = admin_command_prefix
    return "\n".join([
        "JMbot 管理员命令：", f"{prefix} 清空", f"{prefix} kill 123456",
        f"{prefix} kill 全部 123456", f"{prefix} 插队 123456", f"{prefix} 失败",
        f"{prefix} 失败 最近 5", f"{prefix} 失败 21", f"{prefix} 失败 删除 21",
    ])
```

- [ ] **Step 3: Write failing ranking formatter tests**

For page 2, assert rank `6`, title `韩漫 · 周榜 · 关键词：全彩`, and next command `/jm 排行 文本 全彩 韩漫 周榜 3`. Also test no results, out-of-range page, category list, and syntax usage.

- [ ] **Step 4: Implement ranking formatter helpers**

Add `ranking_title`, `build_ranking_command`, `format_ranking_result`, `format_ranking_card_commands`, `format_ranking_categories`, and `format_ranking_usage`. The canonical builder order is:

```python
parts = [command_prefix, "排行"]
if text_mode:
    parts.append("文本")
if query.keyword:
    parts.append(query.keyword)
if query.category != "all":
    parts.append(RANKING_CATEGORY_LABELS[query.category])
parts.extend([RANKING_PERIOD_LABELS[query.period], str(page or query.page)])
```

`format_ranking_result` starts numbering at `(page - 1) * page_size + 1`, limits tags to six, distinguishes zero results from page overflow, and uses the canonical builder for next page.

- [ ] **Step 5: Run tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_formatter.py -v
git add src/jmbot/core/formatter.py tests/test_formatter.py
git commit -m "feat: format rankings and layered help"
```

### Task 4: Add the Async Ranking Command Service

**Files:**
- Modify: `tests/test_command_service.py`
- Modify: `src/jmbot/core/command_service.py`

- [ ] **Step 1: Write failing reply tests**

With injected query functions, assert card mode returns five cover IDs, text mode returns none, the semaphore is held during query, and exceptions become exactly `排行榜查询暂时不可用，请稍后重试。` without raw error text.

- [ ] **Step 2: Implement the service**

```python
@dataclass(frozen=True)
class RankingReply:
    message: str
    result: RankingResult | None = None
    cover_album_ids: tuple[str, ...] = ()

async def query_album_ranking_reply(
    query, *, view, semaphore, timeout_seconds, command_prefix="/jm",
    query_func=query_album_ranking,
) -> RankingReply:
    try:
        async with semaphore:
            result = await asyncio.to_thread(query_func, query, timeout_seconds=timeout_seconds)
    except Exception:
        LOGGER.exception("Ranking query failed for %s", query)
        return RankingReply(format_query_failed("排行榜"))
    message = format_ranking_result(result, command_prefix, text_mode=view == "text")
    covers = () if view == "text" else tuple(item.album_id for item in result.items)
    return RankingReply(message, result, covers)
```

- [ ] **Step 3: Run tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_command_service.py -v
git add src/jmbot/core/command_service.py tests/test_command_service.py
git commit -m "feat: add ranking command service"
```

Expected: PASS, then commit succeeds.

### Task 5: Reuse the Card Layout for Rankings

**Files:**
- Modify: `tests/test_search_card.py`
- Modify: `src/jmbot/core/search_card.py`

- [ ] **Step 1: Write a failing ranking-card test**

Create two temporary covers and a page-2 `RankingResult`; call `build_ranking_result_card` and assert the PNG exists, is at least 900 px wide and 420 px high. Keep the existing author-card test unchanged to guard compatibility.

- [ ] **Step 2: Extract and implement the shared renderer**

Make both public builders call `_build_result_card`. Use continuous ranks for ranking cards:

```python
def build_ranking_result_card(result, cover_paths, output_path, *, title, command_prefix="/jm"):
    start = (result.page - 1) * result.query.page_size + 1
    return _build_result_card(
        result.items, cover_paths, output_path,
        title=f"{title} · 共 {result.total} 条",
        page=result.page, page_count=max(result.page_count, result.page),
        numbers=range(start, start + len(result.items)), command_prefix=command_prefix,
    )
```

The shared helper must use the current card dimensions and enumeration, not item lookup:

```python
def _build_result_card(
    source_items, cover_paths, output_path, *, title, page, page_count, numbers, command_prefix,
) -> Path:
    items = source_items[:len(cover_paths)]
    if not items:
        raise ValueError("at least one result item is required")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    row_height, width, padding, header_height = 210, 960, 28, 72
    image = Image.new("RGB", (width, header_height + row_height * len(items) + padding), (248, 248, 246))
    draw = ImageDraw.Draw(image)
    title_font, body_font, small_font = _font(30), _font(23), _font(19)
    draw.text((padding, 22), f"{title}  第 {page} / {page_count} 页", fill=(28, 31, 35), font=title_font)
    for row_index, (number, item, cover_path) in enumerate(zip(numbers, items, cover_paths)):
        top = header_height + row_index * row_height
        draw.rounded_rectangle(
            (padding, top + 10, width - padding, top + row_height - 10),
            radius=12, fill=(255, 255, 255), outline=(222, 224, 226),
        )
        image.paste(_fit_cover(cover_path, (124, 176)), (padding + 18, top + 27))
        text_x = padding + 166
        draw.text((text_x, top + 28), f"{number}. JM{item.album_id}", fill=(42, 84, 130), font=body_font)
        for line_index, line in enumerate(wrap(item.title or item.album_id, width=28)[:2]):
            draw.text((text_x, top + 66 + line_index * 30), line, fill=(25, 28, 32), font=body_font)
        if item.tags:
            draw.text((text_x, top + 130), " / ".join(item.tags[:6]), fill=(91, 96, 102), font=small_font)
        draw.text(
            (text_x, top + 162),
            f"下载：{command_prefix} {item.album_id}    详情：{command_prefix} 详情 {item.album_id}",
            fill=(64, 66, 70), font=small_font,
        )
    image.save(output, format="PNG")
    return output
```

- [ ] **Step 3: Run tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_search_card.py -v
git add src/jmbot/core/search_card.py tests/test_search_card.py
git commit -m "feat: render ranking result cards"
```

### Task 6: Route and Deliver Rankings in the Bot

**Files:**
- Modify: `src/jmbot/bot.py`

- [ ] **Step 1: Route help, invalid usage, categories, and ranking queries**

Import the new types/functions and add before detail handling:

```python
if command.action == "help":
    await jm_matcher.send(format_help(
        CONFIG.command_prefix, CONFIG.admin_command_prefix, topic=command.help_topic,
    ))
    return False
if command.action == "ranking_invalid":
    await jm_matcher.send(format_ranking_usage(CONFIG.command_prefix))
    return False
if command.action == "ranking_categories":
    await jm_matcher.send(format_ranking_categories(CONFIG.command_prefix))
    return False
if command.action == "ranking":
    query = RankingQuery(
        keyword=command.keyword, period=command.ranking_period,
        category=command.ranking_category, page=command.page,
    )
    reply = await query_album_ranking_reply(
        query, view=command.search_view, semaphore=QUERY_SEMAPHORE,
        timeout_seconds=CONFIG.query_timeout_seconds, command_prefix=CONFIG.command_prefix,
    )
    if command.search_view == "text" or reply.result is None or not reply.cover_album_ids:
        await jm_matcher.send(reply.message)
        return False
    if await _send_ranking_card(bot, target, reply) is not None:
        await jm_matcher.send(reply.message)
    else:
        await jm_matcher.send(format_ranking_card_commands(reply.result, CONFIG.command_prefix))
    return False
```

- [ ] **Step 2: Keep admin help behind authorization**

Immediately after the current superuser rejection, add:

```python
if command.action == "admin_help":
    await jm_matcher.send(format_admin_help(CONFIG.admin_command_prefix))
    return False
```

- [ ] **Step 3: Implement unique temporary media and full cleanup**

Import `tempfile` and `Path`. Use `tempfile.mkdtemp` under `preview_root/ranking`. Acquire `QUERY_SEMAPHORE` while retrieving covers; copy each cover into the unique directory; remove every returned `result.media_dir` in an inner `finally`; generate the card; send through `_send_cover`; remove the unique directory in an outer `finally`. Catch all card/cover exceptions and return a non-`None` marker so the caller sends only `reply.message` as fallback.

The core cleanup structure is:

```python
async def _send_ranking_card(bot: Bot, target: QueueTarget, reply: RankingReply) -> str | None:
    media_root = CONFIG.preview_root / "ranking"
    media_root.mkdir(parents=True, exist_ok=True)
    media_dir = Path(tempfile.mkdtemp(prefix=f"{target.user_id}-", dir=media_root))
    cover_paths = []
    try:
        async with QUERY_SEMAPHORE:
            for album_id in reply.cover_album_ids:
                result = await asyncio.to_thread(
                    query_cover, album_id, CONFIG.preview_root,
                    timeout_seconds=CONFIG.query_timeout_seconds,
                )
                try:
                    target_cover = media_dir / f"{album_id}{result.cover_path.suffix}"
                    await asyncio.to_thread(shutil.copyfile, result.cover_path, target_cover)
                    cover_paths.append(target_cover)
                finally:
                    await asyncio.to_thread(shutil.rmtree, result.media_dir, True)
        card_path = await asyncio.to_thread(
            build_ranking_result_card, reply.result, cover_paths, media_dir / "ranking.png",
            title=ranking_title(reply.result.query), command_prefix=CONFIG.command_prefix,
        )
        await _send_cover(bot, target, card_path)
    except Exception:
        return "fallback"
    finally:
        await asyncio.to_thread(shutil.rmtree, media_dir, True)
    return None
```

This function is called only after `reply.result is not None`, so the card builder receives a concrete `RankingResult`.

- [ ] **Step 4: Compile and run focused verification**

```powershell
.\.venv\Scripts\python.exe -m compileall -q src
.\.venv\Scripts\python.exe -m pytest tests/test_parser.py tests/test_command_feedback.py tests/test_catalog_service.py tests/test_query_executor.py tests/test_formatter.py tests/test_command_service.py tests/test_search_card.py tests/test_sender.py -v
```

Expected: compilation succeeds and all focused tests PASS. Existing sender tests verify that the reused `_send_cover` boundary reaches both group and private OneBot APIs.

- [ ] **Step 5: Commit**

```powershell
git add src/jmbot/bot.py
git commit -m "feat: deliver ranking cards and layered help"
```

### Task 7: Document and Verify the Complete Feature

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add complete user-facing examples**

Add:

```text
/jm 排行
/jm 排行 2
/jm 排行 周榜
/jm 排行 韩漫 周榜 2
/jm 排行 全彩 韩漫 月榜
/jm 排行 文本 全彩 韩漫 月榜 2
/jm 排行 类别
/jm 帮助 排行
```

Document the four periods, default total/all category, free keyword/category order, fixed five-item pages, text mode, `/jm 排行 类别`, public help topics, and `/jmg 帮助|help`.

- [ ] **Step 2: Verify documentation entry points**

Run:

```powershell
rg -n "/jm 排行|/jm 帮助 查找|/jm 帮助 排行|/jmg 帮助" README.md
```

Expected: command examples and explanations both match.

- [ ] **Step 3: Run full verification**

```powershell
.\.venv\Scripts\python.exe -m pytest
git diff --check
git status --short
git diff --stat
```

Expected: all tests PASS; whitespace check prints nothing; only README remains uncommitted.

- [ ] **Step 4: Commit documentation**

```powershell
git add README.md
git commit -m "docs: document rankings and topic help"
```

- [ ] **Step 5: Verify final history and clean worktree**

```powershell
git log -7 --oneline
git status --short
```

Expected: all ranking/help commits are visible and status prints nothing.
