### [app/services/network_policy.py] critical/security located=true
allowlist 模式的全部隔离工作（netns + veth + iptables 链）实际上不会生效：本函数无论 mode 是什么、netns 是否创建成功，都只返回 `--unshare-net`，即 bwrap 会为沙箱新建一个**全新的、与本引擎创建的 `cds-*` netns 完全无关**的网络命名空间。结果是：沙箱既不进入 `cds-*` netns，也不经过 veth/iptables 链，域名/IP 过滤全部落空（而新 netns 默认无网络，则表现为“恰好”全禁，掩盖了真实实现缺失）。同时 `netns_name` 变量声明后从未被使用，说明这是未完成实现。注释里写的“bind-mount the configured one”与代码行为不符。建议：返回 `["--netns", f"/run/netns/{netns_name}"]`（bwrap 支持 `--netns`），或在沙箱启动前用 `ip netns exec`/`setns` 真正进入该 netns；否则不应向调用方宣称 allowlist 已强制执行。
-- suggested: 

### [app/services/network_policy.py] critical/security located=true
只要 `allowed_ips` 为空就完全不建任何 iptables 规则，这是 fail-open：典型的“只配 allowed_domains、不配 allowed_ips”策略下，netns 里没有任何过滤规则（默认 policy ACCEPT），沙箱可以直连任意 IP/端口；域名限制仅依赖 DNS 代理，而代理又监听在 netns 不可达的 127.0.0.1（见 `_start_dns_proxy`），且没有任何 53 端口 DNAT 劫持，攻击者只要直接用硬编码 DNS（如 1.1.1.1）或直连 IP 即可完全绕过。正确做法：allowlist 模式下无条件建立链，并在链中显式放行 DNS/loopback/established 后以 DROP 结尾（默认拒绝），把 `allowed_ips` 为空理解为“不放行任何 IP”，而不是“跳过过滤”。此外若此处 `_create_iptables_chain` 返回空串（内部异常被吞掉），也应视为失败并降级为 deny_all，而不是继续在无规则环境下上报 allowlist。
-- suggested: 

### [app/services/network_policy.py] high/bug located=true
`_create_netns` 忽略了所有 `subprocess.run` 的 `returncode`/stderr，只要不抛异常就 `return True`。`ip netns add`、`ip link add`、`ip addr add`、`sysctl` 等只要因权限不足（非 root）、同名 netns/veth 已存在、`10.66.0.1/24` 与既有接口冲突、`ip` 命令不支持某子命令等原因失败，调用方仍会收到“策略已强制执行”（`netns_name`/`veth_host` 均被填上并写审计日志），实际处于未隔离状态——这是安全上最危险的静默失败。建议：用 `check=True`（或显式检查 returncode）逐步执行，失败时回滚已创建的资源（link/netns/MASQUERADE 规则）并返回 False；`subprocess.TimeoutExpired` 被外层 `except Exception` 吞掉后同样会留下半创建状态，需要单独的清理逻辑。
-- suggested: 

### [app/services/network_policy.py] high/security located=true
上游转发存在响应错配与 transport 泄漏两类问题：1) `datagram_received` 完全不校验响应与 `original_query` 的匹配关系（事务 ID、问题段），也不校验 `addr`，任何在该 UDP socket 上收到的报文都会被原样回给沙箱，等同于放弃了 DNS 响应的基本校验，增加污染/串扰风险；2) 没有超时兜底——若上游不回复或只回复部分报文，`connection_made` 里创建的 upstream transport 永远不会 `close()`，每个查询泄漏一个 fd/socket（高 QPS 下会耗尽 fd）；`error_received` 同样只 close 不向客户端回 SERVFAIL，客户端只能等到自身超时。建议：缓存 `original_query` 的 qid+question 做匹配校验；用 `asyncio.wait_for` + 超时后 `transport.close()`（并在超时/异常路径统一回 SERVFAIL），或改用 `loop.create_datagram_endpoint(..., sock=...)` 复用单一上游 socket 并用 `qid_map` 做映射，配合 TTL 清理过期条目。另外 `_handle_query` 里创建的 `_pending`/`_query_id_map` 正是为此准备的，但目前是死代码。
-- suggested: 

### [app/services/network_policy.py] high/bug located=true
DNS 代理监听地址与数据面不匹配，域名过滤实际不可达：netns 内沙箱的默认路由经 `10.66.0.1`（宿主 veth 端），`_create_iptables_chain` 也只放行 `udp/53 → 10.66.0.1`；而代理绑定在 `127.0.0.1:<10053+>`，沙箱报文既到不了 127.0.0.1，端口也不是 53。要真正生效，应绑定 `0.0.0.1:53` 或 `10.66.0.1:53`，或用 iptables `DNAT` 把 netns 出向 53 流量重定向到代理地址，并向沙箱注入 resolv.conf/`nameserver 10.66.0.1`。另外此处 `protocol` 变量未被使用；`create_datagram_endpoint` 的 `OSError`（端口被占用、权限不足、端口递增越过 65535）没有捕获，会让整个 `create_policy` 直接抛错；`self._dns_proxies` 的声明类型是 `dict[str, asyncio.AbstractServer]`，实际存的是 `DatagramTransport`，类型注解与实现不一致。
-- suggested: 

### [app/services/network_policy.py] high/bug located=true
这里用全局计数器 `self._dns_port_counter - 1` 猜测“本会话的代理端口”，但该计数器被所有会话共享且单调递增：只要有其它会话在其之后启动过代理（并发场景几乎必然），返回的就是**别的会话的端口**，沙箱会被指向错误的（甚至已关闭的）DNS 代理，导致域名过滤串到别的会话或彻底失效。正确做法是把 `_start_dns_proxy` 返回的端口随策略保存（例如 `self._dns_proxy_ports[session_id] = port` 或直接记录 transport 上的 `get_extra_info("sockname")`），在此按 session 查表。
-- suggested: 

### [app/services/network_policy.py] high/security located=true
运行时兜底检查在此 fail-open：当 `mode == "allowlist"` 且 `allowed_ips` 为空（只配了域名白名单）时，`if config.allowed_ips:` 为假直接落到 `return True`，任意目的 IP 都被放行。这与 `DNSProxyProtocol._is_domain_allowed`（白名单为空时 `return False`，即默认拒绝）语义相反，两处对“空白名单”的解释不一致。零信任下应为：`allowed_ips` 为空即拒绝一切 IP 直连（或显式配置 `0.0.0.0/0`），而不是隐式允许。
-- suggested: 

### [app/services/rag_service.py] critical/security located=true
这里用 `repr()` 把调用方传入的 `generative_model_id` / `embedding_model_hash` 直接拼进生成的 Python 源码,而 `str` 与 `bytes` 的 repr 都可以携带可执行前缀:`b'x'.__reduce__()` 的结果是 `b"(os.system)('rm -rf /')\n"`,其 repr 会输出 `b"..."` 后紧跟一行 `(os.system)('rm -rf /')\n`。于是只要这两个字面量之一(或将来新增的任何字面量)来自 API 请求体而非 `build_corpus()` 的计算结果,攻击者就能让 `b'x'.__reduce__()` 在生成的 runner 模块顶层执行任意代码,而调用方只需在 `build_rag_runner()` 之后原样提交这段代码。

更关键的是 `validate_rag_runner()` 无法防御:它同样把这些值当"不透明字面量"原样回传给 `build_rag_runner()`,生成的期望代码与攻击代码字节一致,校验照样通过("byte-exactness 保证安全"的前提被打破)。此外 `top_k` 只校验 `ast.Constant`,复数/浮点字面量会绕过 `int()` 归一化直接写入模板。

建议:对每个字面量做白名单校验并用显式序列化(如 `json.dumps`)而非 repr,例如 `re.fullmatch(r"[A-Za-z0-9._:-]+", v)` 校验 model id / hash,`top_k` 必须 `isinstance(v, int) and not isinstance(v, bool)`,并在 `validate_rag_runner` 的期望重建路径与生成路径上使用同一套严格校验。
-- suggested: 

### [app/utils/watermark.py] high/bug located=true
**参数丢失导致必然的验证失败**:`embed_image_watermark` 支持 `bits_per_channel=1..4`,但 `verify_image_watermark` 既没有该参数,也没有把它透传给 `extract_image_watermark`,因此对任何以 `bits_per_channel > 1` 嵌入的图片,这里都会用默认 1 bit 去解码,只能得到乱码或 None,验证永远返回 False(假阴性)。同理此处用子串包含 `in` 而非 `==`,存在假阳性风险,建议改为等值比较。
-- suggested: expected = generate_watermark(user_id, session_id)
    extracted = extract_image_watermark(image_bytes, bits_per_channel=bits_per_channel)
    return extracted == expected

### [sdk/cds_sdk/client.py] high/bug located=true
`_parse_error` 只在 `resp.json()` 抛出 `ValueError`（JSONDecodeError）时才走兜底分支，但 `payload.get(...)` 对非 dict 的合法 JSON 会抛 `AttributeError`，而这个异常没有被捕获：当服务端返回 JSON 数组（`[...]`）、字符串（`"error"`）或 `null` 时，`payload.get` 直接抛 `AttributeError`，真正的 API 错误（状态码/detail）被吞掉，调用方看到的是一个与业务无关的崩溃。建议先解析 JSON，再用 `isinstance(payload, dict)` 判断取 detail。
-- suggested: try:
        payload = resp.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        detail = payload.get("detail", payload)
    elif payload is None:
        detail = resp.text
    else:
        detail = payload

### [sdk/cds_sdk/client.py] high/security located=true
用户可控的 `filename` 未做 URL 编码就直接拼进请求路径。httpx 的 `_merge_url` 对相对路径只做拼接、不做 dot-segment 规范化，因此 `filename="../../admin/users"`、`"..%2F..%2Fx"`，或含 `?`/`#` 的文件名，都会改写实际请求的路径/查询串，可能越权访问同一 API 下的其他端点（路径穿越 / 请求走私式改写）。`delete_file()`（以及 `download_dataset_bundle` 中的 `bundle_id`）存在同样问题。建议用 `urllib.parse.quote(value, safe="")` 对所有插入路径的标识符统一编码后再拼接。
-- suggested: def download_file(self, session_id: str, filename: str) -> bytes:
        safe_name = quote(filename, safe="")
        resp = self._client.get(f"/api/v1/sandbox-sessions/{session_id}/files/{safe_name}")

### [sdk/cds_sdk/stream.py] high/bug located=true
**ticket / session_id 未做 URL 编码，凭证可能被截断且存在参数注入面**

`ticket` 直接拼接进 query。一次性 ws-ticket 通常是 base64/URL-不安全字符集（含 `+`、`/`、`=`），拼接后服务端按 `application/x-www-form-urlencoded` 解析 query 时，`+` 会被解成空格、`&`/`#` 会截断 ticket，导致握手 401/票据校验失败（表现为随机性连接失败，极难排查）；同理 `session_id` 若含 `/`、`?`、`#`、`&` 会篡改请求路径或注入额外 query 参数。建议对两个值都做显式转义。
-- suggested: path = (
        f"/api/v1/sandbox-sessions/{quote(session_id, safe='')}/exec/stream"
        f"?ticket={quote(str(ticket), safe='')}"
    )

### [sdk/cds_sdk/stream.py] high/bug located=true
**recv 超时被当作“正常结束”，调用方拿到被截断的输出却无从分辨**

`_is_closed_error` 把 `WebSocketTimeoutException` / `TimeoutError` 与连接关闭同等对待并直接 `return`。但 recv 超时只代表 `recv_timeout`（默认 30s）内没有新帧——沙箱内长时间静默运行的命令（编译、拉数据、跑模型）完全可能超过这个间隔，此时迭代器静默结束且没有 `exit` 帧，调用方会误判为“命令执行完毕”，把不完整输出当成完整结果，这与本模块 fail-closed / 不做假流式的整体语义相冲突。此外文档声明客户端帧包含 `ping`，但代码从未发送任何心跳，长静默期极易被服务端 idle 超时断开，进一步扩大该误判窗口。建议：超时单独产出终止帧（或抛 `CDSError`），并实现 ping 心跳或明确文档化“超时即失败”。
-- suggested: except Exception as exc:  # server closed / timeout
                if _is_timeout_error(exc):
                    yield {"type": "error", "code": "STREAM_RECV_TIMEOUT",
                           "message": f"no frame within {recv_timeout}s; output may be incomplete"}
                    return
                if _is_closed_error(exc):
                    return

### [sdk/pyproject.toml] critical/bug located=true
`readme = "README.md"` 被错误地放在 `[project.optional-dependencies]` 表下，TOML 会把它解析成一个名为 `readme` 的可选 extra（其值为字符串 `"README.md"`），而不是项目元数据。后果有两个：1) 打包元数据里完全没有 long_description，PyPI/内部源上的项目页会缺失 README；2) 该 extra 的取值不是合法的 PEP 508 requirement 列表，`pip install cds-sdk[readme]` 或 `pip install -e ".[stream]"` 在依赖解析/元数据校验阶段可能直接报错（setuptools 与 pip 对 optional-dependencies 的 value 类型校验很严格）。同时下游用户会误以为存在名为 readme 的可选功能。应把它移到 `[project]` 段（如 `description` 之后）。
-- suggested: stream = ["websocket-client>=1.6"]

### [sdk/pyproject.toml] high/bug located=true
打包版本与代码内声明的版本不一致：`sdk/cds_sdk/__init__.py` 仍是 `__version__ = "0.2.0"`，而此处为 `0.3.0`。用户通过 `pip show cds-sdk` 看到 0.3.0、通过 `cds_sdk.__version__` 看到 0.2.0，排障与依赖约束（以及问题上报）会出现偏差。建议二者同步，或改为单一来源的动态版本：在 `[project]` 增加 `dynamic = ["version"]`，并配置 `[tool.setuptools.dynamic] version = {attr = "cds_sdk.__version__"}`，避免后续再次漂移。
-- suggested: version = "0.2.0"  # 与 cds_sdk.__version__ 保持一致，或改用 dynamic version

### [app/services/agent_tools.py] medium/bug located=true
allowlist 模式串没有像 host 那样去掉尾部 "."：host 做了 `rstrip(".")`，但 pattern 只做了 `strip().lower()`。k8s/DNS 配置里常见的 FQDN 写法（如 `api.example.com.`、`*.example.com.`）在这里永远匹配不上已经去点的 host，导致整条 allowlist 静默失效（全部拒绝），而且与文件头声明的“mirrors the k8s network policy matcher”以及 network_policy.py DNS 代理里 `[d.lower().rstrip(".")]` 的归一化行为不一致。建议对 pattern 同样 rstrip(".")，并对无效模式打日志。
-- suggested: host = host.rstrip(".").lower()
    for pattern in allowed_domains:
        pattern = pattern.strip().rstrip(".").lower()

### [app/services/agent_tools.py] medium/security located=true
两处问题：1) 只捕获 `ValueError`，但 `url` 非字符串（例如 JSON 里传入 int/dict/list）时 `urlparse` 抛的是 `AttributeError`/`TypeError`，会直接冒泡到调用方；如果上层用宽泛 try/except 包住这个门禁，很容易变成 fail-open。2) 只校验 hostname，不校验 scheme 与端口：`ftp://`、`gopher://`、`http://allowed.example.com:6379/` 都会返回 True，而 schema 与描述明确声明只支持 http(s)，端口收敛也只在 iptables 层兜底。作为应用层门禁应显式限制 scheme（和可选的端口集合）。
-- suggested: if not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname
    if not host:
        return False
    return _host_allowed(host, allowed_domains) or _ip_allowed(host, allowed_ips)

### [app/services/agent_tools.py] medium/bug located=true
类型校验存在两处宽松边界：1) `bool` 是 `int` 子类，`True`/`False` 能通过 `integer` 与 `number` 校验（如 `timeout=True` → 实际按 1 处理），语义上是脏数据；2) 校验器完全忽略 `enum`/`minimum`/`maximum`/数组元素类型，而本文件 schema 恰恰依赖这些约束表达安全边界——`http.method` 描述写“GET/POST”但可传 `DELETE`/`PUT`，`timeout` 可传 `10**9` 让沙箱长时间挂起，`top_k` 可为负数。建议在 schema 中补 `enum`/范围并在本方法中强制执行，否则描述里的约束只是注释。
-- suggested: if isinstance(value, bool) and expected in ("integer", "number"):
                return f"{self.id}: parameter {key!r} must be a number, not a boolean"
            if expected == "integer" and not isinstance(value, int):
                return f"{self.id}: parameter {key!r} must be an integer"
            if expected == "number" and not isinstance(value, (int, float)):
                return f"{s

### [app/services/agent_tools.py] medium/security located=true
`validate_run` 是注册表唯一的 run 级校验入口，但完全没有使用 `AgentTool.roles`（`budget_cost` 同样未参与校验）。如果调用方把 `validate_run` 当作工具选择的授权门禁，`buyer` 也能选中 `inference`/`http`/`llm_reason` 等只对 admin/operator/data_provider 开放的工具，角色声明形同注释。建议增加角色参数并在此处校验（或在文档中明确写出“角色/预算由调用方另行强制”，并补一个统一的 `validate_run(tool_ids, role, budget)` 入口）。
-- suggested: seen.add(tool_id)
            tool = self.get(tool_id)
            if tool is None:
                return f"unknown tool {tool_id!r}"
            if roles and tool.roles and not (set(tool.roles) & set(roles)):
                return f"tool {tool_id!r} not permitted for role(s) {sorted(roles)}"
        return None

### [app/services/agent_tools.py] medium/bug located=true
模块文档声明“Network-capable tools carry `network_targets` and are gated at runner build time”，但唯一真正具备网络能力的 `http` 工具 `network_targets` 恒为空列表——URL 是运行期参数，无法在此静态声明。任何依据 `network_targets` 非空来判定“该工具需要网络门禁”的 runner 构建逻辑都会漏掉 `http`，从而在没有网络白名单的会话里把它挂进沙箱。建议显式增加 `network_capable=True` 标志（或保留占位 sentinel），让门禁判定不依赖这个易漏的空列表。
-- suggested: network_targets=["*runtime-url*"],  # 运行期由 validate_network_target 逐次校验

### [app/services/agent_tools.py] medium/security located=true
工具语义是“对 data 求值的 python 表达式”，但 schema 只要求 `expression` 是字符串，注册表层没有任何受限语法约束（无 AST 白名单、无禁用 `__builtins__`/import/dunder 访问的声明）。这使任意代码执行的可能性完全取决于下游执行器实现；一旦下游图省事直接 `eval(expression, {...})` 并把模块globals 带入，就会绕过 code_scanner 的 import 白名单设计与数据域隔离。建议在注册表内固化受限求值契约（如提供 `ALLOWED_NAMES`/强制 `simpleeval`-类求值器），并在此处对表达式做静态拒绝（import、`__`、`exec`/`open`/`getattr` 等）。
-- suggested: 

### [app/services/agent_tools.py] medium/security located=true
描述承诺“只读 SELECT（SQL 白名单，非 DML）”，但校验只做到“是个字符串”，注册表没有提供任何语句级约束：多语句拼接（`SELECT 1; DROP TABLE ...`）、`INSERT ... RETURNING`、CTE 内 DML、`PRAGMA`/`ATTACH`（SQLite/DuckDB）都能原样通过。白名单承诺写在 schema 描述里等于没有实现，建议在本文件暴露一个 `validate_query()`（单语句 + 以 SELECT 开头 + 解析器 AST 校验），由 http/db 工具执行路径统一调用。
-- suggested: 

### [app/services/memorization_check.py] medium/bug located=true
这里用 `generated_text.split()` 重新统计 `total_ngrams`，与 `mia_probe.check_text_memorization` 的分母口径不一致：后者分母是**去重后**的 n-gram 集合大小 `len(gen_ngrams)`，而这里是**含重复**的位置总数，因此 `int(ratio * total_ngrams)` 推导出的 `matched_ngrams` 与真实匹配数不对应（重复 n-gram 越多偏差越大）。更严重的是 `check_text_memorization` 对每个 reference 都累加 `len(overlap)`，同一个生成 n-gram 命中多条 reference 会被重复计数，返回的 ratio 可能 > 1.0，导致 `matched_ngrams > total_ngrams` 且 risk_level 恒为 "critical"。此外 `int()` 向下截断会稳定少报 1 个匹配。建议由被调方直接返回匹配/总数，或至少对 ratio 做 clamp 并用 `round()`。
-- suggested: words = generated_text.split()
        total_ngrams = max(0, len(words) - self.ngram_size + 1)
        unique_ngrams = len({tuple(words[i:i + self.ngram_size]) for i in range(total_ngrams)})
        ratio = min(ratio, 1.0)
        matched_ngrams = min(round(ratio * unique_ngrams), unique_ngrams)

### [app/services/memorization_check.py] medium/bug located=true
`risk_level` 的分档完全硬编码（0.1/0.3/0.5/0.8），与可配置的 `memorization_threshold` 脱钩，二者会给出矛盾结论：阈值为 0.8 时 ratio=0.45 被标为 "medium" 但 `is_memorized=False`；阈值为 0.05 时 ratio=0.05 已判定记忆化却标为 "safe"。调用方通常同时消费这两个字段，标签会误导判定。建议以 `self.memorization_threshold` 为基准做相对分档（如 threshold 的 1/3、1/2、1x、2x），或让 `MemorizationResult.risk_level` 复用 `MIARiskLevel` 并由阈值推导。
-- suggested: t = self.memorization_threshold
        if ratio < t / 3:
            risk_level = "safe"
        elif ratio < t / 2:
            risk_level = "low"
        elif ratio < t:
            risk_level = "medium"
        elif ratio < t * 2:
            risk_level = "high"
        else:
            risk_level = "critical"

### [app/services/memorization_check.py] medium/bug located=true
`percentile` 是公开参数但未限制在 [0, 100]：传入负值时 `idx` 为负，`sorted_ppl[idx]` 会走 Python 负索引取到接近**最大**的困惑度，与“低于该阈值疑似记忆化”的语义完全相反（且 `min(idx, len-1)` 只做了上界钳制，没有下界钳制）；传入非数值还会直接抛 TypeError。此外 `int(len * p / 100)` 相对最近秩（nearest-rank）定义偏了一位。建议显式校验并双向钳制索引。
-- suggested: if not 0.0 <= percentile <= 100.0:
            raise ValueError("percentile must be between 0 and 100")
        sorted_ppl = sorted(validation_perplexities)
        idx = int(math.ceil(len(sorted_ppl) * percentile / 100)) - 1
        idx = max(0, min(idx, len(sorted_ppl) - 1))
        return sorted_ppl[idx]

### [app/services/network_policy.py] medium/bug located=true
`self._policies[session_id] = config` 直接覆盖，但没有对已存在的旧策略做任何清理：重复调用 `create_policy` 时，旧的 DNS proxy transport（`_dns_proxies` 中的条目）不会被关闭，`_dns_port_counter` 继续增长，宿主侧 `cds-*` netns / veth / MASQUERADE 规则也不会先删除就再次 `ip netns add`（必然失败并被静默忽略），最终 `_dns_proxies[session_id]` 被新 transport 覆盖，旧 transport 永久泄漏。建议：入口处若已有策略先 `await self.remove_policy(session_id)`（或提取 `_teardown` 幂等清理），再创建新策略。
-- suggested: 

### [app/services/network_policy.py] medium/bug located=true
降级路径与返回值不一致：`result` 在函数开头就用 `config.mode`（此时仍是 `"allowlist"`）构建，随后把 `config.mode` 原地改成 `"deny_all"` 却直接返回旧的 `result`，调用方与 API 响应会认为 allowlist 已生效（且上面的 `log_egress_event` 已按 `mode:allowlist` 写入审计），实际却降级为无网络。此外，直接修改传入的 `config` 对象是对调用方数据的副作用（调用方可能仍持有同一实例用于展示/重试）。建议：不修改入参，改为同步更新 `result["mode"] = "deny_all"` 并附加 `result["fallback_reason"]`，同时补一条降级审计事件。下面 `ns_created` 失败分支存在同样问题。
-- suggested: 

### [app/services/network_policy.py] medium/bug located=true
这里只判断了 `mode == "deny_all"`，之后没有任何对 `mode == "allowlist"` 的校验就进入“受控网络”分支。`NetworkPolicyConfig.mode` 是外部 API 可控的自由字符串，任何拼写错误或未支持取值（如 `"allow-list"`、`"AllowAll"`、`""`）都会被当成 allowlist 走 netns/iptables 创建路径，并在未建立有效过滤规则时被上报为已强制执行。建议：在入口处显式白名单校验 `mode in ("deny_all", "allowlist")`，非法值直接 `raise ValueError`（fail-closed）。
-- suggested: 

### [app/services/network_policy.py] medium/bug located=true
`datagram_received` 里用 `asyncio.ensure_future(...)` 创建任务后立即丢弃引用：既没有保存 task 弓引用（可能被 GC 提前回收），也没有任何异常处理——`_handle_query` 一旦抛异常就是“Task exception was never retrieved”，而沙箱只能等自身超时。叠加上面 `struct.unpack("!H", data[offset:offset + 2])` 在指针落在报文末尾时（切块长度没有 2 字节）会抛 `struct.error`，以及 `data[6:12]` 之后的字段解析对恶意/截断报文完全无防护，攻击者（沙箱内任意进程）只需发送精心截断的 UDP 报文就能持续触发未捕获异常；同时每报文一个任务、无并发/速率上限，构成放大与无背压风险。建议：在 `_handle_query` 外层包 `try/except Exception` 并在异常时回 SERVFAIL（fail-closed）；用 `asyncio.create_task` + 集合保存强引用并加 `done_callback` 记录异常；解析前对偏移量做边界校验；对单会话 QPS 复用 `_rate_limiters` 做限流。
-- suggested: 

### [app/services/network_policy.py] medium/security located=true
域名判定只看第一个 question，但 `qdcount` 解出来后从未使用，而转发给上游的仍是**完整原始报文**（`_UpstreamDNSProtocol(data, ...)`）。在 `qdcount >= 2` 的报文里，攻击者可把第一个 question 设为白名单域名、第二个设为任意非白名单域名，本代理判定“允许”并原样转发，上游回复会带回第二个域名的解析结果，从而绕过域名白名单。同样，解析失败或解出空域名（根域查询，`parts` 为空）时，`if not domain: return` 直接静默丢弃报文（不回 NXDOMAIN/SERVFAIL），属于“挂起”而非 fail-closed。建议：`qdcount != 1` 或域名解析失败时直接回 REFUSED/SERVFAIL，并在转发前重写（或只保留第一个）question 段。
-- suggested: 

### [app/services/network_policy.py] medium/security located=true
所有会话共用同一子网 `10.66.0.0/24` 与同一宿主地址 `10.66.0.1`，但每个会话都有自己的 veth，宿主上因此会存在多条同前缀、不同出接口的 connected 路由。宿主主动发往 `10.66.0.2` 的报文（典型如 netns 内向 `10.66.0.1` 发DNS 查询后的回复，因为没经过 NAT、靠普通路由选路）可能从**另一个会话**的 veth 送出，造成跨会话报文串扰/信息泄露；新会话与旧会话并存时还会引发路由混乱。建议：按会话从受控地址池分配互不重叠的 /30 或 /24（用 `_netns_counter` 派生子网并随策略记录），清理时归还。
-- suggested: 

### [app/services/network_policy.py] medium/bug located=true
每个会话都追加**完全相同**的 `iptables -t nat -A POSTROUTING -s 10.66.0.0/24 -j MASQUERADE`（子网写死），而 iptables 允许重复添加同规则，因此 nat 表会随会话数线性膨胀；只要某次 `remove_policy` 未执行（异常、进程重启使 `_policies` 内存态丢失、`create_policy` 中途失败），规则就永久残留。清理侧用 `-D` 只删第一条，无法反映真实状态。建议：全局只添加一次（引用计数或先 `-C` 判断存在），或按会话维度限定规则（如 `-o <veth_host>` / 按分配的唯一子网），并在清理时循环删至不存在。
-- suggested: 

### [app/services/network_policy.py] medium/bug located=true
`if not config: return` 会跳过全部宿主侧资源清理。`self._policies` 是进程内存态：服务重启、多进程部署（每个 worker 各自一份字典）或 `create_policy` 中途抛异常都会导致“内存里没有、但 `cds-*` netns / veth / iptables 链 / MASQUERADE 规则 / DNS proxy 端口仍然存在”。此时清理直接短路，隔离残留会一直占用内核资源，且旧会话的 netns/路由可能与新会话同名冲突（`ip netns add` 失败又被静默忽略）。建议：无论内存是否有记录，都按 `cds-{session_id[:12]}` 幂等地执行一次拆除（先 `-C`/存在性判断再删）；长期看应将策略与 netns 名称持久化。
-- suggested: 

### [app/services/network_policy.py] medium/bug located=true
作为“iptables 不可用时的运行时兼容检查”，本函数只校验了端口和 IP，完全忽略了 `config.deny_out`（以及 `allowed_domains`）：显式列入deny 名单的目标在这里会被返回 `True`，即降级路径下“显式拒绝”失效——这与字段注释声称的 deny 语义、以及零信任“默认拒绝 + 显式拒绝优先”的预期相远。建议：先对 `deny_out` 做匹配（命中即返回 False），再走 allow 判定；并在拒绝/放行分支补上审计上报，以便与 iptables 路径的审计覆盖面保持一致。
-- suggested: 

### [app/services/network_policy.py] medium/performance located=true
`_create_iptables_chain` / `_remove_iptables_chain` 是同步函数，在 `create_policy` / `remove_policy` 的 async 调用链里直接逐条执行 `subprocess.run(..., timeout=5)`。规则数为 `len(allowed_ips) × len(allowed_ports)`（再加上 5 条固定规则和 3 个 hook，共约 8+N 次进程创建），在策略 API 的请求路径上会**同步阻塞整个事件循环**，极端情况下（`ip netns exec` 卡住）单条就阻塞 5 秒、整个函数可达数十秒，拖垮同进程内所有会话的请求。同时这些调用同样未检查 `returncode`：建链失败仅记志并返回空串，`create_policy` 仍上报 allowlist 已生效。建议：把整段放到 `loop.run_in_executor`（并合并为 `iptables-restore` 单次调用以大幅减少子进程数），失败时抛异常或触发降级为 deny_all。
-- suggested: 

### [app/services/opa_client.py] medium/security located=true
`package_path` 仅做 `.` → `/` 替换后就拼进请求路径，既没有字符白名单校验，也没有做百分号编码。由于 httpx 会把该字符串按 URL 解析并直接与 base_url 的 raw_path 拼接（不做 `..` 归一化），若 `package_path` 含 `../`、`?`、`#` 或以 `/` 开头，就可能改变实际请求路径或注入查询参数（例如 `?bundle=true`），从而访问到 `/v1/policies`、`/v1/data` 之外的 OPA 端点。当前唯一调用方 `policy_evaluator._evaluate_via_opa` 传入的是 UUID 派生值，暂未构成可利用的注入，但这是一个对外暴露的通用客户端，任何新调用方传入用户可控值（如策略名/合约名）就会立刻变成路径遍历/参数注入。建议统一走一个私有的路径构造函数：先正则校验（如 `^[A-Za-z0-9_\-.]+$` 分段），再用 `urllib.parse.quote(..., safe="/")` 编码。
-- suggested: path = self._encode_path(package_path)
            resp = await client.put(

### [app/services/opa_client.py] medium/bug located=true
`resp.json()` 的返回值类型完全由 OPA 响应体决定，并不保证是 `dict`：OPA 在策略返回顶层数组/标量、或某些代理返回 JSON 数组时，这里会把 `list`/`str`/`int` 直接返回，与方法签名 `dict | None` 矛盾。下游 `policy_evaluator` 里 `result.get("result", False)` 会抛 `AttributeError`，而该异常又被它自己的宽泛 `except Exception` 吞掉并退化成“OPA 不可用 → Python fallback”，最终表现为策略被静默降级评估，排障极其困难。建议在此处显式校验类型，非 dict 时记录错误并返回 None。
-- suggested: if resp.status_code == 200:
                payload = resp.json()
                if not isinstance(payload, dict):
                    logger.error("OPA evaluate returned unexpected type %s", type(payload).__name__)
                    return None
                return payload

### [app/services/opa_client.py] medium/maintainability located=true
四处 `except Exception` 过宽，且只记录 `str(e)`，丢失 traceback。这里真正需要兜底的是网络层异常（`httpx.HTTPError`，含超时/连接失败）与 JSON 解析异常；而 `self._base_url` 配置非法、`rego_source`/`input_data` 类型错误等编程或配置错误也会被同样吞掉并返回 `False`/`None`，在安全组件里这等价于“配置写错也悄悄降级到 Python PDP”，永远不会被发现。建议缩小异常范围，并用 `logger.exception`（或 `exc_info=True`）保留堆栈。
-- suggested: except httpx.HTTPError as e:
            logger.exception("OPA push error: %s", e)
            return False

### [app/services/opa_client.py] medium/bug located=true
`close()` 关闭客户端后没有把 `self._client` 置回 `None`，也没有记录“已关闭”状态；而 `_get_client()` 的判断条件是 `is_closed`，因此应用进入 shutdown 之后（`main.lifespan` 已调用 `opa_client.close()`）若有迟到的请求仍会走到这里，就会新建一个永不被关闭的 `AsyncClient`（连接池泄漏），在测试中反复启动/停止 lifespan 时还会累积多个连接池。建议 `close()` 中置 `None` 并设置 `_closed` 标志拒绝重建，同时提供 `__aenter__/__aexit__` 便于显式生命周期管理。
-- suggested: async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def __aenter__(self) -> "OPAClient":
        await self._get_client()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

### [app/services/rag_service.py] medium/bug located=true
`_ensure_runner_replicable_engine` 中的 transformers 分支不可达:`rag_embedding._RUNNER_REPLICABLE_ENGINES = {"tf", "regex", "transformers"}` 已经把 `transformers` 列入,所以 `engine not in _RUNNER_REPLICABLE_ENGINES` 先为假,`ship_transformers=False` 时也无法 fail-closed。结果是 `RAG_EMBEDDING_BACKEND=transformers` 且未带模型包时,`build_corpus` 仍会构建 transformers 语料(无 bundle/hash),`prepare_corpus_for_task` 也下载不到 bundle,runner 只能在沙箱内运行期报 "embedding model not shipped",而模块文档声称的 fail-closed 完全失效。建议改为显式白名单 `{"tf", "regex"}` 判断,不要复用包含 transformers 的常量。
-- suggested: if engine == "transformers" and not ship_transformers:
        # NOTE: 必须用 {"tf", "regex"} 显式判断, 不能用 _RUNNER_REPLICABLE_ENGINES
        # (它已包含 transformers, 会让本分支变成死代码, fail-closed 失效)

### [app/services/rag_service.py] medium/bug located=true
沙箱侧 runner 只支持 512 维的 tf/regex 向量:`_embed_query` 里调用 `tf_embed(query)`/`regex_embed(query)` 用的是默认 `dim=512`,而语料向量是主机侧 `embedder.embed()` 产出——当 backend=transformers 时 `dim = len(vec)`(模型隐藏维度,如 768)。`_cosine` 遇到 `len(a) != len(b)` 直接返回 0.0,于是所有 chunk 得分相同,排序退化为语料原始顺序,`answer`/`sources` 看起来完全正常却与查询无关(静默给出错误答案,比报错更危险)。建议在此显式比较 `index["dim"]` 与 `len(qvec)`,不一致时抛错 fail-closed。
-- suggested: dim = int(index.get("dim") or 0)
    if dim and len(qvec) != dim:
        raise RuntimeError(
            "query vector dim %d != index dim %d" % (len(qvec), dim))
    scored = []
    for c in chunks:
        v = c.get("vector") or []
        if len(v) != len(qvec):
            raise RuntimeError("chunk vector dim mismatch")
        scored.append((_cosine(qvec, v), c))

### [app/services/rag_service.py] medium/security located=true
`zipfile.extractall` 风格的任意文件写入:这里只过滤了 `"/" in name` 与 `name.startswith(".")`,但 Python 的 `zipfile.namelist()` 使用 `/` 作为目录分隔符,Windows 反斜杠、`..\\` 等恶意成员名会原样进入 `os.path.join(tmp, name)`,随后被直接 `open(..., "wb")` 写入;更常见的是符号链接成员——`zf.namelist()` 会返回 zip 中的 symlink 条目,若 bundle 内含一个指向宿主敏感文件的 symlink,后续 `open(...).write()` 会跟随链接覆写该文件。bundle 来自 `storage_service`(外部可上传路径),不能当作可信输入。建议改用 `zf.extractall(tmp)` 后再逐个用 `os.path.realpath` 校验仍在 `tmp` 内,或过滤 `os.path.realpath(dest).startswith(os.path.realpath(tmp) + os.sep)` 并跳过非普通文件。
-- suggested: 

### [app/services/rag_service.py] medium/bug located=true
`_sha256_of_dir` 用 `os.listdir` 只哈希顶层普通文件,而 `_package_transformers_model` 用 `sorted(os.listdir(tmp))` 打包全部条目:若 `save_pretrained` 产出子目录(如分片权重目录、附加配置子目录),主机侧 `open(dir, "rb")` 会抛 `IsADirectoryError`,沙箱侧同样会抛 `IsADirectoryError`,两侧都只会得到一句无上下文的堆栈。建议显式跳过非 `os.path.isfile` 条目(或只处理文件),并在两侧都抛出带文件名的明确错误,避免"哈希不一致"被误判成篡改。
-- suggested: def _sha256_of_dir(path):
    import hashlib
    h = hashlib.sha256()
    for p in sorted(os.listdir(path)):
        full = os.path.join(path, p)
        if not os.path.isfile(full):
            continue
        with open(full, "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()

### [app/utils/watermark.py] medium/bug located=true
`bits_per_channel` 完全未做范围校验,但 docstring 声明支持 1-4:
- `bits_per_channel=0`:`capacity` 为 0,报错信息变成误导性的 “Watermark too large”,掩盖了真实原因;
- 负值:`0xFF << bits_per_channel` 抛 `ValueError: negative shift count`;
- `>=8`:`mask` 变成 0(清空整个通道),且 `val` 可达 `2**bpc-1 > 255`,写回像素后 `img.save()` 抛异常,或静默破坏图像内容。
建议在函数入口显式校验并给出清晰错误。
-- suggested: if not 1 <= bits_per_channel <= 4:
        raise ValueError(f"bits_per_channel must be in 1-4, got {bits_per_channel}")

    mask = (0xFF << bits_per_channel) & 0xFF  # e.g. 0xFE for 1 bit

### [app/utils/watermark.py] medium/bug located=true
**未捕获的 struct.error(崩溃)**:`length_bits` 的采集以“通道”为单位,每轮固定追加 `bits_per_channel` 个 bit,而 32 并不是所有取值都能整除的。当 `bits_per_channel=3`(docstring 明确声明支持 1-4)时,第 11 个通道会一次追加 3 bit,使 `len(length_bits) == 33`,`_bytes_from_bits` 返回 5 字节,`struct.unpack(">I", ...)` 抛出未捕获的 `struct.error` 直接让调用方崩溃(该 unpack 不在任何 try 中)。应在 unpack 前截断到 32 bit,或改用异常安全写法。
-- suggested: try:
        wm_len = struct.unpack(">I", _bytes_from_bits(length_bits[:32]))[0]
    except struct.error:
        return None

### [app/utils/watermark.py] medium/bug located=true
**payload 比特流错位**:`if bit_idx >= 32` 中的 `bit_idx` 统计的是“通道×bits_per_channel 次追加”的单位数,payload 的绝对起始比特位应为 `32 * bits_per_channel`。只有当 `bits_per_channel` 整除 8(1/2/4)时,从 `bit_idx == 32` 开始追加才恰好对应第 32 个比特;`bits_per_channel=3` 时实际从第 33 位开始,整体错位 1 bit,解出的 watermark 是乱码且随后可能解出垃圾字符串。建议统一收集绝对比特序列后按位置切片,避免与 bpc 耦合。
-- suggested: for b_idx in range(bits_per_channel - 1, -1, -1):
                    all_bits.append((ch >> b_idx) & 1)
                    bit_idx += 1

    # payload 从绝对比特位 32 * bits_per_channel 开始
    start = 32 * bits_per_channel
    all_bits = all_bits[start:start + wm_len * 8]

### [app/utils/watermark.py] medium/bug located=true
**零宽字符过滤导致解码错位,且缺乏完整性校验**:这里把 sentinel 之后的 *所有* 零宽字符无差别收集成比特流。一旦水印文本之后又被追加/拼接了包含零宽字符的内容(U+200D ZWJ 在 emoji 组合序列中极为常见,U+200B/FEFF 也常出现在网页复制文本中),比特流会整体错位;而长度头没有任何魔数或校验和,错位后仍可能解出落在 1..1024 区间、且恰好是合法 UTF-8 的“水印”,从而返回错误结果(假阳性)。建议在 payload 前加入固定 magic + CRC(如 `struct.pack(">4sH", b"WMK1", crc)`)做完整性校验,解码失败时明确返回 None;同时应只解析 sentinel 之后连续的零宽字符前缀,而不是过滤整段文本。
-- suggested: # 只取 sentinel 之后连续的零宽字符,遇到第一个非零宽字符即停止
    zwc_chars = []
    for ch in encoded:
        if ch not in _ZWC_MAP:
            break
        zwc_chars.append(ch)

### [sdk/cds_sdk/client.py] medium/bug located=true
把 `resp.headers` 转成普通 `dict` 会丢掉 httpx `Headers` 的大小写无关查找能力，而消费端是按固定大小写取值的：`list_sessions()` 里 `headers.get("x-next-cursor")`、`CDSApiError` 里 `headers.get("Retry-After") or headers.get("retry-after")`。HTTP/1.1 服务端（Starlette/uvicorn）通常原样回显 `X-Next-Cursor`，此时普通 dict 查不到 → `next_cursor` 恒为 `None`，`iter_sessions()` 会静默只返回第一页（分页数据丢失且无任何报错）；`Retry-After` 同理可能被忽略。建议直接返回 `resp.headers`（本身大小写无关），或在返回前用 `httpx.Headers(...)` 归一化。
-- suggested: return body, resp.headers  # httpx.Headers 大小写无关，勿转成普通 dict

### [sdk/cds_sdk/client.py] medium/bug located=true
keyset 分支直接对 `body` 下标赋值，但 `_request_with_headers` 在 204 或空响应体时返回 `body=None`，且当响应是 JSON 数组时 `body` 是 list，这里会抛 `TypeError`/`IndexError`，调用方拿到的是与接口异常无关的堆栈。另外此分支的“模式判定”条件过弱：`limit` 只在 `page==1 and page_size==20`（默认值）时才生效，若调用方写 `list_sessions(page=3, limit=50)`，会静默走 offset 分支并把 `limit=50` 丢弃（实际仍按 `page_size=20` 翻页），属于难以排查的隐式行为。建议显式区分两种模式（例如用 `cursor is not None or limit is not None` 判定，并对与 `page/page_size` 混用给出明确报错），同时对 `body` 非 dict 做保护。
-- suggested: if not isinstance(body, dict):
                body = {"items": body if isinstance(body, list) else []}
            body["next_cursor"] = headers.get("x-next-cursor")
            return body

### [sdk/cds_sdk/client.py] medium/security located=true
该 POST 把 `code` / `rag_query` 等正文放进 query string（`params=params`）而不是 `json=`。两个实际风险：1) 沙箱脚本体积稍大即超出服务器/网关的 URI 长度上限（通常 8KB），返回 414/431 且难以定位；2) URL 查询串会被接入层 access log、代理日志、监控链路原样记录，等于把用户代码/查询语句明文落到日志里（可能含敏感逻辑或数据）。若后端确为 query 参数，建议在客户端限制大小并提示改用请求体；否则改为 `json=...`。
-- suggested: 

### [sdk/cds_sdk/client.py] medium/bug located=true
重试逻辑对所有方法一视同仁，包括 `POST /sandbox-sessions`、`POST .../snapshots`、`POST .../terminate` 等非幂等写操作。503 常由网关在“后端已处理但未成功回包”时产生，自动重放会产生重复会话/重复快照/重复计费，而客户端又没有 `Idempotency-Key` 之类的幂等保护。建议只对幂等方法（GET/HEAD/DELETE/PUT）自动重试，写操作要么显式附带幂等键，要么把重试交给调用方决定。
-- suggested: 

### [sdk/cds_sdk/stream.py] medium/bug located=true
**握手阶段没有任何超时，`recv_timeout` 对 connect 完全无效**

`ws.settimeout(timeout)` 在 `connect` 之后才生效，握手（TCP 连接 + HTTP Upgrade 请求）期间不受任何约束。当网关/服务端 hang 住（例如 WS 路由未就绪、代理吞掉 Upgrade 请求）时，该调用会长时间甚至无限阻塞，`recv_timeout` 形同虚设，调用方（很可能是 Web 请求线程）被拖死。`websocket-client` 的 `WebSocket.connect` 本身支持 `timeout` 参数，应传入；同时 connect 失败时显式 `close()` 以释放半开 socket。
-- suggested: def _default_ws_open(url: str, timeout: float):
    ws = _import_websocket().WebSocket()
    try:
        ws.connect(url, timeout=timeout)
    except Exception:
        try:
            ws.close()
        except Exception:
            pass
        raise
    ws.settimeout(timeout)
    return ws

### [sdk/cds_sdk/stream.py] medium/bug located=true
**`ws.send` 未被包装，原始 websocket 异常破坏错误契约**

connect 与 recv 都被转成 `CDSError`，唯独发送 `start` 帧这一步裸奔：连接刚建立即被服务端 RST、或写超时，都会抛出 `WebSocketConnectionClosedException` / `WebSocketTimeoutException` 之类的 websocket-client 异常并直接冒泡。而模块文档与 SDK 的错误模型承诺“可选依赖缺失/流失败一律以 `CDSError` 呈现”，只捕获 `CDSError` 的调用方会在这里崩溃。
-- suggested: try:
            ws.send(json.dumps(frame))
        except CDSError:
            raise
        except Exception as exc:
            raise CDSError(502, f"websocket send failed: {exc}") from exc

### [sdk/cds_sdk/stream.py] medium/security located=true
**连接失败消息可能携带含 ticket 的完整 URL，造成凭证泄漏**

`websocket-client` 的握手异常（`WebSocketBadStatusException`、`WebSocketAddressException` 等）message 中常包含请求 URL 与响应头，而本模块的 URL 携带了 ws-ticket。将其原样嵌入 `CDSError.detail` 后，调用方通常会记录该异常（日志/错误上报/APM trace），一次性凭证便落入日志——虽票据 30s 过期，但会扩大重放窗口并违反“凭证不落日志”的基线。建议在消息中脱敏（仅保留 host/path 或直接把 `ticket=` 替换掉），并确保不把完整 URL 写入 detail/url 字段。
-- suggested: raise CDSError(
            502,
            f"websocket connect failed: {redact_ticket(str(exc))}",
        ) from exc

### [sdk/pyproject.toml] medium/maintainability located=true
核心运行时依赖只有下限、没有上限。`httpx` 在 0.x 阶段仍会引入破坏性变更（且规划中的 1.0 会移除/调整部分已弃用 API，如 `app=`/`proxies` 等参数与部分事件、代理相关接口），一旦用户环境中 pip 解析到不兼容版本，整个 SDK 会在 import 或构造 `httpx.Client` 时抛错，而错误信息与 SDK 本身无关，定位成本很高。建议加上与当前已验证通过的上界，例如 `httpx>=0.27,<1.0`；同理可评估 `stream` extra 的 `websocket-client` 是否需要上界。若项目选择不上界，也建议在 CI 中固定一份 lock 文件（如 pip-tools/uv lock）以保证可复现构建。
-- suggested: 

### [app/services/agent_tools.py] low/dead located=true
`logger` 与 `logging` 在本文件中声明后从未被引用（全文无任何 logger 调用），属于死代码。同时这也导致下面的网络校验失败、非法 CIDR 等安全相关分支完全无法被观测。建议要么删掉，要么在 `validate_network_target` 拒绝路径和 `_ip_allowed` 的配置错误分支中实际使用（配合 egress 审计）。
-- suggested: 

### [app/services/agent_tools.py] low/maintainability located=true
`except ValueError: continue` 会把配置写错的 CIDR（如 `10.0.0.0/8x`、`10.0.0.1/33`）静默丢弃，既无日志也不报错。allowlist 条目拼写错误只会表现为“莫名其妙访问不通”，排障成本很高；若这类函数被复用在 fail-open 的调用点则更危险。建议至少 `logger.warning` 记录被忽略的条目，或在策略编译期就校验 allowlist 合法性并直接拒绝。
-- suggested: 

### [app/services/agent_tools.py] low/maintainability located=true
`register` 对已存在的 id 静默覆盖，且 `tool_registry` 是模块级可变单例：任何运行期注册（测试 fixture、插件式扩展、多 worker 并发导入）都可能把内置平台工具替换掉，而模块文档明确要求“platform-fixed tools only / 用户不得提供工具实现”。建议重复注册显式报错（或提供只 `register` 一次后冻结的 `seal()`），使覆盖行为可感知。
-- suggested: 

### [app/services/agent_tools.py] low/maintainability located=true
`frozen=True` 只做到浅不可变：`params_schema`（dict）与 `network_targets`（list）仍是可变对象，调用方拿到 `tool.params_schema` 后原地修改（例如补 `required`）会污染全局注册表里所有后续 `validate_args`；同时这两个字段使 `AgentTool` 实例不可哈希（无法放入 set/dict key）。建议内部以 `MappingProxyType`/tuple 固化，或明确注释 schema 为“只读、禁止就地修改”。
-- suggested: 

### [app/services/memorization_check.py] low/bug located=true
`ngram_size`/`memorization_threshold` 完全未校验。若 `ngram_size <= 0`，本文件的 `total_ngrams = max(0, len(words) - n + 1)` 会大于实际词数（n=0 时统计毫无意义，n 为负数时更离谱），而 `mia_probe` 侧 `words[i:i+n]` 会产生空/异常元组；若 `memorization_threshold` 被配成 0 或负值，任何非空文本都会被判为 `is_memorized=True`（`0.0 >= 0.0`），配上硬编码的 risk_level="safe" 会直接产生自相矛盾的结果。建议在构造时做显式校验。
-- suggested: if ngram_size < 1:
            raise ValueError("ngram_size must be >= 1")
        if not 0.0 < memorization_threshold <= 1.0:
            raise ValueError("memorization_threshold must be in (0, 1]")
        self.ngram_size = ngram_size
        self.memorization_threshold = memorization_threshold

### [app/services/memorization_check.py] low/maintainability located=true
`field` 与 `MIARiskLevel` 在本文件中均未被引用（`MemorizationResult` 只用了字面量默认值，risk_level 用裸字符串），属于无用导入；同时 risk_level 用魔法字符串而非已有的 `MIARiskLevel` 枚举，与 `mia_probe` 的口径不统一，容易在下游比较时出现 "safe" vs MIARiskLevel.SAFE 的错配。
-- suggested: from dataclasses import dataclass

from app.services.mia_probe import mia_probe, MIARiskLevel

### [app/services/memorization_check.py] low/performance located=true
向 `logger.warning` 传入预先格式化的 f-string，会在日志级别被关闭时仍然完成字符串拼接，失去惰性格式化收益；本文件日志还使用字符串拼接构造多段消息。建议改为 `%s`/`%.3f` 占位符 + 参数形式。
-- suggested: logger.warning(
                "Memorization detected: ratio=%.3f (threshold=%s), "
                "matched=%s/%s n-grams",
                ratio, self.memorization_threshold, matched_ngrams, total_ngrams,
            )

### [app/services/memorization_check.py] low/performance located=true
`check_batch` 对每条生成文本都调用一次 `check`，而 `check_text_memorization` 内部会为 `reference_texts` 中每条文本重新构建 n-gram 集合，整体复杂度为 O(len(generated_texts) × Σlen(ref))，且重复构建同样的集合。参考语料较大且批量检测是常规路径时，这是明显的重复计算热点。建议在 `check_batch` 中预先把 reference 的 n-gram 合并成一个集合（或加缓存）后复用。
-- suggested: # Reuse a single merged reference n-gram set to avoid rebuilding it per text.
        return [self.check(text, reference_texts) for text in generated_texts]

### [app/services/network_policy.py] low/bug located=true
本行日志的第二个字符串字面量不是 f-string，`{config.mode}` 会原样输出，实际排障时看不到真实 mode（而上面审计日志里写的却是真实值，两者不一致容易误导）。建议合并为单个 f-string。同一问题也适用于全文多处 `logger.info(f"...")` / `logger.warning(f"...")`：向 logging 传预先拼接的 f-string 失去了级别未开启时的惰性格式化优势，建议改为 `logger.info("[net-policy] %s: ...", session_id, ...)` 的 %-参数风格。
-- suggested: logger.info("[net-policy] %s: l2c — netns/iptables skipped; "
                        "pushing full-state CubeNetworkConfig (mode=%s)",
                        session_id, config.mode)

### [app/services/network_policy.py] low/maintainability located=true
死代码与“声明未使用”成员集中在几处，容易误导维护者以为能力已存在：1) `import os` 全文未使用；2) `_nft_available`、`_netns_counter` 赋值后从未读取（模块文档宣称支持 nftables 但全文无 nft 调用）；3) `DNSProxyProtocol._pending` / `_query_id_map` 从未被写入或读取（原本应为上游响应匹配预留）；4) `max_bandwidth_bytes_per_second` 全文未参与任何限流实现，而模块文档声称提供“bandwidth control”，调用方会误以为带宽已受限。建议：删除未用项，或补齐实现并在文档中明确“带宽控制未实现”。
-- suggested: 

### [app/services/network_policy.py] low/maintainability located=true
`_TokenBucket.allow` 里“取不到单调时间就退化用 `0.0`”的防御分支反而引入了错误行为：`now` 保持 0.0 时 `elapsed = 0 - self._last_refill <= 0`，令牌永远不回填，一旦突发用尽该会话将**永久被限流**（静默拒绝，无任何日志）。实际上 `import time` / `time.monotonic()` 不会失败，这段 try/except 属于无效防御。建议：模块顶部 `import time`，直接 `now = time.monotonic() if now is None else now`；并考虑用同一桶对 `max_bandwidth_bytes_per_second` 做字节维度限流（当前该字段完全未生效）。
-- suggested: 

### [app/services/opa_client.py] low/maintainability located=true
`health()` 用裸 `except Exception` 吞掉所有异常且不做任何日志输出，直接返回 `False`。健康检查失败通常是配置错误（`OPA_URL` 写错、DNS 解析失败）或 sidecar 未就绪，静默返回 False 会让运维完全看不到根因，而调用方（PDP）此时会静默切到 Python fallback，属于不可观测的降级。至少应 `logger.warning/debug` 记录异常。
-- suggested: except Exception as e:
            logger.warning("OPA health check failed: %s", e)
            return False

### [app/services/opa_client.py] low/performance located=true
日志使用 f-string 会无条件完成字符串格式化，绕过了 logging 的惰性求值（级别关闭时白白格式化，且 `resp.text` 可能很大）。本文件 4 处日志均建议改为 `logger.info("Policy pushed to OPA: %s", path)` 这类占位符形式；同时 `resp.text` 建议截断（如 `resp.text[:500]`）避免把大响应体写入日志。
-- suggested: logger.info("Policy pushed to OPA: %s", path)

### [app/services/rag_service.py] low/bug located=true
`_package_transformers_model` 里 `tempfile.mkdtemp(prefix="cds-emb-pkg-")` 创建的临时目录从不删除,而该目录内含完整模型权重(数百 MB);打包调用每次都会新增一份,长期运行会耗尽宿主磁盘。建议用 `try/finally` + `shutil.rmtree(tmp, ignore_errors=True)`(或 `TemporaryDirectory`)。
-- suggested: tmp = tempfile.mkdtemp(prefix="cds-emb-pkg-")
    try:
        model.save_pretrained(tmp)
        tokenizer.save_pretrained(tmp)
        # ... 打包逻辑 ...
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

### [app/services/rag_service.py] low/bug located=true
这里先 `target.write_bytes(index.to_json())` 写入明文,再"就地加密"。一旦 `encrypt_workspace_file` 是写临时文件后 rename(或中途抛异常),明文 `input/rag_index.json` 会残留在沙箱工作区并随会话目录持久化——这正是函数注释声称要避免的"plaintext at rest"。建议由加密函数直接接收明文字节并原子写出密文(`encrypt_to_file(target, data, dek)`),异常路径上显式 `target.unlink(missing_ok=True)` 清除明文。
-- suggested: 

### [app/services/rag_service.py] low/bug located=true
`_extract_rag_metrics` 取输出最后一行做 `json.loads`,但 runner 内部 `torch` / `transformers` / `onnxruntime` 常会向 stdout/stderr 写进度条或告警日志,最后一行往往不是结果 JSON;此时返回 `{}`,计量字段(`retrieved_chunks`、`answer_mode`、`embedding_engine` 等)全部静默丢失。建议从后往前扫描可解析且含 `rag_meta` 的行,或改用分隔符(如 `CDS_RESULT: {...}`)定位结果行。
-- suggested: for line in reversed([ln for ln in output.strip().splitlines() if ln.strip()]):
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, dict) and "rag_meta" in data:
                break
        else:
            return {}

### [app/services/rag_service.py] low/bug located=true
`retrieved.sort` 之后没有任何相似度阈值,`build_answer` 会把 top_k 段文本无条件拼接为 `answer`;当查询与语料完全无关时,runner 仍会返回一个看似有据可依的抽取式答案(只有 `embedding_engine` 是诚实标注的)。建议保留最小分数阈值(或在 `rag_meta` 中输出 `max_score`/`below_threshold`)让调用方能够识别"语料中无相关内容"。
-- suggested: 

### [app/utils/watermark.py] low/maintainability located=true
**未使用的导入 / 变量(dead code)**:`from PIL import Image` 在本函数中从未被引用(实际全部通过 `_load_image_bytes` 返回的实例操作),`fmt` 解包后也从未被读取(`_load_image_bytes` 返回的 format 在两个调用点都被丢弃)。建议删除导入,并把 `_load_image_bytes` 的返回值简化为只返回 Image,或让调用方真正使用 fmt(目前 embed 无论源格式为何都强制存成 PNG)。
-- suggested: if isinstance(image_bytes, str):
        image_bytes = image_bytes.encode()

    img, fmt = _load_image_bytes(image_bytes)
    img = img.convert("RGBA")
    pixels = img.load()
    width, height = img.size

    # Prepare watermark payload: 4-byte length prefix + data

### [app/utils/watermark.py] low/security located=true
**验证强度被削弱**:`expected in extracted` 是子串包含而非等值比较。由于长度头只声明字节数、payload 没有任何校验,任何解出的更长或含尾部噪声的字符串只要包含 16 位 hex 即判定通过,降低了水印验证的可靠性。建议 `extracted == expected`。
-- suggested: extracted = extract_text_watermark(text)
    return extracted == expected

### [app/utils/watermark.py] low/security located=true
**域分隔歧义**:`user_id` 与 `session_id` 直接用 `:` 拼接且未做转义/校验,`("a:b", "c")` 与 `("a", "b:c")` 会生成完全相同的水印。在泄露溯源场景中,这构成归属混淆:构造含 `:` 的 `user_id` 即可生成与他人相同的水印标记。建议对两个字段做字符集/长度校验,或使用带长度前缀的编码(`struct.pack(">I", len(uid)) + ...`)做域分隔。此外该水印是对公开可知的 ID 做无密钥 MD5 截断,任何知道 `user_id`/`session_id` 的人都能伪造或枚举,若目标是抗伪造溯源,建议改用带服务端密钥的 HMAC-SHA256。
-- suggested: payload = struct.pack(">I", len(user_id)) + user_id.encode("utf-8") + session_id.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]

### [app/utils/watermark.py] low/maintainability located=true
**未使用的导入**:`typing.Literal` 在本文件中从未被引用。
-- suggested: 

### [sdk/cds_sdk/client.py] low/bug located=true
RFC 9110 规定 `Retry-After` 既可以是秒数也可以是 HTTP-date（如 `Wed, 21 Oct 2026 07:28:00 GMT`）。这里 `float(ra)` 解析日期失败后静默回落到 1.0，客户端会在服务端明确要求长时间等待时仍以 1s 间隔猛重试（放大 429/503 压力）。建议用 `email.utils.parsedate_to_datetime` 兜底解析日期并换算为剩余秒数。另外 `CDSApiError.__init__` 里又实现了一份几乎相同的 Retry-After 解析逻辑，两处行为还不一致（一个 `int()`、一个 `float()`），建议抽成单一函数复用。
-- suggested: 

### [sdk/cds_sdk/client.py] low/performance located=true
上传路径把整个文件一次性 `read()` 进内存再交给 httpx（`bytes(data)` 对 bytes/bytearray 还会再复制一份），大文件场景内存占用翻倍，且失去 httpx 分块流式上传能力。httpx 的 `files=` 直接支持文件对象/生成器，建议对文件路径与 BinaryIO 直接把句柄透传给 `files={"file": (filename, fh)}`，仅在需要 `Content-Length` 时提供 `content_type`/size。
-- suggested: 

### [sdk/cds_sdk/stream.py] low/bug located=true
**空帧即终止迭代的判断过于宽泛**

`if not raw: return` 把“空消息”与“连接关闭”混为一谈。空文本帧在 WebSocket 协议里是合法帧（常被用作应用层 keepalive/心跳），一旦服务端发出这类帧，流会被提前判定结束，剩余 stdout/stderr 全部丢失且不会看到 `exit` 帧。websocket-client 在连接真正关闭时会抛 `WebSocketConnectionClosedException`，因此这里更应只把 `None`（测试钩子表达 EOF 的约定）视为结束，空串交给上层 JSON 解析分支处理。
-- suggested: if raw is None:  # test-hook convention for EOF
                return

### [sdk/cds_sdk/stream.py] low/maintainability located=true
**依赖 client 私有属性拼 WS 地址，且能力校验在本函数内缺位**

两点问题：1) `client._client.base_url` 直接读取 `httpx.Client` 私有属性，`CDSClient` 并未暴露 base_url 公共访问器，任何内部重构（字段改名、改用 async client、base_url 归一化差异）都会在运行时才以 `AttributeError` 暴露；2) `SUPPORTED_STREAM_LEVELS` 与文档中“fails fast before any socket is opened”的承诺在本函数里并未落实——目前只靠 `CDSClient.stream_exec` 上层校验，而本函数是模块级公开入口，直接调用（含测试与非 `CDSClient` 的鸭子类型调用方）会绕过 fail-fast，先发出 ws-ticket 再收到服务端的 `STREAM_UNSUPPORTED`，既多消耗了一次性票据也违背文档语义。建议给 `CDSClient` 增加公开 `base_url` 属性，并在此处对能力做一次显式校验。
-- suggested: url = _to_ws_url(str(client.base_url), path)

### [sdk/pyproject.toml] low/maintainability located=true
两点与打包内容相关的风险：1) 当前 `cds_sdk/` 为扁平布局（`__init__.py`/`client.py`/`stream.py`），`cds_sdk*` 可以覆盖，但一旦后续新增不带 `cds_sdk` 前缀的目录（例如把公共类型抽到同级 `common/`），会被静默漏打包，只有在安装后的环境里才暴露为 ImportError。建议同时显式声明 `packages = ["cds_sdk"]` 或让 CI 用 `python -m build` + 在干净 venv 中 `pip install dist/*.whl` 做一次安装冒烟测试来兜底。2) `cds_sdk/__init__.py` 对外宣称是 typed sync client，但包内没有 `py.typed` 标记文件，也没有 `package-data` 配置，安装后 mypy/pyright 等类型检查器不会采用 SDK 的类型标注，等于放弃了类型声明的价值。若希望类型对外生效，需新增 `sdk/cds_sdk/py.typed` 并配置 `[tool.setuptools.package-data] cds_sdk = ["py.typed"]`（配合 `include-package-data = true`）。
-- suggested: 

### [app/services/jupyter_gateway.py] / located=false

-- suggested: 

### [app/services/network_policy.py] /maintainability located=true
`original_offset` 计算后从未被读取（死变量）。如果原本意图是指针跳转后用于定位问题段结束位置（以便后续重写报文），则该逻辑尚未实现；否则应删除。另外循环退出条件 `while offset < len(data) and max_jumps > 0`：当 `max_jumps` 耗尽时函数会返回一个**不完整的域名**，在域名白名单场景下这是不可接受的——截断后的域名可能匹配上错误的 allowlist 条目（造成误放行）。建议：遇到跳转环限时抛/返回 None，由调用方回 SERVFAIL（fail-closed）。
-- suggested: parts = []
        jumped = False
        max_jumps = 10  # Prevent infinite loops

### [app/services/network_policy.py] /performance located=true
`_get_system_dns()` 会在每个 DNS 查询处理路径上同步打开并逐行读 `/etc/resolv.conf`（`__init__` 仅在启动代理时调用一次，但 `_UpstreamDNSProtocol` 每查询都新建一个协议对象，实际上游地址已缓存，这里只是提醒：`__init__` 运行在事件循环线程上，文件读取属于同步 IO）。建议：将系统 DNS 结果在模块级惰性缓存一次（带 TTL），避免在事件循环内重复同步读文件；同时当前实现对“多个 nameserver”只取第一个，上游不可用时也没有备用服务器兼活探测，域名解析整体可用性单点。
-- suggested: 

### [app/services/rag_service.py] /bug located=true
生成式回答完全忽略了用户查询:`_generative_answer(context_text)` 只把检索到的 top-k chunk 拼成 context,内部对 `context_text` 做 `tf_embed` 后取 `argmax(logits)`,没有任何地方使用 `_RAG_QUERY`。结果是在 `answer_mode="generative"` 下,不同查询只要命中相同 chunk 就会返回完全相同的答案,"检索-问答"退化为"语料标签查表";同时 context 拼接也没有像 `build_answer` 那样做 `max_chars` 截断,长语料会把无上限文本送入嵌入计算。建议把 query 一并参与模型输入,并对 context 做长度上限。
-- suggested: def _generative_answer(query, context_text):

### [app/services/rag_service.py] /bug located=true
main() 里调用生成式回答时只传入拼接后的 context,没有传 `_RAG_QUERY`,导致答案与用户问题无关(见 `_generative_answer` 的说明)。同时这里也未对 `parts` 做长度限制。
-- suggested: answer = _generative_answer(query, "\\n\\n".join(parts)[:MAX_CONTEXT_CHARS])

### [app/services/rag_service.py] /bug located=true
`_transformers_embed` 里 `_load_embedding_model_dir()` 被调用了两次:一次为了算哈希、一次为了 `from_pretrained`,而该函数每次都会新建一个 `mkdtemp` 目录并把整个 zip 重新解压一遍;`AutoTokenizer/AutoModel.from_pretrained` 也没有缓存,每次 `_embed_query` 都会重新加载模型权重(数百 MB + 秒级开销),对多 chunk 查询是数量级的性能浪费。另外这两处新建的临时目录从不清理,反复调用会耗尽沙箱 /tmp。建议在模块级做一次性惰性缓存(`_EMBED_MODEL` sentinel),解压/加载一次后复用。
-- suggested: model_dir = _get_model_dir()  # 惰性、只解压一次并缓存
    if _EMBEDDING_MODEL_HASH:
        actual = _sha256_of_dir(model_dir)
        if actual != _EMBEDDING_MODEL_HASH:
            raise RuntimeError("embedding model hash mismatch in sandbox")

### [app/services/rag_service.py] /bug located=true
截断分支存在 off-by-one:`remaining = max_chars - total`,然后追加 `piece[:remaining] + "…"` 并令 `total += remaining + 1`,实际字符数变为 `total + 1 > max_chars`,违反本函数声明的 `max_chars` 上限(与 `MAX_ANSWER_CHARS` 的语义不一致,下游按 8000 做的存储/展示限制会被突破)。建议用 `piece[:max(0, remaining - 1)] + "…"`。
-- suggested: remaining = max_chars - total
            if remaining > 40:
                parts.append(piece[:remaining - 1] + "…")
                total += remaining

### [app/services/rag_service.py] /bug located=true
`_load_bytes` 用裸 `open(...).read()` 读取工作区文件,既未在 `with` 中管理句柄(异常路径会泄漏 fd),也假定文件一定存在——runner 在缺失 `input/rag_index.json` 时会抛出裸 `FileNotFoundError`,而不是注释中承诺的"clear missing-index error"。建议 `with open(path, "rb") as fh:` 并在文件缺失时抛出带路径说明的 RuntimeError。
-- suggested: def _load_bytes(path):
    if not os.path.exists(path):
        raise RuntimeError("required RAG input file missing in workspace: %s" % path)
    with open(path, "rb") as fh:
        raw = fh.read()

### [app/services/rag_service.py] /maintainability located=true
本模块的多个顶层导入/定义从未被引用:`RAGEmbeddingEngine`、`regex_embed`、`tf_embed` 只在导入行出现(真正的嵌入实现由 `RAGEmbedder` 与生成的 runner 模板使用);`_is_cjk_char` 定义了却从未调用(`chunk_text` 实际按字符数计数,并未使用 CJK 判断),属于死代码,且容易让读者误以为 chunking 做了 CJK 感知切分。建议删除这些未使用的导入与函数,或让 `chunk_text` 真正使用 CJK 度量。
-- suggested: 

### [app/services/rag_service.py] /bug located=true
这里的 `ship_transformers=(index.engine == "transformers")` 使表达式恒等于 `engine == "transformers"`,于是 `_ensure_runner_replicable_engine` 中的 transformers fail-closed 分支在本函数里永远不可达(死代码),即使语料索引声明 engine=transformers 但未随附模型包也能通过校验。建议直接校验 `index.embedding_model_hash` 与 bundle 是否就绪,而不是用恒真条件跳过检查。
-- suggested: engine = _ensure_runner_replicable_engine(index.engine, what="retrieval")
    if engine == "transformers" and not index.embedding_model_hash:
        raise ValueError(
            "retrieval: transformers corpus is missing embedding_model_hash; "
            "re-ingest the corpus so the model bundle is available"
        )
